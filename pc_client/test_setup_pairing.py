"""PC first-run setup helpers and the real pairing handshake against a live (local) hub."""

import socket
import threading
import time

import pytest

from pc_client import pairing
from pc_client.setup_dialog import normalize_hub, save_settings, ws_url


@pytest.mark.parametrize("typed,expected", [
    ("willy.example.com", "https://willy.example.com"),
    ("https://truewilly.com/willy/", "https://truewilly.com/willy"),
    ("192.168.1.5:8000", "http://192.168.1.5:8000"),
    ("localhost:8000", "http://localhost:8000"),
    ("https://a.example.com/dashboard", "https://a.example.com"),
    ("bad address", None),
    ("", None),
    ("nodot", None),
])
def test_hub_address_is_normalised(typed, expected):
    assert normalize_hub(typed) == expected


def test_settings_file_keeps_other_lines(tmp_path):
    env = tmp_path / ".env"
    env.write_text("WILLY_PC_NAME=Desk\nWILLY_REMOTE_TOKEN=old\n", encoding="utf-8")
    save_settings("https://h.example.com/willy", "wdev_new", env)
    lines = env.read_text(encoding="utf-8").splitlines()
    assert "WILLY_PC_NAME=Desk" in lines
    assert "WILLY_REMOTE_TOKEN=wdev_new" in lines and "WILLY_REMOTE_TOKEN=old" not in lines
    assert "WILLY_SERVER_URL=wss://h.example.com/willy/ws/devices" in lines
    assert ws_url("http://10.0.0.2:8000") == "ws://10.0.0.2:8000/ws/devices"


@pytest.fixture()
def live_hub(monkeypatch):
    import uvicorn
    import server.app as hub

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(hub.app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    assert srv.started
    monkeypatch.setattr(pairing, "POLL_EVERY_SEC", 0.1)
    yield hub, f"http://127.0.0.1:{port}"
    srv.should_exit = True
    thread.join(5)


def test_pc_pairs_with_a_live_hub_and_gets_a_working_key(live_hub):
    hub, base = live_hub
    seen = {}

    def approve_when_code_shows(code, url):
        seen["code"], seen["url"] = code, url
        owner = hub.accounts.owner_principal()
        threading.Timer(0.3, lambda: hub.accounts.approve_pairing(code, owner.user_id)).start()

    res = pairing.pair_device(base, "pc_test_machine", "pc", "Test PC", on_code=approve_when_code_shows,
                              open_browser=False, timeout_sec=20)
    assert res["success"], res
    assert res["device_key"].startswith("wdev_") and seen["url"].startswith(base + "/pair?code=")

    import json
    import urllib.request

    req = urllib.request.Request(base + "/api/v1/auth/me", headers={"Authorization": f"Bearer {res['device_key']}"})
    me = json.loads(urllib.request.urlopen(req, timeout=5).read())["account"]
    assert me["via"] == "device_key" and me["owner"]


def test_pairing_reports_an_unreachable_hub():
    res = pairing.pair_device("http://127.0.0.1:9", "pc_x", "pc", "X", open_browser=False, timeout_sec=2)
    assert not res["success"] and "reach" in res["error"].lower()


def test_pc_connects_with_a_dashboard_code(live_hub):
    hub, base = live_hub
    link = hub.accounts.create_link_code(hub.accounts.owner_principal().user_id)
    res = pairing.redeem_code(base, link["code"].lower(), "pc_code_machine", "pc", "Code PC")
    assert res["success"] and res["device_key"].startswith("wdev_")
    again = pairing.redeem_code(base, link["code"], "pc_other", "pc", "Other PC")
    assert not again["success"] and "expired" in again["error"].lower()
