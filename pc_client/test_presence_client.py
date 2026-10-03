"""
The PC client tells the hub why it goes offline (app closed, sleep, shutdown) and comes back
right after the PC wakes. Runs against a real local hub; nothing on the PC is changed.
"""

import sys
import time
import asyncio
import threading
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from pc_client.test_pc_realtime import live_hub, _start_node, _wait_for  # noqa: F401 - fixture reuse


def _hub_device(device_id):
    import server.app as hub
    return hub.device_manager.devices.get(device_id)


def test_closing_the_app_tells_the_hub_why(live_hub):  # noqa: F811
    port, token = live_hub
    node, thread = _start_node(port, token)
    assert _wait_for(lambda: node.connected), "never connected"
    node.stop()  # what quitting the app does
    thread.join(timeout=10)
    dev = _hub_device(node.device_id)
    assert _wait_for(lambda: dev.status == "offline", timeout=5)
    assert dev.offline_reason == "app_closed"
    assert dev.to_dict()["presence"]["reason_text"] == "the Willy app was closed"


def test_sleep_is_announced_and_wake_reconnects(live_hub):  # noqa: F811
    port, token = live_hub
    node, thread = _start_node(port, token)
    try:
        assert _wait_for(lambda: node.connected), "never connected"
        dev = _hub_device(node.device_id)
        first_session = node.connected_since
        node._on_suspend()  # what the WM_POWERBROADCAST sleep notification triggers
        assert _wait_for(lambda: dev.status == "offline" and dev.offline_reason == "sleep", timeout=5)
        time.sleep(3)  # no stray heartbeats flip it back while "asleep"
        assert dev.status == "offline"
        node._on_resume()  # ... and the wake-up notification
        assert _wait_for(lambda: node.connected and node.connected_since != first_session, timeout=10)
        assert _wait_for(lambda: dev.status == "online", timeout=5)
        assert dev.offline_reason is None
    finally:
        node.stop()
        thread.join(timeout=10)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows power notifications")
def test_power_event_listener_starts_and_stops():
    from pc_client.power_events import PowerEvents

    events = []
    power = PowerEvents(lambda: events.append("suspend"), lambda: events.append("resume"),
                        lambda kind: events.append(kind))
    assert power.start() is True and power.hwnd
    # Deliver the same messages Windows would send.
    import ctypes
    user32 = ctypes.WinDLL("user32")
    user32.SendMessageW.argtypes = (ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t)
    user32.SendMessageW(power.hwnd, 0x0218, 0x0004, 0)          # PBT_APMSUSPEND
    user32.SendMessageW(power.hwnd, 0x0218, 0x0012, 0)          # PBT_APMRESUMEAUTOMATIC
    user32.SendMessageW(power.hwnd, 0x0016, 1, 0x80000000)      # WM_ENDSESSION, sign-out
    assert events == ["suspend", "resume", "logoff"]
    power.stop()
    assert _wait_for(lambda: power.hwnd is None, timeout=3)


def test_pc_saves_a_token_update_from_the_hub(tmp_path, monkeypatch):
    from pc_client import client as client_mod, config

    env_file = tmp_path / ".env"
    env_file.write_text("WILLY_SERVER_URL=wss://example/ws/devices\nWILLY_REMOTE_TOKEN=old\n", encoding="utf-8")
    monkeypatch.setattr(config, "__file__", str(tmp_path / "config.py"))
    node = client_mod.PCClientNode.__new__(client_mod.PCClientNode)
    node.token = "old"
    node.log = lambda *_: None
    reconnects = []
    node.reconnect = lambda: reconnects.append(True)
    node._save_new_token("brand-new-token-0123456789")
    assert node.token == "brand-new-token-0123456789" and reconnects
    text = env_file.read_text(encoding="utf-8")
    assert "WILLY_REMOTE_TOKEN=brand-new-token-0123456789" in text and "=old" not in text and "WILLY_SERVER_URL" in text
    node._save_new_token("short")  # ignored
    assert node.token == "brand-new-token-0123456789"
