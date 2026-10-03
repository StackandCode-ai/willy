"""
Tests for the desktop app's Server page: the display helpers, the hub REST helper (against a
fake connection, never the real hub) and one render pass of the page itself with a fake app
on a withdrawn Tk root (skipped when Tk can't start).
"""

import json
import sys
import time
from concurrent.futures import Future
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from pc_client import app_window as aw
from pc_client.app_window import (
    AMBER, DIM, GREEN, RED, SKY, HubError, action_ok, app_status_color, cert_color, cert_text, container_color,
    file_name_error, file_row_text, fmt_bytes, fmt_load, fmt_ms, hub_json, pick_server, restart_warning,
    server_action_error, server_app_keys, server_live_system, server_path_join, server_problem_label,
    server_problems, server_state, service_color, site_chip, site_url, sort_sites,
)

STATUS = {
    "success": True,
    "summary": "Your server is having trouble.",
    "problems": [{"key": "site:old.example.com", "message": "old.example.com is down (HTTP 502)."},
                 {"key": "cert:shop.example.com", "message": "The certificate for shop.example.com expires in 9 days."}],
    "ignored": ["site:test.example.com"],
    "snapshot": {
        "name": "Willy Server", "checked_at": 1_700_000_000.0, "sites_checked_at": 1_699_999_000.0,
        "system": {"cpu_pct": 93.0, "cores": 4, "load": [0.5, 0.4, 0.3], "ram_pct": 61.2, "ram_used_mb": 2450,
                   "ram_total_mb": 4000, "swap_pct": 0.0, "disk_pct": 71.0, "disk_free_gb": 22.4,
                   "uptime_sec": 900_000, "hostname": "vps-1"},
        "apps": [{"name": "willy-server", "status": "online", "restarts": 3, "memory_mb": 180, "cpu": 2.0,
                  "uptime_sec": 3600},
                 {"name": "worker", "status": "errored", "restarts": 15, "memory_mb": 0, "cpu": 0, "uptime_sec": None},
                 {"name": "worker", "status": "online", "restarts": 0, "memory_mb": 50, "cpu": 1, "uptime_sec": 60}],
        "services": {"nginx": "active", "docker": "failed"},
        "containers": [{"name": "redis", "state": "running", "status": "Up 3 days"},
                       {"name": "old", "state": "exited", "status": "Exited (1) 2 days ago"}],
        "sites": [{"domain": "shop.example.com", "up": True, "status": 200, "ms": 120, "error": None, "cert_days": 9},
                  {"domain": "old.example.com", "up": False, "status": 502, "ms": 80, "error": None,
                   "cert_days": 40},
                  {"domain": "test.example.com", "up": False, "status": None, "ms": 8000,
                   "error": "URLError: timed out", "cert_days": None}],
    },
}


# ------------------------------------------------------------------ helpers

def test_state_chip_and_problem_labels():
    assert server_state({"problems": []}) == ("Healthy", GREEN)
    assert server_state({}) == ("Healthy", GREEN)
    assert server_state(STATUS) == ("2 problems", RED), "a website is down"
    assert server_state({"problems": [{"key": "cpu", "message": "CPU is at 95%."}]}) == ("1 problem", AMBER)
    assert server_problems({"problems": [{"key": "ram"}, {"message": "no key"}, "junk"]}) == \
        [{"key": "ram", "message": "High memory use"}]
    assert server_problem_label("site:test.example.com") == "Website test.example.com"
    assert server_problem_label("loop:worker") == "worker crash loop"
    assert server_problem_label("disk") == "Disk almost full"
    assert server_problem_label("weird:thing") == "weird:thing"


def test_status_colours():
    assert app_status_color("online") == GREEN
    assert app_status_color("launching") == AMBER
    assert app_status_color("errored") == RED and app_status_color("stopped") == RED
    assert service_color("active") == GREEN and service_color("failed") == RED
    assert service_color("unknown") == DIM and service_color("activating") == AMBER
    assert container_color("running") == GREEN and container_color("exited") == RED
    assert container_color("restarting") == AMBER


@pytest.mark.parametrize("days, color, text", [
    (None, DIM, "—"), (90, GREEN, "90 days left"), (31, GREEN, "31 days left"), (30, AMBER, "30 days left"),
    (15, AMBER, "15 days left"), (14, RED, "14 days left"), (1, RED, "1 day left"), (0, RED, "Expires today"),
    (-3, RED, "Expired"),
])
def test_certificate_days(days, color, text):
    assert cert_color(days) == color
    assert cert_text(days) == text


def test_sites_and_numbers():
    assert site_chip({"up": True, "status": 200}) == ("Up · 200", GREEN)
    assert site_chip({"up": False, "status": 502}) == ("Down · 502", RED)
    assert site_chip({"up": False, "status": None}) == ("Down", RED)
    assert [s["domain"] for s in sort_sites(STATUS["snapshot"]["sites"] + [{"no": "domain"}])] == \
        ["old.example.com", "test.example.com", "shop.example.com"]
    assert site_url("shop.example.com") == "https://shop.example.com"
    for bad in ("", None, "localhost", "evil.com/../x", "a b.com", "javascript:alert(1)", "-x.com"):
        assert site_url(bad) is None, bad
    assert fmt_ms(None) == "—" and fmt_ms(85) == "85 ms" and fmt_ms(2400) == "2.4 s"
    assert fmt_load([0.5, 0.123, 2]) == "0.50 · 0.12 · 2.00"
    assert fmt_load(None) == "—"


def test_app_rows_keep_pm2_order_and_unique_keys():
    keys = [k for k, _a in server_app_keys(STATUS["snapshot"]["apps"] + [{"status": "online"}, "junk"])]
    assert keys == ["willy-server", "worker", "worker #2"]


def test_restarting_the_hub_warns_about_the_reconnect():
    assert "reconnects in a few seconds" in restart_warning("willy-server")
    assert "worker" in restart_warning("worker") and "hub" not in restart_warning("worker")


# ------------------------------------------------------------------ hub_json

class _Response:
    def __init__(self, status, body):
        self.status = status
        self._body = body

    def read(self):
        return self._body


class _Conn:
    def __init__(self, status=200, body=b"{}", fail=None):
        self.status, self.body, self.fail = status, body, fail
        self.requests = []
        self.closed = False

    def request(self, method, url, body=None, headers=None):
        if self.fail:
            raise self.fail
        self.requests.append((method, url, body, headers))

    def getresponse(self):
        return _Response(self.status, self.body)

    def close(self):
        self.closed = True


@pytest.fixture
def fake_hub(monkeypatch):
    from pc_client.tools import file_relay

    state = {"conn": _Conn()}
    monkeypatch.setattr(file_relay, "_hub", lambda: ("https", "hub.example.com", 443, "/willy"))
    monkeypatch.setattr(file_relay, "_headers", lambda: {"Authorization": "Bearer SECRET-TOKEN"})
    monkeypatch.setattr(file_relay, "_connect", lambda timeout=60: state["conn"])
    return state


def test_hub_json_sends_the_token_and_json(fake_hub):
    fake_hub["conn"] = conn = _Conn(body=json.dumps({"ignored": ["site:a.com"]}).encode())
    assert hub_json("POST", "/api/v1/server/ignore", {"key": "site:a.com", "ignore": True}) == {"ignored": ["site:a.com"]}
    method, url, body, headers = conn.requests[0]
    assert (method, url) == ("POST", "/willy/api/v1/server/ignore")
    assert json.loads(body) == {"key": "site:a.com", "ignore": True}
    assert headers["Authorization"] == "Bearer SECRET-TOKEN" and headers["Content-Type"] == "application/json"
    assert conn.closed

    fake_hub["conn"] = conn = _Conn(body=b"")
    assert hub_json("GET", "/api/v1/server/status?refresh=false") == {}
    assert conn.requests[0][2] is None and "Content-Type" not in conn.requests[0][3]


@pytest.mark.parametrize("status, body, offline, words", [
    (404, b'{"detail": "No logs for \'x\'."}', False, "No logs for 'x'."),
    (500, b'{"detail": "pm2 couldn\'t restart worker."}', False, "pm2 couldn't restart worker."),
    (500, b"<html>oops</html>", False, "The hub answered HTTP 500."),
    (422, b'{"detail": [{"loc": ["body"]}]}', False, "The hub answered HTTP 422."),
    (401, b'{"detail": "Unauthorized"}', False, "rejected this PC's token"),
    (502, b"Bad gateway", True, "isn't answering"),
])
def test_hub_json_errors(fake_hub, status, body, offline, words):
    fake_hub["conn"] = _Conn(status=status, body=body)
    with pytest.raises(HubError) as info:
        hub_json("GET", "/x")
    assert info.value.status == status and info.value.offline is offline
    assert words in str(info.value)
    assert "SECRET-TOKEN" not in str(info.value)


def test_hub_json_unreachable(fake_hub):
    fake_hub["conn"] = conn = _Conn(fail=ConnectionRefusedError(10061, "refused"))
    with pytest.raises(HubError) as info:
        hub_json("GET", "/api/v1/server/status")
    assert info.value.offline and "hub.example.com" in str(info.value) and conn.closed


# ------------------------------------------------------------------ server device helpers

def _server_dev(online=True, **telemetry):
    t = {"cpu_pct": 12.0, "ram_pct": 40.0, "ram_used_gb": 1.6, "ram_total_gb": 4.0, "swap_pct": 0.0, "disk_pct": 70.0,
         "disk_free_gb": 22.4, "disk_total_gb": 80.0, "load_1": 0.1, "load_5": 0.2, "load_15": 0.3, "cores": 4,
         "uptime_hours": 250.0, "process_count": 123, "hostname": "vps-1", **telemetry}
    return {"device_id": "server_vps_1", "device_type": "server", "name": "Willy Server", "online": online,
            "last_seen": time.time(), "telemetry": t}


def test_server_device_helpers():
    pc = {"device_id": "pc_1", "device_type": "pc", "online": True}
    old = {**_server_dev(online=False), "device_id": "server_old", "last_seen": 5}
    assert pick_server({"pc_1": pc}) is None
    assert pick_server([pc, old, _server_dev()])["device_id"] == "server_vps_1"
    assert pick_server([old, {**_server_dev(online=False), "last_seen": 1}])["device_id"] == "server_old"
    live = server_live_system(_server_dev())
    assert live["ram_used_mb"] == 1600 and live["ram_total_mb"] == 4000 and live["uptime_sec"] == 900_000
    assert live["load"] == [0.1, 0.2, 0.3] and live["process_count"] == 123
    assert server_live_system({"telemetry": {}}) == {} and server_live_system(None) == {}


def test_action_results_and_names():
    assert action_ok({"message": "x"}) and not action_ok({"success": False}) and not action_ok(None)
    assert "isn't connected" in server_action_error({"success": False, "error": "DEVICE_OFFLINE"})
    assert "20 seconds" in server_action_error({"success": False, "error": "TIMEOUT"})
    assert server_action_error({"success": False, "error": "OFFLINE", "reply": "Not connected."}) == "Not connected."
    assert server_action_error({"success": False, "error": "No  such\nfile"}) == "No such file"
    assert server_action_error("junk") == "The server didn't answer."
    assert fmt_bytes(None) == "" and fmt_bytes(512) == "512 B" and fmt_bytes(2048) == "2.0 KB"
    assert fmt_bytes(5 * 1024 ** 3) == "5.0 GB"
    assert server_path_join("/home/hari/", "x") == "/home/hari/x"
    assert server_path_join("/", "tmp") == "/tmp" and server_path_join("", "x") == "~/x"
    assert file_name_error("ok.txt") is None
    assert file_name_error("a/b") and file_name_error("..") and file_name_error(".")
    line = file_row_text({"name": "a-very-long-folder-name", "folder": True, "modified": None}, 12)
    assert line.startswith("a-very-long…") and len(line.split("  ")[0]) == 12
    assert "2.0 KB" in file_row_text({"name": "f.txt", "size": 2048, "modified": time.time()}, 20)


class _FakeNode:
    """Stands in for PCClientNode: actions resolve at once with results[action] (a dict, or a
    function of the payload)."""

    def __init__(self):
        self.devices, self.connected, self.sent, self.results = {}, True, [], {}

    def send_action(self, device_id, action, payload):
        self.sent.append((device_id, action, payload))
        return action, payload

    def submit(self, request):
        action, payload = request
        res = self.results.get(action, {"success": True})
        fut = Future()
        fut.set_result(res(payload) if callable(res) else res)
        return fut


# ------------------------------------------------------------------ the page itself

@pytest.fixture(scope="module")
def tk_app():
    """One withdrawn Tk root for the module (creating many roots in one process makes Tk on
    Windows fail now and then with "Can't find a usable tk.tcl")."""
    import tkinter as tk

    import customtkinter as ctk

    class FakeApp(ctk.CTk):
        px = aw.WillyDesktopApp.px
        glyph = aw.WillyDesktopApp.glyph
        button_style = aw.WillyDesktopApp.button_style
        button = aw.WillyDesktopApp.button
        card = aw.WillyDesktopApp.card
        card_header = aw.WillyDesktopApp.card_header
        page_header = aw.WillyDesktopApp.page_header

    try:
        app = FakeApp()
    except tk.TclError as e:  # no display
        pytest.skip(f"Tk unavailable: {e}")
    app.withdraw()
    app.closing = False
    app.scale = float(ctk.ScalingTracker.get_widget_scaling(app))
    app.fonts = aw.Fonts(app, app.scale)
    app.icons = aw.Icons(app.scale)
    try:
        yield app
    finally:
        try:
            ctk.AppearanceModeTracker.callback_list.clear()
            for window in list(ctk.ScalingTracker.window_widgets_dict):
                ctk.ScalingTracker.window_widgets_dict[window] = []
        except Exception:
            pass
        app.destroy()


@pytest.fixture
def page(tk_app):
    app = tk_app
    app.calls, app.toasts, app.logs = [], [], []
    app.run_bg = lambda fn, *args, on_done=None, on_error=None: app.calls.append((fn, args, on_done, on_error))
    app.toast = lambda title, message, query=None, kind="info", duration=4.5: app.toasts.append((title, message, kind))
    app.local_log = app.logs.append
    app.post = lambda fn, *a: fn(*a)
    app.copy_text = lambda text: app.toasts.append(("Copied", text, "success"))
    app.asked = []
    app.confirm = type("C", (), {"ask": lambda self, heading, message, ok, cb, danger=True:
                                 app.asked.append((heading, message, cb))})()
    app.node = _FakeNode()
    p = aw.ServerPage(app, app)
    p.grid(row=0, column=0, sticky="nsew")
    p.ensure_built()
    p.visible = True
    try:
        yield p
    finally:
        for dialog in (p._text_dialog, p._prompt):
            if dialog is not None:
                dialog.destroy()
        if p._job is not None:
            p.after_cancel(p._job)
        p.destroy()


def _answer(page, call, res=None, err=None):
    fn, args, on_done, on_error = call
    assert fn is hub_json
    if err is not None:
        on_error(err)
    else:
        on_done(res)
    page.update_idletasks()


def test_page_renders_a_status_and_handles_actions(page, monkeypatch):
    monkeypatch.setattr(aw.ServerTextDialog, "open", lambda self: None)  # never show a window
    app = page.app
    page.on_show()
    assert len(app.calls) == 1 and app.calls[0][1][:2] == ("GET", "/api/v1/server/status?refresh=false")
    assert page._fetching == 1 and str(page.refresh_btn.cget("state")) == "normal", "no flicker on auto-reads"
    page.refresh()  # a click always asks, even while an automatic read is in flight
    assert len(app.calls) == 2 and str(page.refresh_btn.cget("state")) == "disabled"
    page.refresh()  # ...but not twice
    assert len(app.calls) == 2
    _answer(page, app.calls.pop(), STATUS)
    assert str(page.refresh_btn.cget("state")) == "normal"
    _answer(page, app.calls.pop(), STATUS)

    assert page.chip.cget("text") == "●  2 problems"
    assert page.subtitle.cget("text") == "Willy Server · vps-1"
    assert page.problems_card.winfo_manager() == "grid"
    assert page.meters["cpu"]["value"].cget("text") == "93%"
    assert page.meters["cpu"]["value"].cget("fg") == RED
    assert page.meters["ram"]["detail"].cget("text") == "2.4 GB of 3.9 GB"
    assert page.meters["disk"]["detail"].cget("text") == "22.4 GB free"
    assert "0.50 · 0.40 · 0.30" in page.load_label.cget("text")
    assert page.uptime_label.cget("text") == "Up 10d 10h"
    assert list(page._app_rows) == ["willy-server", "worker", "worker #2"]
    assert page._app_rows["worker"]["status"].cget("text") == "errored"
    assert page.apps_note.cget("text") == "2 of 3 online"
    assert page._orders["sites"] == ["old.example.com", "test.example.com", "shop.example.com"]
    shop, test = page._site_rows["shop.example.com"], page._site_rows["test.example.com"]
    assert shop["chip"].cget("text") == "Up · 200" and shop["cert"].cget("text") == "9 days left"
    assert shop["cert"].cget("fg") == RED and not shop["mute"].winfo_manager()
    assert test["mute"].cget("text") == "Unmute" and test["mute"].winfo_manager() == "grid"
    assert "timed out" in test["ms"].cget("text")
    assert page._site_rows["old.example.com"]["mute"].cget("text") == "Mute"
    assert len(page.svc_box.winfo_children()) > 4

    # Mute a down site: the hub call, then the problem leaves the list at once and the status is re-read.
    page._toggle_site("old.example.com")
    fn, args, on_done, _ = app.calls.pop()
    assert args[:3] == ("POST", "/api/v1/server/ignore", {"key": "site:old.example.com", "ignore": True})
    on_done({"ignored": ["site:old.example.com", "site:test.example.com"]})
    assert [p["key"] for p in page._status["problems"]] == ["cert:shop.example.com"]
    assert page._site_rows["old.example.com"]["mute"].cget("text") == "Unmute"
    assert app.toasts[-1][2] == "success"
    assert app.calls.pop()[1][:2] == ("GET", "/api/v1/server/status?refresh=false")
    page._fetching = 0

    # Restart: confirm first (fake the confirm dialog), then POST, a pop-up, and a re-read later.
    asked = app.asked
    page.restart_app("willy-server")
    assert asked[0][0] == "Restart willy-server?" and "reconnects" in asked[0][1]
    asked[0][2]()
    assert page._app_rows["willy-server"]["restart"].cget("text") == "Restarting…"
    _answer(page, app.calls.pop(), {"message": "Restarting the Willy hub; it reconnects in a few seconds."})
    assert app.toasts[-1] == ("Restart willy-server", "Restarting the Willy hub; it reconnects in a few seconds.",
                              "success")
    assert page._app_rows["willy-server"]["restart"].cget("text") == "Restart"
    assert page._job is not None

    # Check sites now: busy state, then the answer; a site that went away loses its row.
    page.check_sites()
    assert page.sites_btn.cget("text") == "Checking sites…"
    call = app.calls.pop()
    assert call[1][:2] == ("GET", "/api/v1/server/status?refresh=true")
    healthy = json.loads(json.dumps(STATUS))
    healthy["problems"], healthy["ignored"] = [], []
    healthy["snapshot"]["sites"] = healthy["snapshot"]["sites"][:1]
    _answer(page, call, healthy)
    assert page.sites_btn.cget("text") == "Check sites now"
    assert list(page._site_rows) == ["shop.example.com"]
    assert page.chip.cget("text") == "●  Healthy"
    assert not page.problems_card.winfo_manager()
    assert app.toasts[-1] == ("Websites checked", "1 of 1 website up.", "success")

    # The hub goes away: the data stays, with a banner; a stale failure never hides newer data.
    page.refresh()
    _answer(page, app.calls.pop(), err=HubError("Can't reach your Willy server (hub).", offline=True))
    assert page.chip.cget("text") == "●  Unreachable"
    assert page.banner.winfo_manager() == "grid" and "Showing what it reported" in page.banner_text.cget("text")
    page.refresh()
    _answer(page, app.calls.pop(), STATUS)
    assert not page.banner.winfo_manager() and page.chip.cget("text") == "●  2 problems"

    # The log viewer.
    page.show_app_logs("worker")
    dialog = page._text_dialog
    _answer(page, app.calls.pop(), {"logs": "started\nError: boom\n"})
    assert dialog.text.get("1.0", "end").strip() == "started\nError: boom"
    assert "err" in dialog.text.tag_names("2.0")
    dialog.close()


def test_page_offline_before_any_answer(page):
    app = page.app
    page.on_show()
    _answer(page, app.calls.pop(), err=HubError("Can't reach your Willy server (hub).", offline=True))
    assert page.empty.winfo_manager() == "grid"
    assert page.empty_title.cget("text") == "Can't reach your Willy server"
    assert page.empty_retry.winfo_manager() == "pack"
    assert page.chip.cget("text") == "●  Unreachable"
    assert app.logs and "Server page" in app.logs[-1]
    page.on_show()  # just tried: no new request within 5 s
    assert not app.calls
    page._fetch_at -= aw.SERVER_REFRESH_SEC
    page.tick()  # the periodic re-read
    assert len(app.calls) == 1


def test_live_resources_and_server_tabs(page, monkeypatch):
    monkeypatch.setattr(aw.ServerTextDialog, "open", lambda self: None)
    monkeypatch.setattr(aw.PromptDialog, "open", lambda self: None)
    app, node = page.app, page.app.node
    page.on_show()
    _answer(page, app.calls.pop(), STATUS)
    assert page.res_note.cget("text") == "From the last check" and page._ready is False

    # The server agent connects: live meters from its heartbeats, controls enabled.
    dev = _server_dev()
    node.devices = {dev["device_id"]: dev}
    page.on_device(dev)
    assert page._ready is True and page.res_note.cget("text") == "●  Live"
    assert page.meters["cpu"]["value"].cget("text") == "12%"
    assert page.meters["ram"]["detail"].cget("text") == "1.6 GB of 3.9 GB"
    assert page.meters["disk"]["detail"].cget("text") == "22.4 GB free of 80"
    assert page.uptime_label.cget("text") == "Up 10d 10h · 123 processes"
    node.devices = {dev["device_id"]: {**dev, "telemetry": {**dev["telemetry"], "cpu_pct": 55.0}}}
    page.on_device(dev)  # what counts is node.devices (the event only says something changed)
    assert page.meters["cpu"]["value"].cget("text") == "55%"

    # Service control: asks first, then the "control" action, a pop-up and a status re-read.
    node.results["control"] = {"success": True, "message": "Restarted nginx."}
    page.control("service", "nginx", "restart")
    heading, message, confirm = app.asked.pop()
    assert heading == "Restart nginx?" and "Every website" in message
    confirm()
    assert node.sent[-1] == ("server_vps_1", "control", {"kind": "service", "name": "nginx", "action": "restart"})
    assert app.toasts[-1] == ("Restart nginx", "Restarted nginx.", "success")
    node.results["control"] = {"success": True, "stdout": "line 1\nline 2"}
    page.control_logs("container", "redis")
    assert node.sent[-1][2]["action"] == "logs"
    assert page._text_dialog.text.get("1.0", "end").strip() == "line 1\nline 2"

    # Files: the top level, then a folder; buttons follow the selection.
    home = {"success": True, "path": "/home/hari", "parent": "/home", "entries": [
        {"name": "docs", "path": "/home/hari/docs", "folder": True, "size": None, "modified": time.time()},
        {"name": "notes.txt", "path": "/home/hari/notes.txt", "folder": False, "size": 2048,
         "modified": time.time()},
        {"name": "site.zip", "path": "/home/hari/site.zip", "folder": False, "size": 4096, "modified": 1}]}
    places = {"success": True, "path": "", "parent": None, "drives": [{"name": "/", "free_gb": 22.4, "total_gb": 80}],
              "entries": [{"name": "/home/hari", "path": "/home/hari", "folder": True}]}
    node.results["list_dir"] = lambda p: home if p.get("path") else places
    page.show_tab("Files")
    assert node.sent[-1][1:] == ("list_dir", {"path": ""})
    assert page.f_list.size() == 1 and "Server folders" in page.f_status.cget("text")
    page.f_list.selection_set(0)
    page.files_activate()
    assert page._files_path == "/home/hari" and page.f_list.size() == 3
    assert page.f_list.get(0).startswith("docs/") and page.f_list.itemcget(0, "fg") == SKY
    assert str(page.f_buttons["delete"].cget("state")) == "disabled", "nothing selected yet"
    page.f_list.selection_set(1)
    page._files_buttons()
    assert str(page.f_buttons["download"].cget("state")) == "normal"
    assert str(page.f_buttons["unzip"].cget("state")) == "disabled"
    page.files_delete()
    heading, message, confirm = app.asked.pop()
    assert heading == "Delete notes.txt?" and ".willy-trash" in message
    node.results["manage_file"] = {"success": True, "message": "Moved notes.txt to ~/.willy-trash."}
    confirm()
    assert ("server_vps_1", "manage_file", {"op": "delete", "path": "/home/hari/notes.txt"}) in node.sent
    assert node.sent[-1][1] == "list_dir", "the folder is re-listed"
    page.files_mkdir()
    page.prompt.entry.insert(0, "a/b")
    page.prompt._ok()
    assert "can't contain" in page.prompt.error.cget("text")
    page.prompt.entry.delete(0, "end")
    page.prompt.entry.insert(0, "new")
    page.prompt._ok()
    assert ("server_vps_1", "manage_file", {"op": "mkdir", "path": "/home/hari/new"}) in node.sent
    page.f_list.selection_set(1)
    node.results["file_to_hub"] = {"success": True, "file": {"url": "/api/v1/files/abc", "name": "notes.txt"}}
    page.files_download()
    fn, args, on_done, _ = app.calls.pop()
    assert fn.__name__ == "receive_file" and args == ({"url": "/api/v1/files/abc", "name": "notes.txt"},)
    on_done({"success": True, "message": "Saved notes.txt to Downloads\\Willy on your PC."})
    assert app.toasts[-1][0] == "Download" and app.toasts[-1][2] == "success"
    node.results["manage_file"] = {"success": True, "content": "hello", "truncated": False}
    page.f_list.selection_clear(0, "end")
    page.f_list.selection_set(1)
    page.files_activate()  # a file opens in the viewer
    assert page._text_dialog.text.get("1.0", "end").strip() == "hello"

    # Terminal: Run sends the command; output, errors and the exit code are shown; history recalls it.
    node.results["run_shell"] = {"success": False, "exit_code": 2, "stdout": "a\n", "stderr": "oops",
                                 "duration_sec": 0.05}
    page.show_tab("Terminal")
    page.term_input.insert("1.0", "ls -la")
    page.term_run()
    assert node.sent[-1][1:] == ("run_shell", {"command": "ls -la", "timeout": aw.SERVER_SHELL_TIMEOUT_SEC})
    out = page.t_out.get("1.0", "end")
    assert "~ $ ls -la" in out and "oops" in out and "exit 2 · 0.05 s" in out
    assert page.term_input.get("1.0", "end").strip() == "" and page.t_run.cget("text") == "Run"
    page._term_history(-1)
    assert page.term_input.get("1.0", "end").strip() == "ls -la"

    # Processes: the top 15, re-asked when the sort changes.
    rows = [{"pid": 10 + i, "name": f"proc{i}", "user": "root", "cpu": 60.0 if i == 0 else 1.0, "memory_mb": 100 - i,
             "command": "/usr/bin/x"} for i in range(3)]
    node.results["list_processes"] = lambda p: {"success": True, "processes": rows}
    page.show_tab("Processes")
    assert node.sent[-1][1:] == ("list_processes", {"limit": 15, "sort": "memory"})
    assert page._proc_rows[0]["name"].cget("text") == "proc0" and page._proc_rows[0]["cpu"].cget("fg") == RED
    assert not page.p_empty.winfo_manager()
    page._procs_sort_changed("CPU")
    assert node.sent[-1][2]["sort"] == "cpu"

    # The agent goes away: every tab says so and its controls wait.
    node.devices = {dev["device_id"]: {**dev, "online": False}}
    page.sync_device()
    assert page._ready is False and "offline" in page.p_hint.cget("text")
    assert str(page.p_refresh.cget("state")) == "disabled"
    assert str(page.f_buttons["open"].cget("state")) == "disabled"
    assert page.res_note.cget("text") == "From the last check"
    sent = len(node.sent)
    page.show_tab("Terminal")
    page.term_input.insert("1.0", "uptime")
    page.term_run()
    assert len(node.sent) == sent and app.toasts[-1][2] == "error"


# ------------------------------------------------------------------ system administration & history

def test_admin_helpers():
    assert aw.fmt_kbit(None) == "—" and aw.fmt_kbit(0) == "0 kbit/s" and aw.fmt_kbit(4.24) == "4.2 kbit/s"
    assert aw.fmt_kbit(850) == "850 kbit/s" and aw.fmt_kbit(1234) == "1.2 Mbit/s" and aw.fmt_kbit(-5) == "0 kbit/s"
    assert aw.fmt_gb(None) == "—" and aw.fmt_gb(0.25) == "250 MB" and aw.fmt_gb(12.34) == "12.3 GB"
    assert aw.fmt_wait(20) == "20 seconds" and aw.fmt_wait(1920) == "32 minutes"
    assert "32 minutes" in server_action_error({"success": False, "error": "TIMEOUT"}, wait=1920)
    assert aw.nice_ceiling(0) == 1 and aw.nice_ceiling(7) == 10 and aw.nice_ceiling(130) == 200
    assert aw.nice_ceiling(2400) == 2500

    points = [{"t": 180, "cpu": 30}, {"t": 0, "cpu": 10}, {"t": 60, "cpu": None}, {"t": 120, "cpu": "20"},
              {"cpu": 99}, "junk", {"t": 1000, "cpu": 50}]
    pairs = aw.history_values(points, "cpu")
    assert pairs == [(0, 10.0), (60, None), (120, 20.0), (180, 30.0), (1000, 50.0)]
    assert aw.history_stats(pairs) == (10.0, 27.5, 50.0) and aw.history_stats([(0, None)]) is None
    assert aw.history_gap([0, 60, 120, 180, 1000]) == 180.0
    assert aw.history_segments(pairs, 180.0) == [[(0, 10.0)], [(120, 20.0), (180, 30.0)], [(1000, 50.0)]]
    assert aw.history_stats_text(pairs, lambda v: f"{v:.0f}%") == "min 10%  ·  avg 28%  ·  max 50%"

    assert aw.boot_state("enabled") is True and aw.boot_state("disabled") is False
    assert aw.boot_state("static") is None and aw.boot_state("") is None
    rows = [{"name": "zeta", "active": "active", "description": "Z"},
            {"name": "alpha", "active": "inactive", "description": "web cache"},
            {"name": "broken", "active": "failed", "description": "x"}, {"active": "failed"}, "junk"]
    assert [r["name"] for r in aw.filter_services(rows)] == ["broken", "zeta", "alpha"]
    assert [r["name"] for r in aw.filter_services(rows, "  WEB ")] == ["alpha"]
    assert aw.service_line({"name": "nginx", "active": "active", "sub": "running", "boot": "enabled",
                            "description": "web"}, 10).startswith("nginx       active/running")

    assert aw.ssh_setting("password", "yes") == ("On", AMBER)
    assert aw.ssh_setting("password", "no")[1] == GREEN
    assert aw.ssh_setting("root", "prohibit-password") == ("Keys only", GREEN)
    assert aw.ssh_setting("root", "yes") == ("On", RED)
    assert aw.ssh_setting("fail2ban", "active") == ("Active", GREEN)
    assert aw.ssh_setting("fail2ban", "not installed")[1] == AMBER
    assert "never touched" in aw.cleanup_warning("docker") and "Permanently" in aw.cleanup_warning("trash")
    assert aw.cleanable_text(1.5) == ("1.5 GB", True) and aw.cleanable_text(0.0) == ("Nothing to clean", False)
    assert aw.cleanable_text("Images: 1.2GB (40%)") == ("Images: 1.2GB (40%)", True)
    assert aw.cleanable_text("") == ("Not available", False)

    assert aw.journal_payload("All units", "any", "any time", "", "100") == ({"lines": 100}, None)
    assert aw.journal_payload("nginx", "err", " 1 hour  ago", " boom ", "200") == (
        {"lines": 200, "unit": "nginx", "priority": "err", "since": "1 hour ago", "grep": "boom"}, None)
    assert aw.journal_payload("bad unit;rm", "any", "", "", 100)[1]
    assert aw.journal_payload("", "loud", "", "", 100)[1]
    assert aw.journal_payload("", "", "$(reboot)", "", 100)[1]


class _WireNode:
    """Just enough of PCClientNode for the long-wait request: connected, _pending, _send."""

    def __init__(self, answer=None):
        self.connected, self._pending, self.sent, self.answer, self.plain = True, {}, [], answer, []

    async def _send(self, message):
        self.sent.append(message)
        if self.answer is not None:
            fut = self._pending[message["request_id"]]
            fut.get_loop().call_soon(fut.set_result, self.answer)
        return True

    def send_action(self, device_id, action, payload):
        self.plain.append(action)
        return "plain"


def test_slow_actions_wait_longer_than_the_node_default(monkeypatch):
    import asyncio

    from pc_client import client

    calls = []

    class Node:
        async def _answer(self, timeout):
            return {"success": True, "timeout": timeout}

        def send_action(self, device_id, action, payload, timeout=None):
            calls.append((device_id, action, payload, timeout))
            return self._answer(timeout)

    node = Node()
    assert asyncio.run(aw.server_action_request(node, "srv", "network", {}))["timeout"] is None, "quick: default wait"
    res = asyncio.run(aw.server_action_request(node, "srv", "apply_updates", {"security_only": True}))
    assert res["timeout"] == aw.SERVER_ACTION_WAIT_SEC["apply_updates"] >= 1800
    assert calls[-1][:3] == ("srv", "apply_updates", {"security_only": True})
    assert aw.SERVER_ACTION_WAIT_SEC["sys_updates"] >= 240

    class OldNode:  # no timeout argument: still works, with the default wait
        def send_action(self, device_id, action, payload):
            async def answer():
                return {"success": True}
            return answer()

    assert asyncio.run(aw.server_action_request(OldNode(), "srv", "storage", {}))["success"]

    # The real client honours the timeout: nobody answers -> TIMEOUT after the short wait, offline -> OFFLINE.
    monkeypatch.setattr(client, "COMMAND_TIMEOUT_SEC", 0.01)
    real = client.PCClientNode.__new__(client.PCClientNode)
    real.connected, real._pending = True, {}

    async def fake_send(message):
        return True

    real._send = fake_send
    res = asyncio.run(real.send_action("srv", "storage", {}, timeout=0.05))
    assert res["error"] == "TIMEOUT" and not real._pending
    real.connected = False
    assert asyncio.run(real.send_action("srv", "storage", {}, timeout=0.05))["error"] == "OFFLINE"


def _history(n=30, step=60.0, start=1_700_000_000.0):
    points = []
    for i in range(n):
        points.append({"t": start + i * step, "cpu": 10.0 + i, "ram": 50.0, "disk": 70.0,
                       "swap": None if i < 3 else 2.0, "load": 0.5,
                       "rx_kbps": None if i == 0 else 800.0 + i, "tx_kbps": None if i == 0 else 120.0})
    points[10]["cpu"] = None
    return {"success": True, "hours": 24, "points": points}


def _sized(chart, w=480, h=130):
    chart.winfo_width = lambda: w   # the root is withdrawn: give the canvas a size to draw into
    chart.winfo_height = lambda: h
    chart.render()


def test_history_tab_graphs(page):
    app = page.app
    page.on_show()
    app.calls.clear()
    page.show_tab("History")
    fn, args, on_done, on_error = app.calls.pop()
    assert fn is hub_json and args == ("GET", "/api/v1/server/history?hours=24&points=240", None, 30.0)
    assert page.g_status.cget("text") == "Loading…" and str(page.g_refresh.cget("state")) == "disabled"
    on_done(_history())
    cpu = page.g_charts["cpu"]
    assert cpu["keys"][0][2].cget("text") == "39%"
    assert cpu["stats"].cget("text") == "min 10%  ·  avg 25%  ·  max 39%"
    assert page.g_charts["swap"]["keys"][0][2].cget("text") == "2%"
    net = page.g_charts["net"]
    assert net["keys"][0][2].cget("text") == "↓ 829 kbit/s" and net["keys"][1][2].cget("text") == "↑ 120 kbit/s"
    assert net["stats"].cget("text").startswith("↓ min 801 kbit/s")
    assert page.g_status.cget("text").startswith("Updated")

    # Drawing and the hover read-out.
    chart = cpu["chart"]
    _sized(chart)
    assert len(chart.find_all()) > 10 and chart._geom is not None
    chart._motion(type("E", (), {"x": 470})())
    assert "CPU 39%" in chart.hover_text and chart.find_withtag("hover")
    chart._hide_hover()
    assert not chart.find_withtag("hover") and chart.hover_text == ""
    chart._motion(type("E", (), {"x": 100})())  # 30 minutes of samples on a 24 h axis: nothing out there
    assert chart.hover_text == "" and not chart.find_withtag("hover")
    _sized(net["chart"])
    net["chart"]._motion(type("E", (), {"x": 468})())
    assert "↓ 82" in net["chart"].hover_text and "↑ 120 kbit/s" in net["chart"].hover_text

    # Another range: a new read; a failure keeps the graphs and says why.
    page._graph_range_changed("7 d")
    fn, args, on_done, on_error = app.calls.pop()
    assert args[1] == "/api/v1/server/history?hours=168&points=240"
    on_error(HubError("Not Found", status=404))
    assert "update the hub" in page.g_status.cget("text") and page._graph_points
    # Every minute while it's on screen.
    page._graph_at -= aw.SERVER_GRAPH_REFRESH_SEC
    page.tick()
    assert app.calls and app.calls[-1][1][1].startswith("/api/v1/server/history?hours=168")
    app.calls.pop()[2]({"success": True, "hours": 168, "points": []})
    assert "No history yet" in cpu["chart"].empty_text and cpu["keys"][0][2].cget("text") == "—"


def _admin_results():
    return {
        "sys_updates": {"success": True,
                        "packages": [{"name": "openssl-libs.x86_64", "version": "1:3.0.8-1.amzn2023",
                                      "repo": "amazonlinux"},
                                     {"name": "kernel.x86_64", "version": "6.1.100-1", "repo": "amazonlinux"}],
                        "security_count": 1, "newer_release": "2023.6.20241010",
                        "release_note": "Version 2023.6.20241010:\n  Run: dnf upgrade --releasever=2023.6.20241010",
                        "reboot_needed": False, "kernel": "6.1.90-99.173.amzn2023.x86_64",
                        "message": "2 package updates available (1 security). A newer release is available."},
        "apply_updates": {"success": True, "output": "Upgraded:\n  kernel-6.1.100-1\nComplete!",
                          "reboot_needed": True, "message": "Updates installed. A reboot is needed to finish."},
        "reboot": {"success": True, "message": "The server will reboot in 1 minute; Willy reconnects when it's back."},
        "cancel_reboot": {"success": True, "message": "Reboot cancelled."},
        "storage": {"success": True,
                    "mounts": [{"mount": "/", "device": "/dev/nvme0n1p1", "fs": "xfs", "used_gb": 70.2,
                                "total_gb": 80.0, "free_gb": 9.8, "pct": 91.0}],
                    "biggest": [{"path": "/var/lib/docker", "gb": 20.5}, {"path": "/home/ec2-user/.pm2", "gb": 1.2}],
                    "cleanable": {"journal": 0.8, "docker": "Images: 4.1GB (60%); Build Cache: 1GB", "pm2_logs": 0.0,
                                  "dnf_cache": 0.3, "trash": 0.0},
                    "cleanable_labels": {"journal": "System logs (journald)"},
                    "swap": {"used_gb": 0.2, "total_gb": 2.0, "pct": 10.0}, "message": "Disk / is 91% full."},
        "cleanup": {"success": True, "output": "Total reclaimed space: 4GB", "message": "Cleaned up: Docker."},
        "network": {"success": True,
                    "interfaces": [{"interface": "ens5", "state": "UP", "addresses": ["172.31.5.10/20", "fe80::1/64"]}],
                    "listening": [{"proto": "tcp", "address": "0.0.0.0", "port": "443", "public": True,
                                   "process": "nginx", "pid": 812},
                                  {"proto": "tcp", "address": "127.0.0.1", "port": "8000", "public": False,
                                   "process": "python3", "pid": 900}],
                    "rate": {"down_kbps": 1234.5, "up_kbps": 88.0}, "established": 12, "message": "ok"},
        "security": {"success": True, "logged_in": ["ec2-user pts/0 2026-10-02 09:00 (1.2.3.4)"],
                     "recent_logins": ["ec2-user pts/0 1.2.3.4 Thu Oct  2 09:00   still logged in"],
                     "failed_ssh_24h": 1532, "top_attackers": [{"ip": "45.1.2.3", "attempts": 900}],
                     "ssh_password_login": "yes", "root_login": "prohibit-password", "fail2ban": "not installed",
                     "message": "1532 failed SSH logins in 24 h"},
        "services_list": {"success": True, "services": [
            {"name": "nginx", "load": "loaded", "active": "active", "sub": "running", "description": "web server",
             "boot": "enabled"},
            {"name": "backup", "load": "loaded", "active": "failed", "sub": "failed", "description": "nightly backup",
             "boot": "disabled"},
            {"name": "systemd-journald", "load": "loaded", "active": "active", "sub": "running",
             "description": "Journal", "boot": "static"}], "message": "2 running"},
        "timers": {"success": True, "cron": ["*/5 * * * * /home/ec2-user/check.sh"],
                   "timers": [{"timer": "dnf-makecache.timer",
                               "line": "Thu 2026-10-02 10:00 UTC 40min left - - dnf-makecache.timer"}],
                   "message": "1 cron"},
        "journal": lambda p: {"success": True, "logs": "2026-10-02T09:00 nginx[1]: started\n"
                                                       "2026-10-02T09:01 backup[2]: Error: disk full\n"
                                                       "2026-10-02T09:02 kernel: warning: low memory\n"},
        "control": {"success": True, "message": "Restarted nginx."},
        "service_boot": {"success": True, "message": "nginx will not start at boot."},
    }


def _text(widget):
    return widget.get("1.0", "end")


def _labels(box):
    return [w.cget("text") for w in box.winfo_children() if isinstance(w, aw.tk.Label)]


def test_system_tab_sections(page, monkeypatch):
    monkeypatch.setattr(aw.ServerTextDialog, "open", lambda self: None)
    app, node = page.app, page.app.node
    node.results.update(_admin_results())
    dev = _server_dev()
    node.devices = {dev["device_id"]: dev}
    page.on_show()
    page.sync_device()
    app.calls.clear()

    # Updates: read when the section is first shown.
    page.show_tab("System")
    assert node.sent[-1][1:] == ("sys_updates", {})
    assert page.u_summary.cget("text").startswith("2 package updates available")
    assert page.u_count.cget("text") == "2 updates" and page.u_security.cget("text") == "1 security"
    assert page.u_reboot.cget("text") == "No reboot needed" and "6.1.90" in page.u_kernel.cget("text")
    assert "2023.6.20241010" in page.u_release.cget("text") and page.u_release.winfo_manager() == "pack"
    assert "openssl-libs.x86_64" in _text(page.u_list) and page.u_pk_note.cget("text") == "2 packages"
    assert str(page.u_all_btn.cget("state")) == "normal" and str(page.u_sec_btn.cget("state")) == "normal"
    assert page.s_status.cget("text").startswith("Updated")

    page.apply_updates(False)
    heading, message, confirm = app.asked.pop()
    assert heading == "Install all updates?" and "all 2 updates" in message
    confirm()
    assert ("server_vps_1", "apply_updates", {"security_only": False}) in node.sent
    assert app.toasts[-1] == ("Updates", "Updates installed. A reboot is needed to finish.", "success")
    assert node.sent[-1][1] == "sys_updates", "re-checked after installing"
    assert "Complete!" in _text(page.u_out) and page.u_out_card.winfo_manager() == "pack"
    assert page.u_reboot.cget("text") == "No reboot needed", "the fresh check wins over the install's answer"

    page.reboot_server()
    heading, message, confirm = app.asked.pop()
    assert heading == "Reboot the server?" and "EVERY website" in message and "Cancel reboot" in message
    confirm()
    assert node.sent[-1][1:] == ("reboot", {}) and page._reboot_at is not None
    assert "restarts in" in page.r_text.cget("text")
    page.cancel_reboot()
    assert node.sent[-1][1:] == ("cancel_reboot", {}) and page._reboot_at is None
    assert app.toasts[-1] == ("Reboot", "Reboot cancelled.", "success")

    # Storage: disks, cleanups (asked first; Docker never touches volumes), biggest folders.
    page.show_section("Storage")
    assert node.sent[-1][1:] == ("storage", {})
    assert page.st_note.cget("text") == "/ has 9.8 GB free"
    texts = _labels(page.st_disks)
    assert "91%" in texts and any("70.2 GB of 80.0 GB" in t for t in texts) and "10%" in texts
    clean_buttons = [w for w in page.st_clean.winfo_children() if isinstance(w, aw.ctk.CTkButton)]
    assert [str(b.cget("state")) for b in clean_buttons] == ["normal", "normal", "disabled", "normal", "disabled"]
    page.clean("docker")
    heading, message, confirm = app.asked.pop()
    assert "Docker" in heading and "Volumes are never touched" in message
    confirm()
    assert ("server_vps_1", "cleanup", {"what": "docker"}) in node.sent and node.sent[-1][1] == "storage"
    assert app.toasts[-1] == ("Clean up", "Cleaned up: Docker.", "success")
    big = _labels(page.st_big)
    assert "/var/lib/docker" in big and "20.5 GB" in big

    # Network & security: public ports in amber, the SSH summary, and the live rate re-read.
    page.show_section("Network & security")
    assert [s[1] for s in node.sent[-2:]] == ["network", "security"]
    assert page.n_down.cget("text") == "↓ 1.2 Mbit/s" and page.n_conns.cget("text") == "12 active connections"
    assert page.n_ports_note.cget("text") == "1 open to the internet · 2 in all"
    port = next(w for w in page.n_ports.winfo_children() if isinstance(w, aw.tk.Label) and w.cget("text") == "443")
    assert port.cget("fg") == AMBER
    assert page.sec_tiles["SSH password login"].cget("text") == "On"
    assert page.sec_tiles["Failed SSH logins · 24 h"].cget("text") == "1,532"
    assert page.sec_tiles["Root login over SSH"].cget("text") == "Keys only"
    assert any("45.1.2.3" in t and "900 attempts" in t for t in _labels(page.sec_box))
    sent = len(node.sent)
    page._sys["network"]["asked"] -= aw.SERVER_NETWORK_REFRESH_SEC
    page.tick()
    assert len(node.sent) == sent + 1 and node.sent[-1][1] == "network"

    # Services & jobs: failed first in red; filter; controls and the boot switch on the selection.
    page.show_section("Services & jobs")
    assert [s[1] for s in node.sent[-2:]] == ["services_list", "timers"]
    assert page.sv_list.size() == 3 and page.sv_list.get(0).startswith("backup")
    assert page.sv_list.itemcget(0, "fg") == aw.SOFT_RED
    assert page.sv_note.cget("text") == "2 running · 1 failed · 3 in all"
    assert str(page.sv_buttons["restart"].cget("state")) == "disabled", "nothing selected"
    page.sv_filter.insert(0, "ngin")
    page._render_svc_list()
    assert page.sv_list.size() == 1
    page.sv_list.selection_set(0)
    page._svc_select()
    assert page.sv_selected.cget("text").startswith("nginx · active")
    assert str(page.sv_buttons["stop"].cget("state")) == "normal"
    assert str(page.sv_buttons["start"].cget("state")) == "disabled"
    assert page.sv_boot.get() == 1 and str(page.sv_boot.cget("state")) == "normal"
    page._svc_do("restart")
    heading, _message, confirm = app.asked.pop()
    assert heading == "Restart nginx?"
    confirm()
    assert ("server_vps_1", "control", {"kind": "service", "name": "nginx", "action": "restart"}) in node.sent
    assert node.sent[-1][1] == "services_list", "the list is re-read after a control"
    page.sv_boot.toggle()  # a click: asks first, the switch stays put until the server agrees
    assert page.sv_boot.get() == 1
    heading, message, confirm = app.asked.pop()
    assert heading == "Stop starting nginx at boot?" and "Careful" in message
    confirm()
    assert ("server_vps_1", "service_boot", {"name": "nginx", "enable": False}) in node.sent
    assert app.toasts[-1] == ("nginx at boot", "nginx will not start at boot.", "success")
    page.sv_filter.delete(0, "end")
    page.sv_filter.insert(0, "journald")
    page._render_svc_list()
    page.sv_list.selection_set(0)
    page._svc_select()
    assert str(page.sv_boot.cget("state")) == "disabled" and "static" in page.sv_boot.cget("text")
    jobs = _text(page.tm_text)
    assert "check.sh" in jobs and "dnf-makecache.timer" in jobs and page.tm_note.cget("text") == "1 cron job · 1 timer"

    # Logs: the unit list comes from the services; filters go to the journal action.
    page.show_section("Logs")
    assert "nginx" in page.lg_unit.cget("values") and page.lg_unit.cget("values")[0] == aw.SERVER_ALL_UNITS
    assert node.sent[-1][1:] == ("journal", {"lines": 200, "since": "1 hour ago"})
    assert "disk full" in _text(page.lg_text)
    assert "err" in page.lg_text.tag_names("2.30") and "warn" in page.lg_text.tag_names("3.30")
    assert page.lg_status.cget("text").startswith("3 lines · all units · since 1 hour ago")
    page.lg_unit.set("nginx")
    page.lg_prio.set("err")
    page.lg_search.insert(0, "boom")
    page.logs_refresh()
    assert node.sent[-1][1:] == ("journal", {"lines": 200, "unit": "nginx", "priority": "err",
                                             "since": "1 hour ago", "grep": "boom"})
    sent = len(node.sent)
    page.lg_since.set("$(reboot)")
    page.logs_refresh()
    assert len(node.sent) == sent and app.toasts[-1][2] == "error"
    node.results["journal"] = {"success": False, "error": "TIMEOUT"}
    page.lg_since.set("today")
    page.logs_refresh()
    assert "didn't answer" in page.lg_status.cget("text") and "disk full" in _text(page.lg_text), "old lines stay"


def test_system_tab_waits_for_the_agent(page):
    app, node = page.app, page.app.node
    node.results.update(_admin_results())
    dev = _server_dev(online=False)
    node.devices = {dev["device_id"]: dev}
    page.on_show()
    page.sync_device()
    page.show_tab("System")
    assert not node.sent, "nothing is asked of an offline agent"
    assert "offline" in page.s_hint.cget("text") and page.s_hint.winfo_manager() == "grid"
    assert str(page.u_check.cget("state")) == "disabled" and str(page.s_refresh.cget("state")) == "disabled"
    assert page.u_summary.cget("text") == "Press Check now to look for operating-system updates."
    page.section_refresh()
    assert not node.sent and app.toasts[-1][2] == "error"
    # The agent comes back: the open section reads at once.
    node.devices = {dev["device_id"]: {**dev, "online": True}}
    page.sync_device()
    assert node.sent and node.sent[-1][1] == "sys_updates" and not page.s_hint.winfo_manager()
    # A slow check that times out says how long it waited.
    node.results["storage"] = {"success": False, "error": "TIMEOUT", "reply": "Timed out waiting for response."}
    page.show_section("Storage")
    assert "didn't answer within 2 minutes" in page.s_status.cget("text")
