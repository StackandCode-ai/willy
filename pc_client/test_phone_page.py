"""
Tests for the Phone page's building blocks: link detection, alarm / timer parsing, presence
and "last seen" wording, which phone the page follows, notification rows, the result
pop-ups of phone actions, and the tray's "Ring my phone" item.
Nothing here shows a window or a tray icon.
"""

import sys
import time
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from pc_client.app_window import (
    PHONE_RING, WillyDesktopApp, as_url, device_online, fmt_clock, fmt_last_seen, fmt_when, looks_like_url,
    normalize_notifications, notification_time, parse_alarm_time, parse_timer_seconds, pick_phone,
    presence_line, quickdrop_payload,
)
from pc_client.tray import TrayIcon


def local(year, month, day, hour=0, minute=0) -> float:
    return time.mktime((year, month, day, hour, minute, 0, 0, 0, -1))


NOW = local(2026, 9, 28, 22, 0)


# ------------------------------------------------------------------ send to phone

@pytest.mark.parametrize("text", [
    "https://example.com", "http://x.io/a?b=1#c", "HTTPS://Example.com/Docs", "www.google.com", "example.com",
    "docs.python.org/3/library/", "sub.domain.co.uk", "my-site.dev:8443/x", "192.168.1.5:8080/admin",
])
def test_links_are_recognised(text):
    assert looks_like_url(text)
    assert looks_like_url(f"  {text}  ")


@pytest.mark.parametrize("text", [
    "", "   ", None, "buy milk", "3.14", "v1.2.3", "hello", "me@example.com", "example.com is great", "file.",
    "localhost", "see https://x.com",
])
def test_everything_else_is_a_note(text):
    assert not looks_like_url(text)


def test_links_get_a_scheme_and_notes_a_title():
    assert as_url("example.com") == "https://example.com"
    assert as_url("www.x.com/a") == "https://www.x.com/a"
    assert as_url("HTTP://Example.com") == "HTTP://Example.com"
    assert as_url("192.168.1.5:8080") == "http://192.168.1.5:8080", "LAN addresses rarely speak https"
    assert quickdrop_payload("  example.com/docs  ") == {"url": "https://example.com/docs"}
    assert quickdrop_payload("Call mom at 5") == {"text": "Call mom at 5", "title": "From PC"}
    assert quickdrop_payload("3.14", title="Note") == {"text": "3.14", "title": "Note"}


# ------------------------------------------------------------------ alarm & timer

@pytest.mark.parametrize("text, expected", [
    ("7:30 am", (7, 30)), ("7:30am", (7, 30)), ("7.30 PM", (19, 30)), ("19:05", (19, 5)), ("7 pm", (19, 0)),
    ("7pm", (19, 0)), ("7 p", (19, 0)), ("12 am", (0, 0)), ("12 pm", (12, 0)), ("12:15 a.m.", (0, 15)),
    ("0730", (7, 30)), ("1930", (19, 30)), ("730", (7, 30)), ("at 6:45 am", (6, 45)), ("7h30", (7, 30)),
    ("  07:05  ", (7, 5)), ("7", (7, 0)), ("0:00", (0, 0)), ("23:59", (23, 59)), ("noon", (12, 0)),
    ("Midnight", (0, 0)),
])
def test_alarm_times(text, expected):
    assert parse_alarm_time(text) == expected


@pytest.mark.parametrize("text", [
    "", None, "abc", "25:00", "24:00", "7:60", "13 pm", "0 am", "7:5", "tomorrow", "7:30 xm", "12345", "-7",
])
def test_bad_alarm_times_are_refused(text):
    assert parse_alarm_time(text) is None


def test_alarm_confirmation_uses_a_12_hour_clock():
    assert fmt_clock(7, 30) == "7:30 AM"
    assert fmt_clock(19, 5) == "7:05 PM"
    assert fmt_clock(0, 0) == "12:00 AM"
    assert fmt_clock(12, 0) == "12:00 PM"


@pytest.mark.parametrize("text, seconds", [
    ("5", 300), ("2.5", 150), ("2,5", 150), (" 10 ", 600), ("90 s", 90), ("90s", 90), ("10 min", 600),
    ("1 h", 3600), ("24 hours", 86400), ("0.01", 1),
])
def test_timer_takes_minutes(text, seconds):
    assert parse_timer_seconds(text) == seconds


@pytest.mark.parametrize("text", ["", None, "0", "abc", "25 h", "5 parsecs", "-5", "1:30", "five"])
def test_bad_timer_lengths_are_refused(text):
    assert parse_timer_seconds(text) is None


# ------------------------------------------------------------------ presence wording

def test_moments_read_the_way_people_say_them():
    assert fmt_when(local(2026, 9, 28, 21, 42), NOW) == "9:42 PM"
    assert fmt_when(local(2026, 9, 28, 0, 5), NOW) == "12:05 AM"
    assert fmt_when(local(2026, 9, 27, 21, 42), NOW) == "yesterday 9:42 PM"
    three_days = local(2026, 9, 25, 9, 7)
    assert fmt_when(three_days, NOW) == f"{time.strftime('%a', time.localtime(three_days))} 9:07 AM"
    old = local(2026, 9, 12, 21, 42)
    assert fmt_when(old, NOW) == f"12 {time.strftime('%b', time.localtime(old))} 9:42 PM"
    assert fmt_when(None, NOW) == "—"


def test_last_seen_wording():
    assert fmt_last_seen(NOW - 2, NOW) == "Last seen just now"
    assert fmt_last_seen(NOW - 12 * 60, NOW) == "Last seen 12m ago"
    assert fmt_last_seen(NOW - 3 * 3600, NOW) == "Last seen 3h ago"
    assert fmt_last_seen(local(2026, 9, 27, 21, 42), NOW) == "Last seen yesterday 9:42 PM"
    assert fmt_last_seen(None, NOW) == "Never seen"
    assert fmt_last_seen(0, NOW) == "Never seen"


def test_presence_line_online():
    assert presence_line({"online": True, "telemetry": {"last_ping_ms": 64.4}}, NOW) == "Online · 64 ms"
    assert presence_line({"online": True, "telemetry": {"last_ping_ms": None}}, NOW) == "Online"
    assert presence_line({"online": True, "telemetry": None}, NOW) == "Online"


def test_presence_line_offline_says_since_when_and_why():
    went = local(2026, 9, 28, 21, 42)
    dev = {"online": False, "last_seen": went - 30, "telemetry": {"last_ping_ms": 80},
           "presence": {"state": "offline", "since": went, "reason": "app_closed",
                        "reason_text": "the Willy app was closed on the phone", "last_online_at": went}}
    assert presence_line(dev, NOW) == "Offline since 9:42 PM — the Willy app was closed on the phone"
    dev["presence"]["reason_text"] = None
    assert presence_line(dev, NOW) == "Offline since 9:42 PM"


def test_presence_line_without_presence_uses_last_seen():
    assert presence_line({"online": False, "last_seen": local(2026, 9, 27, 21, 42)}, NOW) == \
        "Offline since yesterday 9:42 PM"
    assert presence_line({"online": False}, NOW) == "Offline"
    # A presence block that disagrees with the online flag is stale (the device was marked
    # offline locally without a fresh copy): its details are ignored.
    stale = {"online": False, "status": "offline", "last_seen": local(2026, 9, 28, 20, 15),
             "presence": {"state": "online", "since": local(2026, 9, 28, 8, 0), "reason_text": "x"}}
    assert presence_line(stale, NOW) == "Offline since 8:15 PM"


def test_online_flag_falls_back_to_presence_then_status():
    assert device_online({"online": True}) and not device_online({"online": False, "status": "online"})
    assert device_online({"presence": {"state": "online"}})
    assert not device_online({"presence": {"state": "offline"}, "status": "online"})
    assert device_online({"status": "online"})
    assert not device_online(None) and not device_online({})


# ------------------------------------------------------------------ which phone

def _dev(device_id, device_type="mobile", online=True, last_seen=100.0):
    return {"device_id": device_id, "device_type": device_type, "online": online, "last_seen": last_seen}


def test_the_page_follows_the_most_recently_seen_phone_preferring_online():
    pc = _dev("pc", "pc", last_seen=900)
    old = _dev("old", online=False, last_seen=500)
    live = _dev("live", online=True, last_seen=300)
    assert pick_phone({"pc": pc, "old": old, "live": live})["device_id"] == "live"
    assert pick_phone({"pc": pc, "old": old})["device_id"] == "old", "an offline phone still shows"
    assert pick_phone([pc, old, live])["device_id"] == "live"
    assert pick_phone({"pc": pc}) is None
    assert pick_phone({}) is None and pick_phone(None) is None


def test_the_followed_phone_sticks_while_it_is_online():
    a, b = _dev("a", last_seen=300), _dev("b", last_seen=400)
    assert pick_phone({"a": a, "b": b})["device_id"] == "b"
    assert pick_phone({"a": a, "b": b}, current_id="a")["device_id"] == "a", "no flipping between two phones"
    a["online"] = False
    assert pick_phone({"a": a, "b": b}, current_id="a")["device_id"] == "b", "moves on when it goes offline"
    b["online"] = False
    b["last_seen"] = 900
    assert pick_phone({"a": a, "b": b}, current_id="a")["device_id"] == "a", "none online: stay put"
    assert pick_phone({"a": a, "b": b}, current_id="gone")["device_id"] == "b"


# ------------------------------------------------------------------ notifications

def test_notification_rows_newest_first_and_cleaned_up():
    items = [
        {"app": "WhatsApp", "package": "com.whatsapp", "title": "Mom", "text": "Dinner at 8?\nBring  bread",
         "time": 1790000000000},
        {"app": "", "package": "com.google.android.gm", "title": "", "text": "New mail", "time": 1790000100000},
        {"title": "", "text": ""},
        "junk",
        {"app": "Clock", "title": "Alarm", "text": "", "time": None},
    ]
    rows = normalize_notifications(items)
    assert rows == [
        {"app": "com.google.android.gm", "title": "New mail", "text": "", "ts": 1790000100.0},
        {"app": "WhatsApp", "title": "Mom", "text": "Dinner at 8? Bring bread", "ts": 1790000000.0},
        {"app": "Clock", "title": "Alarm", "text": "", "ts": None},
    ]
    many = [{"app": "A", "title": str(i), "time": 1_700_000_000_000 + i * 1000} for i in range(25)]
    rows = normalize_notifications(many, limit=20)
    assert len(rows) == 20 and rows[0]["title"] == "24" and rows[-1]["title"] == "5"
    assert normalize_notifications(None) == [] and normalize_notifications({"a": 1}) == []


def test_notification_times_are_milliseconds_from_the_phone():
    assert notification_time(1790000000000) == 1790000000.0
    assert notification_time(1790000000) == 1790000000.0, "seconds are kept as they are"
    assert notification_time(None) is None and notification_time(0) is None and notification_time("x") is None


# ------------------------------------------------------------------ action results

class _FakeNode:
    def __init__(self, result, devices=None, running=True):
        self.result, self.devices, self.running, self.sent = result, devices or {}, running, []

    def send_action(self, device_id, action, payload):
        self.sent.append((device_id, action, payload))
        return (device_id, action, payload)  # stands in for the coroutine

    def submit(self, request):
        if not self.running:
            return None
        fut = Future()
        fut.set_result(self.result)
        return fut


def _fake_app(result, devices=None, running=True):
    toasts = []
    app = SimpleNamespace(node=_FakeNode(result, devices, running), pages={}, local_log=lambda _text: None,
                          post=lambda fn, *a: fn(*a),
                          toast=lambda title, message, query=None, kind="info", duration=4.5:
                          toasts.append((title, message, kind)))
    app.device_action = lambda *a, **kw: WillyDesktopApp.device_action(app, *a, **kw)
    return app, toasts


@pytest.mark.parametrize("result, toast", [
    ({"success": True, "message": "Ringing for 20 seconds."}, ("Ring", "Ringing for 20 seconds.", "success")),
    ({"success": True}, ("Ring", "Ringing…", "success")),
    ({"success": True, "needs_tap": True, "message": "Tap the Willy notification on your phone."},
     ("Ring", "Tap the Willy notification on your phone.", "phone")),
    ({"success": False, "error": "No contact named 'Bob' on your phone."},
     ("Ring", "No contact named 'Bob' on your phone.", "error")),
    ({"success": False, "error": "TIMEOUT", "reply": "Timed out waiting for response from device."},
     ("Ring", "Timed out waiting for response from device.", "error")),
    ("not a dict", ("Ring", "The device sent an unexpected reply.", "error")),
])
def test_phone_action_results_pop_up_and_come_back(result, toast):
    app, toasts = _fake_app(result)
    results = []
    WillyDesktopApp.device_action(app, "mobile_test", "ring_device", {}, "Ringing…", "Ring", on_result=results.append)
    assert toasts == [toast]
    assert len(results) == 1 and isinstance(results[0], dict)
    assert app.node.sent == [("mobile_test", "ring_device", {})]


def test_phone_action_without_a_running_engine():
    app, toasts = _fake_app({}, running=False)
    results = []
    WillyDesktopApp.device_action(app, "mobile_test", "vibrate", {"ms": 600}, "Done.", "Vibrate",
                                  on_result=results.append)
    assert toasts == [("Vibrate", "The Willy engine isn't running.", "error")]
    assert results and results[0]["success"] is False


def test_tray_ring_rings_the_online_phone_or_says_none_is_connected():
    app, toasts = _fake_app({"success": True, "message": "Ringing."},
                            devices={"pc": _dev("pc", "pc"), "p": {**_dev("p"), "name": "Pixel"}})
    WillyDesktopApp.ring_phone(app)
    assert app.node.sent == [("p", "ring_device", PHONE_RING)]
    assert toasts == [("Ring Pixel", "Ringing.", "success")]

    app, toasts = _fake_app({}, devices={"p": _dev("p", online=False)})
    WillyDesktopApp.ring_phone(app)
    assert app.node.sent == [] and toasts[0][0] == "No phone connected" and toasts[0][2] == "error"


def test_presence_alerts_pop_up_for_other_devices_and_are_always_logged():
    logs, toasts = [], []
    app = SimpleNamespace(node=SimpleNamespace(device_id="pc_me"), pages={"phone": None}, local_log=logs.append,
                          toast=lambda title, message, query=None, kind="info", duration=4.5:
                          toasts.append((title, message, kind)))
    base = {"type": "presence_alert", "reason": "app_closed", "reply": None, "timestamp": NOW}
    WillyDesktopApp._on_presence_alert(app, {**base, "device_id": "mobile_1", "device_name": "Pixel",
                                             "device_type": "mobile", "event": "offline",
                                             "title": "Pixel went offline",
                                             "message": "Offline since 9:42 PM: the Willy app was closed."})
    WillyDesktopApp._on_presence_alert(app, {**base, "device_id": "pc_other", "device_name": "Laptop",
                                             "device_type": "pc", "event": "online", "title": "Laptop is back online",
                                             "message": "Back after 5 minutes.", "reply": "CPU is at 12%."})
    WillyDesktopApp._on_presence_alert(app, {**base, "device_id": "pc_me", "device_name": "Me", "device_type": "pc",
                                             "event": "online", "title": "", "message": ""})
    assert toasts == [("Pixel went offline", "Offline since 9:42 PM: the Willy app was closed.", "phone"),
                      ("Laptop is back online", "Back after 5 minutes. CPU is at 12%.", "info")]
    assert len(logs) == 3 and logs[2] == "[*] Me is back online", "this PC's own alert is only logged"


# ------------------------------------------------------------------ tray

def test_tray_menu_has_ring_my_phone_after_voice_call():
    pytest.importorskip("pystray")
    dispatched = []
    tray = TrayIcon(lambda fn, *a: dispatched.append((fn, a)), {"ring_phone": "RING", "call": "CALL"})
    items = list(tray.build_menu().items)
    texts = [item.text for item in items]
    assert texts.index("Ring my phone") == texts.index("Voice call") + 1
    next(item for item in items if item.text == "Ring my phone")(None)
    assert dispatched == [("RING", ())]
