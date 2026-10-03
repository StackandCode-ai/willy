"""
Tests for the realtime hub: fast-path router, time parsing, reminder scheduler,
device manager (reconnect races, fast failure, telemetry fan-out), activity log,
multi-tool orchestration, and an end-to-end command round-trip over WebSockets.
"""

import sys
import time
import asyncio
import datetime as dt
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest
from fastapi.testclient import TestClient

from server import timeutil
from server.fast_router import match_fast_intent, telemetry_reply, normalize
from server.device_manager import DeviceManager
from server.activity_log import ActivityLog
from server.scheduler import ReminderScheduler
from server.orchestrator import ServerOrchestrator, clean_reply, compact_tool_result


# ---------------------------------------------------------------- fast router

@pytest.mark.parametrize("text,tool,args", [
    ("lock my pc", "power_action", {"action": "lock"}),
    ("Hey Willy, lock the computer please", "power_action", {"action": "lock"}),
    ("lock", "power_action", {"action": "lock"}),
    ("put my laptop to sleep", "power_action", {"action": "sleep"}),
    ("set volume to 50%", "volume_control", {"action": "set", "level": 50}),
    ("volume 30", "volume_control", {"action": "set", "level": 30}),
    ("turn the volume to 75 percent", "volume_control", {"action": "set", "level": 75}),
    ("40% volume", "volume_control", {"action": "set", "level": 40}),
    ("mute", "volume_control", {"action": "mute"}),
    ("unmute the sound", "volume_control", {"action": "unmute"}),
    ("turn the volume up", "volume_control", {"action": "up"}),
    ("lower the volume", "volume_control", {"action": "down"}),
    ("set brightness to 70", "set_brightness", {"level": 70}),
    ("brightness 40%", "set_brightness", {"level": 40}),
    ("increase the brightness", "set_brightness", {"change": 20}),
    ("make the screen brighter", "set_brightness", {"change": 20}),
    ("full brightness", "set_brightness", {"level": 100}),
    ("dim the screen", "set_brightness", {"change": -20}),
    ("lower brightness", "set_brightness", {"change": -20}),
    ("pause the music", "media_control", {"action": "play_pause"}),
    ("next song", "media_control", {"action": "next"}),
    ("previous track", "media_control", {"action": "prev"}),
    ("take a screenshot", "take_screenshot", {}),
    ("show desktop", "window_action", {"action": "minimize_all"}),
    ("minimize all windows", "window_action", {"action": "minimize_all"}),
    ("open chrome", "launch_application", {"target": "chrome"}),
    ("can you open vs code", "launch_application", {"target": "code"}),
    ("launch the calculator app", "launch_application", {"target": "calculator"}),
    ("open youtube", "open_url", {"url": "https://www.youtube.com"}),
    ("open github.com", "open_url", {"url": "https://github.com"}),
])
def test_fast_router_pc_commands(text, tool, args):
    intent = match_fast_intent(text)
    assert intent is not None, text
    assert intent.tool == tool
    assert intent.args == args


@pytest.mark.parametrize("text,metric,device", [
    ("what's my battery", "battery", "pc"),
    ("What is the laptop battery percentage?", "battery", "pc"),
    ("how much battery do i have", "battery", "pc"),
    ("phone battery", "battery", "mobile"),
    ("cpu usage", "cpu", "pc"),
    ("what's the ram usage on my laptop", "ram", "pc"),
    ("pc status", "status", "pc"),
    ("how's my laptop doing", "status", "pc"),
])
def test_fast_router_telemetry_questions(text, metric, device):
    intent = match_fast_intent(text)
    assert intent is not None, text
    assert intent.tool == "telemetry"
    assert intent.args == {"metric": metric, "device": device}


@pytest.mark.parametrize("text", [
    "open chrome and search for flights to delhi",
    "play despacito on youtube",
    "lock the door",
    "sleep",
    "set volume to 150",
    "set brightness to 150",
    "decrease",
    "lower the",
    "open the pod bay doors",
    "what's the weather tomorrow",
    "remind me to call mom at 5",  # AM or PM? the LLM can ask
    "shutdown my pc",
    "restart the computer",
    "",
])
def test_fast_router_leaves_everything_else_to_llm(text):
    assert match_fast_intent(text) is None


def test_fast_router_clock_intents():
    assert match_fast_intent("what time is it").tool == "clock"
    assert match_fast_intent("what's the date today").args == {"what": "date"}


def test_normalize_strips_wake_word_and_politeness():
    assert normalize("Hey Willy, please mute the volume now!") == "mute the volume"


def test_telemetry_reply_formats():
    pc = {"name": "HariG", "telemetry": {
        "battery_pct": 81, "is_charging": False, "battery_secs_left": 5400,
        "cpu_pct": 23.4, "ram_pct": 64, "ram_used_gb": 10.2, "ram_total_gb": 16,
        "top_processes": [{"name": "chrome.exe", "cpu": 12.0}], "disk_free_gb": 120.5,
    }}
    assert telemetry_reply("battery", pc) == "HariG is at 81 percent, on battery, about 1 hour 30 minutes left."
    assert "23 percent" in telemetry_reply("cpu", pc) and "chrome.exe" in telemetry_reply("cpu", pc)
    assert "10.2 of 16 gigabytes" in telemetry_reply("ram", pc)
    assert telemetry_reply("status", pc).startswith("HariG is online: battery 81 percent")
    desktop = {"name": "Tower", "telemetry": {"battery_pct": None}}
    assert "AC power" in telemetry_reply("battery", desktop)
    assert telemetry_reply("battery", None) is None


# ------------------------------------------------------------------ time utils

@pytest.mark.parametrize("text,expected", [
    ("11:00 AM", (11, 0)), ("2:30 pm", (14, 30)), ("17:00", (17, 0)), ("7am", (7, 0)),
    ("12 AM", (0, 0)), ("12:15 PM", (12, 15)), ("noon", (12, 0)), ("7.45 p.m.", (19, 45)),
    ("25:00", None), ("13 PM", None), ("soon", None), ("", None), (None, None),
])
def test_parse_clock(text, expected):
    assert timeutil.parse_clock(text) == expected


def test_parse_date_relative():
    today = dt.date(2026, 9, 28)  # a Monday
    assert timeutil.parse_date("tomorrow", today) == dt.date(2026, 9, 29)
    assert timeutil.parse_date("2026-10-02", today) == dt.date(2026, 10, 2)
    assert timeutil.parse_date("friday", today) == dt.date(2026, 10, 2)
    assert timeutil.parse_date("monday", today) == dt.date(2026, 10, 5)  # next week, not today
    assert timeutil.parse_date(None, today) == today


def test_format_clock_matches_app_style():
    assert timeutil.format_clock(7, 0) == "07:00 AM"
    assert timeutil.format_clock(17, 5) == "05:05 PM"
    assert timeutil.format_clock(0, 30) == "12:30 AM"


# ------------------------------------------------------------------- scheduler

class _FakeMorning:
    def __init__(self, config):
        self.config = config
        self.saves = 0

    def save_config(self):
        self.saves += 1


def _run(coro):
    return asyncio.run(coro)


def test_scheduler_fires_due_reminder_once():
    tz = dt.timezone(dt.timedelta(hours=5, minutes=30))
    now = dt.datetime(2026, 9, 28, 17, 0, 30, tzinfo=tz)
    morning = _FakeMorning({
        "call_enabled": False,
        "reminders": [
            {"id": "a", "text": "Call mom", "time": "05:00 PM", "date": "2026-09-28"},
            {"id": "b", "text": "Later", "time": "06:00 PM", "date": "2026-09-28"},
            {"id": "c", "text": "Done already", "time": "04:00 PM", "date": "2026-09-28", "completed": True},
            {"id": "d", "text": "Ancient", "time": "08:00 AM", "date": "2026-09-20"},
        ],
        "alarms": [],
    })
    sent = []

    async def broadcast(event):
        sent.append(event)

    sched = ReminderScheduler(morning, broadcast)
    events = _run(sched.tick(now))
    assert [e["message"] for e in events] == ["Call mom"]
    assert sent[:len(events)] == events
    assert sent[-1]["type"] == "reminders_changed"  # open lists refresh without refetching
    reminders = {r["id"]: r for r in morning.config["reminders"]}
    assert reminders["a"].get("fired_at") and not reminders["b"].get("fired_at")
    assert reminders["d"].get("missed") is True  # too old: marked missed, not rung
    assert _run(sched.tick(now)) == []            # never fires twice


def test_scheduler_alarm_window_and_creation_guard():
    tz = dt.timezone.utc
    now = dt.datetime(2026, 9, 28, 7, 2, tzinfo=tz)
    seven = dt.datetime(2026, 9, 28, 7, 0, tzinfo=tz).timestamp()
    morning = _FakeMorning({
        "call_enabled": True,
        "call_time": "07:00",
        "last_call_date": "",
        "reminders": [],
        "alarms": [
            {"id": "old", "time": "07:00 AM", "label": "Wake up", "enabled": True, "created_at": seven - 86400},
            {"id": "new", "time": "07:00 AM", "label": "Too late today", "enabled": True, "created_at": seven + 60},
            {"id": "off", "time": "07:00 AM", "label": "Disabled", "enabled": False, "created_at": 0},
        ],
    })
    events = _run(ReminderScheduler(morning, AsyncMock()).tick(now))
    kinds = sorted((e["type"], e.get("message")) for e in events)
    assert kinds == [("morning_call_due", "Your daily Willy briefing is ready."), ("reminder_due", "Wake up")]
    later = now + dt.timedelta(minutes=10)
    morning.config["alarms"][0]["last_fired_date"] = ""
    assert _run(ReminderScheduler(morning, AsyncMock()).tick(later)) == []  # outside 5-min window


# -------------------------------------------------------------- device manager

def test_reconnect_race_keeps_new_connection_online():
    async def scenario():
        mgr = DeviceManager()
        old_ws, new_ws = AsyncMock(), AsyncMock()
        await mgr.register_device(old_ws, "pc_1", "pc", "Laptop")
        await mgr.register_device(new_ws, "pc_1", "pc", "Laptop")
        # The stale socket's handler exits late and tries to unregister.
        await mgr.unregister_device("pc_1", old_ws)
        assert mgr.active_sockets["pc_1"] is new_ws
        assert mgr.devices["pc_1"].status == "online"
        await asyncio.sleep(0)  # let the background close run
        old_ws.close.assert_awaited()
    _run(scenario())


def test_pending_command_fails_fast_when_device_drops():
    async def scenario():
        mgr = DeviceManager(command_timeout=10)
        ws = AsyncMock()
        await mgr.register_device(ws, "pc_1", "pc", "Laptop")
        t0 = time.perf_counter()
        task = asyncio.create_task(mgr.send_to_device("pc_1", "take_screenshot", {}))
        await asyncio.sleep(0.05)
        await mgr.unregister_device("pc_1", ws)
        res = await task
        assert res["success"] is False and res["error"] == "DEVICE_OFFLINE"
        assert time.perf_counter() - t0 < 1.0  # no 10 s timeout wait
    _run(scenario())


def test_heartbeat_records_history_and_notifies_observers():
    async def scenario():
        mgr = DeviceManager()
        pc_ws, observer = AsyncMock(), AsyncMock()
        mgr.add_observer(observer)
        dev = await mgr.register_device(pc_ws, "pc_1", "pc", "Laptop")
        await mgr.handle_heartbeat(dev, {"cpu_pct": 12.5, "ram_pct": 40, "bogus_key": "x",
                                         "top_processes": [{"name": f"p{i}"} for i in range(20)]})
        await asyncio.sleep(0.05)
        payloads = [c.args[0] for c in observer.send_json.await_args_list]
        assert [p["type"] for p in payloads] == ["device_discovered", "device_update"]
        device = payloads[-1]["device"]
        assert device["telemetry"]["cpu_pct"] == 12.5
        assert "bogus_key" not in device["telemetry"]
        assert len(device["telemetry"]["top_processes"]) == 8
        assert device["history"]["cpu"] == [12.5]
    _run(scenario())


def test_stale_device_goes_offline_and_is_announced():
    async def scenario():
        mgr = DeviceManager(stale_after_sec=5)
        observer = AsyncMock()
        mgr.add_observer(observer)
        dev = await mgr.register_device(AsyncMock(), "pc_1", "pc", "Laptop")
        dev.last_seen -= 30
        await mgr.sweep_stale()
        assert dev.status == "offline"
        assert observer.send_json.await_args_list[-1].args[0]["type"] == "device_offline"
        assert mgr.get_first_online_pc() is None
    _run(scenario())


def test_cleanly_disconnected_pc_stays_offline():
    async def scenario():
        mgr = DeviceManager()
        ws = AsyncMock()
        await mgr.register_device(ws, "pc_1", "pc", "Laptop")
        await mgr.unregister_device("pc_1", ws)
        # Its last heartbeat is seconds old, but the socket is gone: it must not look online.
        assert mgr.get_all_devices()[0]["online"] is False
        assert mgr.get_first_online_pc() is None
        # A phone that only reports over HTTP keeps its grace window.
        mgr.upsert_http_device("mobile_1", "mobile", "Phone", telemetry={"battery_pct": 50})
        phone = next(d for d in mgr.get_all_devices() if d["device_id"] == "mobile_1")
        assert phone["online"] is True and phone["connection"] == "http"
    _run(scenario())


def test_offline_is_announced_even_if_a_listing_noticed_it_first():
    async def scenario():
        mgr = DeviceManager(stale_after_sec=5)
        observer = AsyncMock()
        mgr.add_observer(observer)
        dev = await mgr.register_device(AsyncMock(), "pc_1", "pc", "Laptop")
        dev.last_seen -= 30
        assert mgr.get_all_devices()[0]["online"] is False  # e.g. a REST listing flips it first
        await mgr.sweep_stale()
        sent = [c.args[0]["type"] for c in observer.send_json.await_args_list]
        assert sent.count("device_offline") == 1
        await mgr.sweep_stale()  # announced once, not on every sweep
        sent = [c.args[0]["type"] for c in observer.send_json.await_args_list]
        assert sent.count("device_offline") == 1
    _run(scenario())


def test_live_context_summarises_online_devices():
    async def scenario():
        mgr = DeviceManager()
        dev = await mgr.register_device(AsyncMock(), "pc_1", "pc", "HariG")
        dev.update_telemetry({"battery_pct": 80, "is_charging": True, "cpu_pct": 20, "active_window": "Claude"})
        text = mgr.live_context()
        assert "Windows PC 'HariG'" in text and "battery 80%, charging" in text and "'Claude'" in text
    _run(scenario())


# ---------------------------------------------------------------- activity log

def test_activity_log_lifecycle_and_persistence(tmp_path):
    path = tmp_path / "activity.json"
    log = ActivityLog(path=path, maxlen=5)
    seen = []
    log.listeners.append(seen.append)
    entry = log.start("mobile", "lock my pc", "pc_1")
    log.finish(entry["id"], reply="Locked.", success=True, fast_path=True, latency_ms=120)
    failed = log.start("dashboard", "open foo")
    log.finish(failed["id"], reply="nope", success=False, latency_ms=900)
    assert [s["status"] for s in seen] == ["running", "done", "running", "error"]
    stats = log.stats()
    assert stats["total"] == 2 and stats["failed"] == 1 and stats["fast_path"] == 1
    assert stats["fast_path_p50_ms"] == 120
    reloaded = ActivityLog(path=path, maxlen=5)
    assert [e["query"] for e in reloaded.recent(10)] == ["open foo", "lock my pc"]


# ---------------------------------------------------------------- orchestrator

def _tool_call(call_id, name, args):
    return SimpleNamespace(id=call_id, type="function",
                           function=SimpleNamespace(name=name, arguments=args))


def _completion(content=None, tool_calls=None):
    msg = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


def test_orchestrator_runs_parallel_tools_and_keeps_sessions_apart():
    async def scenario():
        orch = ServerOrchestrator()
        create = AsyncMock(side_effect=[
            _completion(tool_calls=[
                _tool_call("c1", "launch_application", '{"target": "chrome"}'),
                _tool_call("c2", "volume_control", '{"action": "set", "level": 30}'),
            ]),
            _completion(content="<think>plan</think>Opened Chrome and set volume to 30."),
            _completion(content="Hi from the other session."),
        ])
        orch.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

        started = []

        async def executor(name, args):
            started.append(name)
            await asyncio.sleep(0.1)
            return {"success": True, "name": name}

        t0 = time.perf_counter()
        res = await orch.process_query("open chrome and set volume 30", executor, pc_online=True, session_id="mobile")
        assert time.perf_counter() - t0 < 0.19  # both tools ran concurrently
        assert res["reply"] == "Opened Chrome and set volume to 30."
        assert [t["name"] for t in res["tools"]] == ["launch_application", "volume_control"]
        assert sorted(started) == ["launch_application", "volume_control"]

        await orch.process_query("hello", executor, pc_online=True, session_id="dashboard")
        third_call_messages = create.await_args_list[2].kwargs["messages"]
        assert not any("chrome" in str(m.get("content", "")).lower() for m in third_call_messages[1:])
    _run(scenario())


def test_orchestrator_reports_offline_pc_without_running_tools():
    async def scenario():
        orch = ServerOrchestrator()
        create = AsyncMock(return_value=_completion(tool_calls=[_tool_call("c1", "power_action", '{"action": "lock"}')]))
        orch.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        res = await orch.process_query("lock it", None, pc_online=False, session_id="s")
        assert res["success"] is False and "offline" in res["reply"]
    _run(scenario())


def test_reply_cleanup_and_tool_result_compaction():
    assert clean_reply("<think>long reasoning</think> **Done**, locked.") == "Done, locked."
    assert clean_reply("Sure.<think>unfinished") == "Sure."
    compact = compact_tool_result({"success": True, "image_base64": "A" * 50000, "items": list(range(100))})
    assert "AAAA" not in compact and len(compact) < 3700


# ------------------------------------------------- end-to-end over WebSockets

def test_fast_path_command_round_trip_through_a_connected_pc():
    import server.app as hub
    token = hub.DEFAULT_TOKEN
    headers = {"Authorization": f"Bearer {token}", "X-Willy-Client": "mobile"}

    # `with` shares one event loop between the HTTP requests and the fake PC socket,
    # like a real server (and runs the lifespan background tasks).
    with TestClient(hub.app) as client, client.websocket_connect(
        f"/ws/devices?token={token}&device_id=pc_e2e&device_type=pc&name=E2E-PC"
    ) as pc:
        watchdog = threading.Timer(20, pc.close)  # fail instead of hanging forever
        watchdog.start()
        assert pc.receive_json()["type"] == "snapshot"
        pc.send_json({"type": "heartbeat", "ts": 123.0, "telemetry": {"battery_pct": 64, "is_charging": True}})
        ack = pc.receive_json()
        while ack.get("type") != "heartbeat_ack":
            ack = pc.receive_json()
        assert ack["client_ts"] == 123.0

        result = {}
        worker = threading.Thread(target=lambda: result.update(
            client.post("/api/v1/command", headers=headers,
                        json={"query": "lock my pc", "target_device_id": "pc_e2e"}).json()))
        worker.start()

        msg = pc.receive_json()
        while "req_id" not in msg:
            msg = pc.receive_json()
        assert msg["action"] == "power_action" and msg["payload"] == {"action": "lock"}
        pc.send_json({"req_id": msg["req_id"], "result": {"success": True, "message": "Locked"}})
        worker.join(timeout=10)

        assert result["success"] is True
        assert result["fast_path"] is True
        assert result["reply"] == "Locked your PC."
        assert result["tools"][0]["name"] == "power_action"

        battery = client.post("/api/v1/command", headers=headers, json={"query": "what's my battery"}).json()
        assert battery["fast_path"] is True and "64 percent, charging" in battery["reply"]

        activity = client.get("/api/v1/activity", headers=headers).json()["activity"]
        assert activity[0]["query"] == "what's my battery" and activity[0]["source"] == "mobile"
        watchdog.cancel()


def test_events_socket_requires_token_and_sends_snapshot():
    import server.app as hub
    client = TestClient(hub.app)
    with client.websocket_connect(f"/ws/events?token={hub.DEFAULT_TOKEN}") as ws:
        snap = ws.receive_json()
        assert snap["type"] == "snapshot" and "devices" in snap and "server" in snap
        ws.send_json({"type": "ping", "ts": 5})
        assert ws.receive_json()["ts"] == 5
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect) as rejected:
        with client.websocket_connect("/ws/events?token=wrong") as ws:
            ws.receive_json()
    assert rejected.value.code == 4001  # browsers can tell a bad token from an outage


def test_status_questions_without_a_pc_skip_the_llm():
    import server.app as hub
    from server.device_manager import DeviceInfo
    client = TestClient(hub.app)
    headers = {"Authorization": f"Bearer {hub.DEFAULT_TOKEN}"}
    saved = dict(hub.device_manager.devices)
    hub.device_manager.devices.clear()
    try:
        assert hub.device_manager.get_first_online_pc() is None
        res = client.post("/api/v1/command", headers=headers, json={"query": "what's my battery"}).json()
        assert res["fast_path"] is True and "isn't connected" in res["reply"]
        assert "llm_ms" not in res["timings"]

        # A PC that was seen before: say since when and why it's offline, plus its last reading.
        pc = DeviceInfo("pc_known", "pc", "HariG")
        pc.update_telemetry({"battery_pct": 82, "is_charging": True})
        pc.status, pc.connection = "offline", "none"
        pc.offline_since, pc.offline_reason = time.time() - 600, "sleep"
        hub.device_manager.devices["pc_known"] = pc
        res = client.post("/api/v1/command", headers=headers, json={"query": "what's my battery"}).json()
        assert res["fast_path"] is True
        assert "HariG has been offline since" in res["reply"] and "went to sleep" in res["reply"]
        assert "was at 82 percent, charging when it was last online" in res["reply"]
        lock = client.post("/api/v1/command", headers=headers, json={"query": "lock my pc"}).json()
        assert lock["success"] is False and "went to sleep" in lock["reply"] and "tell you when it's back" in lock["reply"]
    finally:
        hub.device_manager.devices.clear()
        hub.device_manager.devices.update(saved)


def test_dashboard_pages_do_not_leak_the_token():
    import server.app as hub
    client = TestClient(hub.app)
    for path in ("/", "/dashboard", "/call"):
        html = client.get(path).text
        assert hub.DEFAULT_TOKEN not in html, path


def test_direct_action_allowlist():
    import server.app as hub
    client = TestClient(hub.app)
    headers = {"Authorization": f"Bearer {hub.DEFAULT_TOKEN}"}
    bad = client.post("/api/v1/devices/pc_x/action", headers=headers,
                      json={"action": "execute_powershell", "payload": {"command": "whoami"}})
    assert bad.status_code == 400
    offline = client.post("/api/v1/devices/pc_x/action", headers=headers, json={"action": "notify", "payload": {}})
    assert offline.json()["error"] == "DEVICE_OFFLINE"


# ------------------------------------------------------------------ token budget

def _names(defs):
    return {d["function"]["name"] for d in defs}


def test_tool_selection_sends_only_relevant_groups():
    from server.pc_tools_schema import select_tools, PC_TOOLS_DEFINITIONS, CORE_TOOLS
    core = _names(select_tools("close edge"))
    assert core == CORE_TOOLS & _names(PC_TOOLS_DEFINITIONS)
    assert "send_sms" in _names(select_tools("text mom I'll be late"))
    assert "set_timer" in _names(select_tools("set a timer for 5 minutes"))
    assert "watch_device" in _names(select_tools("tell me when my pc is back online"))
    assert "set_brightness" in _names(select_tools("dim the screen a bit"))
    # a follow-up keeps the tools of the exchange before it
    assert "send_sms" in _names(select_tools("yes", context="Text Mom: I'll be late. Send?"))
    # phone requests always get the phone tools; tools already used stay declared
    assert "phone_call" in _names(select_tools("hello", source="mobile"))
    assert "navigate" in _names(select_tools("hello", extra={"navigate"}))
    assert len(select_tools("close edge")) < len(PC_TOOLS_DEFINITIONS) / 2


@pytest.mark.parametrize("text,target", [
    ("close edge", "edge"), ("quit chrome", "chrome"), ("close the spotify app", "spotify"),
])
def test_fast_router_closes_known_apps(text, target):
    intent = match_fast_intent(text)
    assert intent is not None and intent.tool == "close_application" and intent.args == {"target": target}


@pytest.mark.parametrize("text", ["close explorer", "close the door", "close settings"])
def test_fast_router_never_closes_the_shell(text):
    intent = match_fast_intent(text)
    assert intent is None or intent.tool != "close_application"


def test_history_keeps_only_the_gist_of_tool_results():
    from server.orchestrator import ServerOrchestrator, _Session, HISTORY_TOOL_RESULT_MAX_CHARS
    session = _Session()
    ServerOrchestrator._commit(ServerOrchestrator.__new__(ServerOrchestrator), session, [
        {"role": "user", "content": "list processes"},
        {"role": "tool", "tool_call_id": "1", "name": "list_processes", "content": "x" * 5000},
    ])
    assert len(session.history[1]["content"]) <= HISTORY_TOOL_RESULT_MAX_CHARS + 20


def test_requests_are_trimmed_to_the_token_budget():
    from server.orchestrator import _fit_budget, _estimate_tokens, PROMPT_TOKEN_BUDGET
    from server.pc_tools_schema import PC_TOOLS_DEFINITIONS, CORE_TOOLS
    system = {"role": "system", "content": "x" * 6000}
    history = []
    for i in range(12):
        history += [{"role": "user", "content": f"question {i} " + "y" * 800},
                    {"role": "assistant", "content": "answer " + "z" * 800}]
    turn = [{"role": "user", "content": "delete the file D:/a.txt"}]
    kept, tools = _fit_budget(system, history, turn, PC_TOOLS_DEFINITIONS)
    total = _estimate_tokens(system) + _estimate_tokens(kept) + _estimate_tokens(turn) + _estimate_tokens(tools)
    assert total <= PROMPT_TOKEN_BUDGET and (not kept or kept[0]["role"] == "user")
    assert kept == history[-len(kept):] if kept else True  # the newest exchanges survive
    # a huge prompt keeps what this question needs (file tools) and drops unrelated ones
    _, tools = _fit_budget({"role": "system", "content": "x" * 18000}, [], turn, PC_TOOLS_DEFINITIONS)
    names = {t["function"]["name"] for t in tools}
    assert "manage_file" in names and "phone_call" not in names and "watch_device" not in names
