"""The master: Google sign-in brokered for other hubs, the registry of sign-ins and hubs, usage counts."""

import pytest
from fastapi.testclient import TestClient

import server.app as hub
from server import broker
from server.accounts import Accounts, AuthError
from server.registry import Registry
from server.test_accounts import _bearer, _claims, _sign_in, fresh_accounts  # noqa: F401  (fixture)

MASTER = "https://master.example/willy"
HUB = "https://myhub.example.com"
STATE = "state-abcdefghij"


@pytest.fixture()
def keys(tmp_path):
    signer = broker.BrokerSigner(tmp_path / "key.pem")
    return signer, broker.JwksCache(lambda url: signer.jwks())


def test_broker_token_is_bound_to_one_hub_and_expires(tmp_path, keys):
    signer, cache = keys
    claims = {"sub": "s1", "email": "pat@example.com", "name": "Pat"}
    tok = signer.issue(MASTER, HUB, claims, STATE)
    ok = broker.verify_token(tok, "x", MASTER, HUB, cache)
    assert ok["email"] == "pat@example.com" and ok["state"] == STATE

    with pytest.raises(AuthError, match="different hub"):
        broker.verify_token(tok, "x", MASTER, "https://evil.example.org", cache)
    with pytest.raises(AuthError, match="different service"):
        broker.verify_token(tok, "x", "https://other-master.example", HUB, cache)

    head, _body, sig = tok.split(".")
    forged_body = broker.b64u(b'{"iss":"%s","aud":"%s","exp":9999999999,"iat":1,"sub":"x","email":"boss@example.com","email_verified":true}'
                              % (MASTER.encode(), HUB.encode()))
    with pytest.raises(AuthError, match="signature"):
        broker.verify_token(f"{head}.{forged_body}.{sig}", "x", MASTER, HUB, cache)

    stranger = broker.BrokerSigner(tmp_path / "other.pem").issue(MASTER, HUB, claims, STATE)
    with pytest.raises(AuthError):
        broker.verify_token(stranger, "x", MASTER, HUB, cache)

    expired = signer.sign({"iss": MASTER, "aud": HUB, "iat": 1, "exp": 2, "sub": "s", "email": "a@b.co", "email_verified": True})
    with pytest.raises(AuthError, match="expired"):
        broker.verify_token(expired, "x", MASTER, HUB, cache)


def test_hub_origin_rules():
    assert broker.clean_origin("https://hub.example.com/") == "https://hub.example.com"
    assert broker.clean_origin("http://192.168.1.5:8000") == "http://192.168.1.5:8000"
    assert broker.clean_origin("http://localhost:8000") == "http://localhost:8000"
    for bad in ("http://hub.example.com", "https://hub.example.com/path", "javascript:alert(1)",
                "https://u:p@hub.example.com", "https://hub.example.com?x=1", "ftp://x.example.com", ""):
        assert broker.clean_origin(bad) is None, bad


def test_a_client_hub_signs_people_in_through_the_master(fresh_accounts, keys, monkeypatch):  # noqa: F811
    """A hub with no Firebase of its own accepts a token the master made for it, and nothing else."""
    signer, cache = keys
    monkeypatch.setattr(hub, "jwks_cache", cache)
    monkeypatch.setenv("WILLY_BROKER_URL", MASTER)
    monkeypatch.setenv("WILLY_PUBLIC_URL", HUB)
    monkeypatch.setenv("WILLY_SIGNUP", "open")
    monkeypatch.delenv("WILLY_MASTER", raising=False)
    monkeypatch.setattr(hub, "_firebase_web_config", lambda: None)
    client = TestClient(hub.app)
    cfg = client.get("/api/v1/auth/config").json()
    assert cfg["google"] is None and cfg["broker"] == {"url": MASTER}

    claims = {"sub": "sub-pat", "email": "pat@example.com", "name": "Pat"}
    good = signer.issue(MASTER, HUB, claims, STATE)
    res = client.post("/api/v1/auth/broker", json={"token": good, "state": STATE}).json()
    assert res["success"] and res["user"]["email"] == "pat@example.com"
    assert client.get("/api/v1/auth/me", headers=_bearer(res["token"])).json()["account"]["email"] == "pat@example.com"

    other_hub = signer.issue(MASTER, "https://another.example.com", claims, STATE)
    assert client.post("/api/v1/auth/broker", json={"token": other_hub, "state": STATE}).status_code == 401
    assert client.post("/api/v1/auth/broker", json={"token": good, "state": "a-different-state"}).status_code == 401


def test_master_issues_tokens_and_keeps_a_registry(fresh_accounts, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setenv("WILLY_MASTER", "1")
    monkeypatch.setenv("WILLY_BROKER_URL", MASTER)
    monkeypatch.setattr(hub, "master_registry", Registry(fresh_accounts.engine))
    monkeypatch.setattr(hub, "broker_signer", broker.BrokerSigner(tmp_path / "master.pem"))
    client = TestClient(hub.app)
    assert client.get("/api/v1/broker/jwks").json()["keys"][0]["crv"] == "Ed25519"
    assert "Continue with Google" in client.get("/broker/login").text

    bad = client.post("/api/v1/broker/token", json={"id_token": "google:pat@example.com", "hub": "http://evil.example.com", "state": STATE})
    assert bad.status_code == 400
    tok = client.post("/api/v1/broker/token", json={"id_token": "google:pat@example.com", "hub": HUB, "state": STATE}).json()["token"]
    claims = broker.verify_token(tok, "x", MASTER, HUB, broker.JwksCache(lambda u: hub.broker_signer.jwks()))
    assert claims["email"] == "pat@example.com"

    ping = {"hub_id": "a" * 32, "origin": HUB, "version": "3.1.0", "platform": "Linux 6", "owner_email": "pat@example.com",
            "accounts": 2, "devices": {"pc": 1, "mobile": 2, "server": 1}, "ai_provider": "gemini",
            "usage": {"2026-10-01": {"commands": 12, "failures": 1, "tools": {"launch_application": 5, "get_weather": 2}}}}
    assert client.post("/api/v1/registry/ping", json=ping).json()["success"]
    assert not client.post("/api/v1/registry/ping", json={**ping, "hub_id": "nope"}).json()["success"]

    owner = _bearer(hub._expected_token())
    s = client.get("/api/v1/master/summary", headers=owner).json()["summary"]
    assert s["users"] == 1 and s["hubs"] == 1
    assert s["devices"] == {"pc": 1, "mobile": 2, "server": 1} and s["ai_providers"] == {"gemini": 1}
    users = client.get("/api/v1/master/users", headers=owner).json()["users"]
    assert users[0]["email"] == "pat@example.com" and "google_sub" not in users[0]
    assert client.get("/api/v1/master/hubs", headers=owner).json()["hubs"][0]["origin"] == HUB

    friend = _sign_in(client, "friend@example.com")
    assert client.get("/api/v1/master/summary", headers=_bearer(friend)).status_code == 403
    assert "Willy network" in client.get("/master").text


def test_only_the_master_serves_master_endpoints(fresh_accounts, monkeypatch):  # noqa: F811
    monkeypatch.delenv("WILLY_MASTER", raising=False)
    client = TestClient(hub.app)
    assert client.get("/api/v1/broker/jwks").status_code == 404
    assert client.post("/api/v1/registry/ping", json={}).status_code == 404
    assert client.get("/master").status_code == 404


def test_check_ins_hold_counts_and_names_never_content(tmp_path):
    from server import registry
    from server.usage import UsageCounter

    u = UsageCounter(tmp_path / "usage.json")
    u.note(["launch_application", "set_volume"], True)
    u.note(["launch_application"], False)
    t = u.totals()
    assert t["commands"] == 2 and t["failures"] == 1 and t["tools"]["launch_application"] == 2
    assert "query" not in (tmp_path / "usage.json").read_text()

    payload = registry.build_ping("3.1.0", HUB, "pat@example.com", 2, {"pc": 1}, "groq", {})
    assert set(payload) == {"hub_id", "origin", "version", "platform", "owner_email", "accounts", "devices", "ai_provider", "usage"}


def test_telemetry_can_be_turned_off(monkeypatch):
    from server import registry

    monkeypatch.delenv("WILLY_MASTER", raising=False)
    monkeypatch.setenv("WILLY_BROKER_URL", MASTER)
    monkeypatch.setenv("WILLY_TELEMETRY", "on")
    assert registry.telemetry_enabled()
    monkeypatch.setenv("WILLY_TELEMETRY", "off")
    assert not registry.telemetry_enabled()
    monkeypatch.setenv("WILLY_TELEMETRY", "on")
    monkeypatch.setenv("WILLY_MASTER", "1")
    assert not registry.telemetry_enabled()  # the master doesn't report to itself
