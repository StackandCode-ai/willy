"""
Accounts: Google sign-in, per-account spaces, device keys and pairing.

Google itself is not called: the Firebase verifier is replaced by a fake that turns
"google:<email>" into verified claims.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

import server.app as hub
from server.accounts import Accounts, AuthError, HOME_SPACE


def _claims(email):
    return {"sub": "sub-" + email, "email": email, "email_verified": True, "name": email.split("@")[0],
            "iss": "https://securetoken.google.com/test"}


@pytest.fixture()
def fresh_accounts(tmp_path, monkeypatch):
    """A clean accounts database for each test, with fake Google sign-in."""
    acc = Accounts(url=f"sqlite:///{(tmp_path / 'acc.db').as_posix()}", project_id="test")

    def fake_verify(id_token):
        if not id_token.startswith("google:"):
            raise AuthError("bad token")
        return _claims(id_token.split(":", 1)[1])

    monkeypatch.setattr(acc.firebase, "verify", fake_verify)
    monkeypatch.setattr(hub, "accounts", acc)
    hub._owner_cache[:] = [0.0, None]
    monkeypatch.setenv("WILLY_OWNER_EMAIL", "owner@example.com")
    monkeypatch.setenv("WILLY_SIGNUP", "open")
    yield acc
    hub._owner_cache[:] = [0.0, None]


def _sign_in(client, email):
    res = client.post("/api/v1/auth/google", json={"id_token": f"google:{email}"}).json()
    assert res["success"], res
    return res["token"]


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_owner_gets_home_space_and_others_get_their_own(fresh_accounts):
    other = fresh_accounts.sign_in_claims(_claims("friend@example.com"))
    owner = fresh_accounts.sign_in_claims(_claims("owner@example.com"))
    assert owner["user"]["owner"] and owner["user"]["role"] == "admin"
    assert not other["user"]["owner"] and other["user"]["role"] == "user"
    p_owner = fresh_accounts.resolve(owner["token"])
    p_other = fresh_accounts.resolve(other["token"])
    assert p_owner.space_id == HOME_SPACE
    assert p_other.space_id not in (HOME_SPACE, "") and p_other.space_id == p_other.user_id


def test_closed_signup_refuses_strangers(fresh_accounts, monkeypatch):
    monkeypatch.setenv("WILLY_SIGNUP", "closed")
    with pytest.raises(AuthError):
        fresh_accounts.sign_in_claims(_claims("stranger@example.com"))
    monkeypatch.setenv("WILLY_ALLOWED_EMAILS", "family@example.com")
    assert fresh_accounts.sign_in_claims(_claims("family@example.com"))["token"].startswith("wses_")


def test_nobody_wins_ownership_by_being_first(fresh_accounts, monkeypatch):
    monkeypatch.delenv("WILLY_OWNER_EMAIL")
    first = fresh_accounts.sign_in_claims(_claims("stranger@example.com"))
    assert not first["user"]["owner"] and fresh_accounts.home_owner() is None
    monkeypatch.setenv("WILLY_OWNER_EMAIL", "owner@example.com")
    assert fresh_accounts.sign_in_claims(_claims("owner@example.com"))["user"]["owner"]


def test_tokens_are_stored_hashed(fresh_accounts):
    res = fresh_accounts.sign_in_claims(_claims("owner@example.com"))
    with fresh_accounts.engine.connect() as c:
        stored = [r[0] for r in c.exec_driver_sql("select token_hash from willy_sessions")]
    assert res["token"] not in stored and len(stored[0]) == 64


def test_spaces_are_isolated_over_the_api(fresh_accounts):
    client = TestClient(hub.app)
    owner = _sign_in(client, "owner@example.com")
    friend = _sign_in(client, "friend@example.com")

    me = client.get("/api/v1/auth/me", headers=_bearer(friend)).json()["account"]
    assert me["email"] == "friend@example.com" and not me["owner"]

    # A reminder made by the friend is theirs alone.
    text = f"water plants {uuid.uuid4().hex[:6]}"
    assert client.post("/api/v1/reminders", json={"text": text, "time": "18:00"}, headers=_bearer(friend)).json()["success"]
    friend_rems = client.get("/api/v1/reminders", headers=_bearer(friend)).json()["reminders"]
    owner_rems = client.get("/api/v1/reminders", headers=_bearer(owner)).json()["reminders"]
    hub_rems = client.get("/api/v1/reminders", headers=_bearer(hub._expected_token())).json()["reminders"]
    assert any(r["text"] == text for r in friend_rems)
    assert not any(r["text"] == text for r in owner_rems + hub_rems)

    # The hub's own server and the account list are the owner's only.
    assert client.get("/api/v1/server/status", headers=_bearer(friend)).status_code == 403
    assert client.get("/api/v1/admin/users", headers=_bearer(friend)).status_code == 403
    users = client.get("/api/v1/admin/users", headers=_bearer(owner)).json()["users"]
    assert {u["email"] for u in users} == {"owner@example.com", "friend@example.com"}
    assert all("google_sub" not in u for u in users)


def test_pairing_gives_a_device_its_own_key_in_the_right_space(fresh_accounts):
    client = TestClient(hub.app)
    friend = _sign_in(client, "friend@example.com")
    device_id = f"pc_friend_{uuid.uuid4().hex[:6]}"

    start = client.post("/api/v1/pair/start", json={"device_id": device_id, "device_type": "pc", "name": "Friend PC"}).json()
    assert start["success"] and "/pair?code=" in start["verify_url"]
    poll = {"code": start["code"], "poll_secret": start["poll_secret"]}
    assert client.post("/api/v1/pair/poll", json=poll).json()["status"] == "pending"
    assert client.post("/api/v1/pair/poll", json={**poll, "poll_secret": "wrong"}).status_code == 404

    info = client.get(f"/api/v1/pair/info?code={start['code'].lower()}", headers=_bearer(friend)).json()
    assert info["name"] == "Friend PC" and not info["approved"]
    assert client.post("/api/v1/pair/approve", json={"code": start["code"]}, headers=_bearer(friend)).json()["success"]

    got = client.post("/api/v1/pair/poll", json=poll).json()
    assert got["status"] == "approved" and got["device_key"].startswith("wdev_") and got["email"] == "friend@example.com"
    assert client.post("/api/v1/pair/poll", json=poll).status_code == 410  # the key is handed out once

    key = got["device_key"]
    # The key connects as that device (whatever id it claims) into the friend's space only.
    with client.websocket_connect(f"/ws/devices?token={key}&device_id=spoofed&device_type=pc&name=Friend%20PC") as ws:
        ws.receive_json()
        friend_devices = {d["device_id"] for d in client.get("/api/v1/devices", headers=_bearer(friend)).json()["devices"]}
        owner_devices = {d["device_id"] for d in client.get("/api/v1/devices", headers=_bearer(hub._expected_token())).json()["devices"]}
        assert device_id in friend_devices and "spoofed" not in friend_devices
        assert device_id not in owner_devices

    # Removing the device kills its key.
    assert client.delete(f"/api/v1/account/devices/{device_id}", headers=_bearer(friend)).json()["revoked"] == 1
    assert client.get("/api/v1/auth/me", headers=_bearer(key)).status_code == 401


def test_link_code_and_phone_device_key(fresh_accounts):
    client = TestClient(hub.app)
    friend = _sign_in(client, "friend@example.com")
    link = client.post("/api/v1/pair/link", headers=_bearer(friend)).json()
    red = client.post("/api/v1/pair/redeem", json={"code": link["code"], "device_id": "mobile_abc", "device_type": "mobile"}).json()
    assert red["success"] and red["device_key"].startswith("wdev_")
    assert client.post("/api/v1/pair/redeem", json={"code": link["code"], "device_id": "x"}).status_code == 404

    # The phone app signs in with Google itself and trades the session for a device key.
    res = client.post("/api/v1/auth/device-key", json={"device_id": "mobile_def", "device_type": "mobile", "name": "Pixel"},
                      headers=_bearer(friend)).json()
    me = client.get("/api/v1/auth/me", headers=_bearer(res["device_key"])).json()["account"]
    assert me["email"] == "friend@example.com" and me["via"] == "device_key"
    # ...but a device key can't mint more keys.
    assert client.post("/api/v1/auth/device-key", json={"device_id": "y"}, headers=_bearer(res["device_key"])).status_code == 403


def test_blocked_account_is_signed_out_everywhere(fresh_accounts):
    client = TestClient(hub.app)
    owner = _sign_in(client, "owner@example.com")
    friend = _sign_in(client, "friend@example.com")
    uid = next(u["id"] for u in client.get("/api/v1/admin/users", headers=_bearer(owner)).json()["users"]
               if u["email"] == "friend@example.com")
    assert client.post(f"/api/v1/admin/users/{uid}/block", headers=_bearer(owner)).json()["success"]
    assert client.get("/api/v1/auth/me", headers=_bearer(friend)).status_code == 401
    assert client.post("/api/v1/auth/google", json={"id_token": "google:friend@example.com"}).status_code == 403
    # The owner can't block themselves.
    owner_id = next(u["id"] for u in client.get("/api/v1/admin/users", headers=_bearer(owner)).json()["users"]
                    if u["email"] == "owner@example.com")
    assert not client.post(f"/api/v1/admin/users/{owner_id}/block", headers=_bearer(owner)).json()["success"]


def test_sockets_need_a_valid_token(fresh_accounts):
    client = TestClient(hub.app)
    with client.websocket_connect("/ws/events?token=wses_nope") as ws:
        with pytest.raises(Exception):
            ws.receive_json()
    assert client.get("/api/v1/devices", headers=_bearer("wdev_nope")).status_code == 401


def test_public_endpoints_leak_nothing(fresh_accounts):
    client = TestClient(hub.app)
    health = client.get("/health").json()
    assert "devices" not in health
    cfg = client.get("/api/v1/auth/config").json()
    assert set(cfg) >= {"google", "signup"}
    assert "Add a device to Willy" in client.get("/pair").text


def test_hub_without_google_still_pairs_devices_and_owner_can_take_over(fresh_accounts, monkeypatch):
    client = TestClient(hub.app)
    owner_token = hub._expected_token()
    # No Google account has signed in: the hub token still owns the home space and can pair devices.
    link = client.post("/api/v1/pair/link", headers=_bearer(owner_token)).json()
    assert link["success"]
    red = client.post("/api/v1/pair/redeem", json={"code": link["code"], "device_id": "mobile_local", "device_type": "mobile"}).json()
    key = red["device_key"]
    me = client.get("/api/v1/auth/me", headers=_bearer(key)).json()["account"]
    assert me["owner"] and me["email"] == "owner@local.willy"

    # Later the configured owner signs in with Google: same account, devices stay paired.
    res = fresh_accounts.sign_in_claims(_claims("owner@example.com"))
    assert res["user"]["owner"] and res["user"]["email"] == "owner@example.com"
    fresh_accounts._cache.clear()
    me = client.get("/api/v1/auth/me", headers=_bearer(key)).json()["account"]
    assert me["owner"] and me["email"] == "owner@example.com"


def test_ai_provider_can_be_chosen_tested_and_saved(fresh_accounts, monkeypatch, tmp_path):
    from server import ai_config

    monkeypatch.setattr(ai_config, "AI_FILE", tmp_path / "ai.json")
    client = TestClient(hub.app)
    owner = hub._expected_token()
    friend = _sign_in(client, "friend@example.com")
    assert client.get("/api/v1/ai", headers=_bearer(friend)).status_code == 403  # owner only

    view = client.get("/api/v1/ai", headers=_bearer(owner)).json()
    assert {p["id"] for p in view["providers"]} >= {"groq", "gemini", "openai", "openrouter", "custom"}

    async def fake_test(cfg):
        return {"ok": cfg.api_key == "good-key-1234", "error": "That API key was rejected."}

    monkeypatch.setattr(ai_config, "test", fake_test)
    bad = client.post("/api/v1/ai", json={"provider": "gemini", "api_key": "nope"}, headers=_bearer(owner))
    assert bad.status_code == 422 and "rejected" in bad.json()["error"]
    assert not (tmp_path / "ai.json").exists()  # nothing saved for a key that failed

    ok = client.post("/api/v1/ai", json={"provider": "gemini", "api_key": "good-key-1234"}, headers=_bearer(owner)).json()
    assert ok["success"] and ok["provider"] == "gemini" and ok["key"] == "…1234" and ok["ready"]
    assert "good-key-1234" not in client.get("/api/v1/ai", headers=_bearer(owner)).text  # keys never come back
    cfg = ai_config.current()
    assert (cfg.provider, cfg.api_key, cfg.model) == ("gemini", "good-key-1234", "gemini-2.5-flash")
    assert "generativelanguage" in cfg.base_url
    assert hub.orchestrator.provider == "gemini" and hub.orchestrator.client is not None  # reloaded live

    custom = client.post("/api/v1/ai", json={"provider": "custom", "base_url": "http://localhost:11434/v1", "model": "llama3.1",
                                              "skip_test": True}, headers=_bearer(owner)).json()
    assert custom["ready"] and ai_config.current().base_url == "http://localhost:11434/v1"


def test_email_accounts_register_login_and_are_rate_limited(fresh_accounts):
    client = TestClient(hub.app)
    reg = client.post("/api/v1/auth/register", json={"email": "Sam@Example.com", "password": "a long enough pass", "name": "Sam"}).json()
    assert reg["success"] and reg["user"]["email"] == "sam@example.com" and not reg["user"]["owner"]
    assert client.post("/api/v1/auth/register", json={"email": "sam@example.com", "password": "another long pass"}).status_code == 409
    assert client.post("/api/v1/auth/register", json={"email": "x@example.com", "password": "short"}).status_code == 400
    assert client.post("/api/v1/auth/register", json={"email": "not-an-email", "password": "a long enough pass"}).status_code == 400
    # An address the owner is configured with can't be claimed with a password.
    assert client.post("/api/v1/auth/register", json={"email": "owner@example.com", "password": "a long enough pass"}).status_code == 403

    login = client.post("/api/v1/auth/login", json={"email": "sam@example.com", "password": "a long enough pass"}).json()
    assert login["success"] and client.get("/api/v1/auth/me", headers=_bearer(login["token"])).json()["account"]["email"] == "sam@example.com"
    assert client.post("/api/v1/auth/login", json={"email": "sam@example.com", "password": "wrong password!"}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"email": "nobody@example.com", "password": "whatever goes here"}).status_code == 401

    for _ in range(8):  # lock-out after repeated misses on one address
        client.post("/api/v1/auth/login", json={"email": "sam@example.com", "password": "guess guess guess"})
    assert client.post("/api/v1/auth/login", json={"email": "sam@example.com", "password": "a long enough pass"}).status_code == 429


def test_closed_hub_refuses_email_signup(fresh_accounts, monkeypatch):
    monkeypatch.setenv("WILLY_SIGNUP", "closed")
    client = TestClient(hub.app)
    assert client.post("/api/v1/auth/register", json={"email": "sam@example.com", "password": "a long enough pass"}).status_code == 403
    monkeypatch.setenv("WILLY_ALLOWED_EMAILS", "sam@example.com")
    assert client.post("/api/v1/auth/register", json={"email": "sam@example.com", "password": "a long enough pass"}).json()["success"]


def test_google_sign_in_cancels_a_password_someone_set_for_that_email_first(fresh_accounts):
    """Attacker registers victim's address with a password; when the victim later signs in with Google the
    attacker's password, sessions and paired devices stop working."""
    client = TestClient(hub.app)
    evil = client.post("/api/v1/auth/register", json={"email": "victim@example.com", "password": "attacker chosen pass"}).json()["token"]
    key = client.post("/api/v1/auth/device-key", json={"device_id": "evil_phone", "device_type": "mobile"}, headers=_bearer(evil)).json()["device_key"]
    assert client.get("/api/v1/auth/me", headers=_bearer(key)).status_code == 200

    fresh_accounts.sign_in_claims(_claims("victim@example.com"))  # the real person arrives with Google
    fresh_accounts._cache.clear()
    assert client.post("/api/v1/auth/login", json={"email": "victim@example.com", "password": "attacker chosen pass"}).status_code == 401
    assert client.get("/api/v1/auth/me", headers=_bearer(evil)).status_code == 401
    assert client.get("/api/v1/auth/me", headers=_bearer(key)).status_code == 401


def test_owner_can_add_an_email_login_to_the_owner_account(fresh_accounts):
    client = TestClient(hub.app)
    owner_token = hub._expected_token()
    r = client.post("/api/v1/account/password", json={"password": "owner long password", "email": "boss@example.com"},
                    headers=_bearer(owner_token))
    assert r.json()["success"], r.text
    login = client.post("/api/v1/auth/login", json={"email": "boss@example.com", "password": "owner long password"}).json()
    me = client.get("/api/v1/auth/me", headers=_bearer(login["token"])).json()["account"]
    assert me["owner"] and me["email"] == "boss@example.com"
    # Changing it keeps the session that changed it.
    ok = client.post("/api/v1/account/password", json={"password": "new owner password"}, headers=_bearer(login["token"]))
    assert ok.json()["success"] and client.get("/api/v1/auth/me", headers=_bearer(login["token"])).status_code == 200
    # Devices can't change passwords.
    key = client.post("/api/v1/auth/device-key", json={"device_id": "ph"}, headers=_bearer(login["token"])).json()["device_key"]
    assert client.post("/api/v1/account/password", json={"password": "device tries this"}, headers=_bearer(key)).status_code == 403


def test_voice_key_is_stored_without_changing_the_brain(fresh_accounts, monkeypatch, tmp_path):
    from server import ai_config

    monkeypatch.setattr(ai_config, "AI_FILE", tmp_path / "ai.json")
    ai_config.save("gemini", "", "gemini-key-9999")
    client = TestClient(hub.app)
    owner = _bearer(hub._expected_token())

    async def fake_test(cfg):
        return {"ok": cfg.api_key == "gsk_voice_key_12345", "error": "That API key was rejected."}

    monkeypatch.setattr(ai_config, "test", fake_test)
    assert client.post("/api/v1/ai/voice", json={"provider": "groq", "api_key": "wrong"}, headers=owner).status_code == 422
    assert client.post("/api/v1/ai/voice", json={"provider": "gemini", "api_key": "x"}, headers=owner).status_code == 400
    ok = client.post("/api/v1/ai/voice", json={"provider": "groq", "api_key": "gsk_voice_key_12345"}, headers=owner).json()
    assert ok["success"] and ok["provider"] == "gemini"          # still thinking with Gemini
    assert ai_config.key_for("groq") == "gsk_voice_key_12345"    # ...but voice can transcribe with Groq
    assert "gsk_voice_key_12345" not in client.get("/api/v1/ai", headers=owner).text
