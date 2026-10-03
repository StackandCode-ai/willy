"""
Tests for presence (why/when a device went offline), the device registry, watchdog alerts,
the hub-side fast path (reminders, timers, presence, find my phone), offline-aware tool
routing, and the voice transcript filter.
"""

import sys
import json
import time
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from server.device_manager import DeviceManager, DeviceInfo, describe_duration
from server.fast_router import match_fast_intent
from server.watchdog import Watchdog


def _run(coro):
    return asyncio.run(coro)


def _recorder(mgr: DeviceManager):
    seen = []
    mgr.presence_listeners.append(lambda kind, dev, info: seen.append((kind, dev.device_id, dict(info))))
    return seen


# ------------------------------------------------------------------- presence

def test_announced_reason_wins_and_restart_is_detected():
    async def scenario():
        mgr = DeviceManager()
        seen = _recorder(mgr)
        ws, observer = AsyncMock(), AsyncMock()
        mgr.add_observer(observer)
        dev = await mgr.register_device(ws, "pc_1", "pc", "HariG", boot_time=1000.0)
        await mgr.announce_going_offline("pc_1", "sleep")
        assert dev.status == "offline" and dev.offline_reason == "sleep"
        assert observer.send_json.await_args_list[-1].args[0]["type"] == "device_offline"
        # The socket dies abnormally later (the PC is asleep): the announced reason stays.
        await mgr.unregister_device("pc_1", ws, close_code=1006)
        assert dev.offline_reason == "sleep"
        assert dev.to_dict()["presence"]["reason_text"] == "the PC went to sleep"
        # Next connection reports a newer boot: it was restarted, not just woken up.
        dev.offline_since = time.time() - 120
        await mgr.register_device(AsyncMock(), "pc_1", "pc", "HariG", boot_time=time.time() - 30)
        assert [k for k, _d, _i in seen] == ["online", "offline", "online"]
        back = seen[-1][2]
        assert back["previous_reason"] == "restarted" and back["offline_for"] >= 119
        assert dev.status == "online" and dev.offline_reason is None
    _run(scenario())


def test_close_codes_and_silence_give_reasons():
    async def scenario():
        mgr = DeviceManager(stale_after_sec=5)
        clean_ws, lost_ws = AsyncMock(), AsyncMock()
        await mgr.register_device(clean_ws, "pc_a", "pc", "A")
        await mgr.register_device(lost_ws, "pc_b", "pc", "B")
        await mgr.unregister_device("pc_a", clean_ws, close_code=1000)
        await mgr.unregister_device("pc_b", lost_ws, close_code=1006)
        assert mgr.devices["pc_a"].offline_reason == "app_closed"
        assert mgr.devices["pc_b"].offline_reason == "connection_lost"
        silent = await mgr.register_device(AsyncMock(), "pc_c", "pc", "C")
        silent.last_seen -= 30
        await mgr.sweep_stale()
        assert silent.status == "offline" and silent.offline_reason == "no_heartbeat"
        assert "stopped responding" in silent.presence_sentence()
    _run(scenario())


def test_beat_in_flight_does_not_undo_an_announcement():
    async def scenario():
        mgr = DeviceManager()
        dev = await mgr.register_device(AsyncMock(), "pc_1", "pc", "HariG")
        await mgr.announce_going_offline("pc_1", "shutdown")
        await mgr.handle_heartbeat(dev, {"cpu_pct": 5})  # sent just before the announcement
        assert dev.status == "offline" and mgr.get_all_devices()[0]["online"] is False
        dev.announced_offline_at -= 10  # much later: it's genuinely alive again (shutdown cancelled)
        await mgr.handle_heartbeat(dev, {"cpu_pct": 6})
        assert dev.status == "online" and dev.offline_reason is None
    _run(scenario())


def test_registry_remembers_devices_across_hub_restarts(tmp_path):
    async def scenario():
        path = tmp_path / "devices.json"
        mgr = DeviceManager(registry_path=path)
        dev = await mgr.register_device(AsyncMock(), "pc_1", "pc", "HariG")
        dev.update_telemetry({"battery_pct": 71})
        mgr.save_registry()
        assert json.loads(path.read_text())[0]["device_id"] == "pc_1"
        reloaded = DeviceManager(registry_path=path)
        pc = reloaded.devices["pc_1"]
        assert pc.status == "offline" and pc.offline_reason == "hub_restarted" and pc.telemetry["battery_pct"] == 71
        assert reloaded.last_known("pc").device_id == "pc_1" and reloaded.get_first_online_pc() is None
        assert "OFFLINE" in reloaded.live_context() and "Last known battery 71%" in reloaded.live_context()
    _run(scenario())


def test_describe_duration():
    assert describe_duration(42) == "42 seconds"
    assert describe_duration(60 * 12) == "12 minutes"
    assert describe_duration(3600 + 60 * 5) == "1 hour 5 min"
    assert describe_duration(3 * 86400) == "3 days"


# ------------------------------------------------------------------- watchdog

def _watchdog_setup(tmp_path, **kw):
    delivered = []

    async def deliver(alert):
        delivered.append(alert)

    mgr = DeviceManager()
    online = lambda device_id: getattr(mgr.devices.get(device_id), "status", None) == "online"  # noqa: E731
    wd = Watchdog(tmp_path / "watches.json", deliver=deliver, is_online=online,
                  grace_sec=kw.get("grace", 0.05), then_delay_sec=0, run_query=kw.get("run_query"))
    mgr.presence_listeners.append(wd.on_presence)
    return mgr, wd, delivered


def test_one_shot_online_alert_fires_once_with_downtime_and_follow_up(tmp_path):
    async def run_query(query):
        return {"reply": f"answer to {query}"}

    async def scenario():
        mgr, wd, delivered = _watchdog_setup(tmp_path, run_query=run_query)
        ws = AsyncMock()
        dev = await mgr.register_device(ws, "pc_1", "pc", "HariG")
        await mgr.announce_going_offline("pc_1", "sleep")
        await mgr.unregister_device("pc_1", ws, close_code=1006)
        wd.add("pc", "online", then="what is using my CPU")
        dev.offline_since = time.time() - 23 * 60
        await mgr.register_device(AsyncMock(), "pc_1", "pc", "HariG")
        await asyncio.sleep(0.1)
        assert len(delivered) == 1
        alert = delivered[0]
        assert alert["type"] == "presence_alert" and alert["event"] == "online"
        assert alert["title"] == "HariG is back online"
        assert alert["message"] == "Back after 23 minutes (the PC went to sleep)."
        assert alert["reply"] == "answer to what is using my CPU"
        assert wd.watches == []  # one-shot watches are used up
        assert json.loads((tmp_path / "watches.json").read_text()) == []
    _run(scenario())


def test_watchdog_skips_blips_and_reports_real_outages(tmp_path):
    async def scenario():
        mgr, wd, delivered = _watchdog_setup(tmp_path)
        wd.add("pc", "both", repeat=True)
        ws = AsyncMock()
        await mgr.register_device(ws, "pc_1", "pc", "HariG")
        delivered.clear()  # first connection
        # Blip: drops and is back within the grace period -> nothing.
        await mgr.unregister_device("pc_1", ws, close_code=1006)
        ws2 = AsyncMock()
        await mgr.register_device(ws2, "pc_1", "pc", "HariG")
        await asyncio.sleep(0.15)
        assert delivered == []
        # Real outage: offline alert after the grace period, then a back-online alert.
        await mgr.announce_going_offline("pc_1", "shutdown")
        await mgr.unregister_device("pc_1", ws2, close_code=1006)
        await asyncio.sleep(0.15)
        assert [a["event"] for a in delivered] == ["offline"]
        assert delivered[0]["title"] == "HariG went offline" and "was shut down" in delivered[0]["message"]
        await mgr.register_device(AsyncMock(), "pc_1", "pc", "HariG")
        await asyncio.sleep(0.05)
        assert [a["event"] for a in delivered] == ["offline", "online"]
        assert len(wd.watches) == 1  # the watchdog keeps running
    _run(scenario())


def test_watch_management_and_persistence(tmp_path):
    mgr, wd, _ = _watchdog_setup(tmp_path)
    a = wd.add("laptop", "online")
    assert wd.add("pc", "online") is a  # asking twice doesn't double the alert
    wd.add("phone", "offline")
    assert [w["device"] for w in Watchdog(tmp_path / "watches.json", deliver=AsyncMock()).watches] == ["pc", "phone"]
    assert wd.list()[0]["summary"] == "Once your PC comes online"
    assert wd.remove(device="phone") == 1 and wd.remove(watch_id=a["id"]) == 1 and wd.watches == []


# ------------------------------------------------------------ hub fast path

@pytest.mark.parametrize("text,tool,args", [
    ("Remind me to stretch in 30 minutes", "schedule_reminder", {"text": "Stretch", "in_minutes": 30.0}),
    ("remind me to call mom at 5pm", "schedule_reminder", {"text": "Call mom", "time": "5pm"}),
    ("in 10 min remind me to check the oven", "schedule_reminder", {"text": "Check the oven", "in_minutes": 10.0}),
    ("remind me to drink water in half an hour", "schedule_reminder", {"text": "Drink water", "in_minutes": 30.0}),
    ("set a timer for 10 minutes", "set_timer", {"minutes": 10.0}),
    ("start a 30 second timer", "set_timer", {"minutes": 0.5}),
    ("is my pc online", "presence", {"device": "pc"}),
    ("why is my laptop offline", "presence", {"device": "pc"}),
    ("when was my phone last online", "presence", {"device": "mobile"}),
    ("tell me when my pc comes online", "watch", {"device": "pc", "event": "online", "repeat": False}),
    ("let me know when my laptop is back online", "watch", {"device": "pc", "event": "online", "repeat": False}),
    ("I need watch dog", "watch", {"device": "pc", "event": "both", "repeat": True}),
    ("turn off watchdog", "unwatch", {"device": "pc"}),
])
def test_fast_router_hub_intents(text, tool, args):
    intent = match_fast_intent(text)
    assert intent is not None and intent.target == "hub", text
    assert intent.tool == tool and intent.args == args


def test_find_my_phone_goes_to_the_phone():
    ring = match_fast_intent("where is my phone")
    assert ring.target == "phone" and ring.tool == "ring_device"
    assert match_fast_intent("stop ringing").tool == "stop_ring"


# ------------------------------------------------- hub routing (in-process)

def _fresh_hub():
    import server.app as hub
    hub.device_manager.devices.clear()
    hub.device_manager.active_sockets.clear()
    hub.watchdog.watches.clear()
    return hub


def test_reminders_timers_and_watches_work_while_everything_is_offline():
    hub = _fresh_hub()

    async def scenario():
        before = len(hub.morning_service.list_reminders())
        res = await hub.run_command("Remind me to stretch in 30 minutes", source="test", session_id="t")
        assert res["success"] and res["fast_path"] and res["reply"].startswith("Reminder set ")  # "at" or "tomorrow at"
        rem = hub.morning_service.list_reminders()[-1]
        assert rem["text"] == "Stretch" and abs(rem["due_at"] - (time.time() + 1800)) < 5

        timer = await hub.run_command("set a timer for 10 minutes", source="test", session_id="t")
        assert timer["success"] and "10 minutes" in timer["reply"] and "alert you" in timer["reply"]
        assert hub.morning_service.list_reminders()[-1].get("kind") == "timer"

        watch = await hub.run_command("tell me when my pc comes online", source="test", session_id="t")
        assert watch["success"] and "alert your phone" in watch["reply"] and len(hub.watchdog.watches) == 1

        executor = hub.make_tool_executor(None, "t", "test")
        pc_tool = await executor("take_screenshot", {})
        assert pc_tool["success"] is False and pc_tool["error"] == "PC_OFFLINE"
        phone_tool = await executor("send_sms", {"to": "Mom", "message": "hi"})
        assert phone_tool["error"] == "PHONE_OFFLINE"
        status = await executor("get_pc_status", {})
        assert status["success"] is False and "hasn't connected" in status["reply"]
        assert (await executor("schedule_reminder", {"text": "x"}))["success"] is False  # no time given
        for rem in hub.morning_service.list_reminders()[before:]:
            hub.morning_service.delete_reminder(rem["id"])
        hub.watchdog.remove()

    _run(scenario())


def test_orchestrator_runs_hub_tools_when_the_pc_is_offline():
    from types import SimpleNamespace
    from server.orchestrator import ServerOrchestrator
    hub = _fresh_hub()

    def call(name, args):
        return SimpleNamespace(id="c1", type="function", function=SimpleNamespace(name=name, arguments=args))

    def completion(content=None, tool_calls=None):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tool_calls))])

    async def scenario():
        orch = ServerOrchestrator()
        create = AsyncMock(side_effect=[
            completion(tool_calls=[call("schedule_reminder", '{"text": "Stretch", "in_minutes": 5}')]),
            completion(content="Reminder set for 5 minutes from now."),
        ])
        orch.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        before = len(hub.morning_service.list_reminders())
        res = await orch.process_query("remind me to stretch in 5", hub.make_tool_executor(None, "s"),
                                       pc_online=False, session_id="s")
        assert res["success"] is True and res["tools"][0]["name"] == "schedule_reminder"
        assert len(hub.morning_service.list_reminders()) == before + 1
        hub.morning_service.delete_reminder(hub.morning_service.list_reminders()[-1]["id"])
        # The reminder's own confirmation is spoken: no second LLM round.
        assert create.await_count == 1 and res["reply"].startswith("Reminder set ") and "Stretch" in res["reply"]
        assert orch.recent_exchanges("s") == [{"user": "remind me to stretch in 5", "assistant": res["reply"]}]

    _run(scenario())


def test_multi_part_requests_keep_the_second_round():
    from types import SimpleNamespace
    from server.orchestrator import ServerOrchestrator
    hub = _fresh_hub()

    def call(name, args):
        return SimpleNamespace(id=name, type="function", function=SimpleNamespace(name=name, arguments=args))

    def completion(content=None, tool_calls=None):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tool_calls))])

    async def scenario():
        orch = ServerOrchestrator()
        create = AsyncMock(side_effect=[
            completion(tool_calls=[call("schedule_reminder", '{"text": "Call the bank", "in_minutes": 60}')]),
            completion(tool_calls=[call("watch_device", '{"device": "pc", "event": "online"}')]),
            completion(content="Reminder set, and I'll tell you when your PC is back."),
        ])
        orch.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        res = await orch.process_query("remind me to call the bank in an hour and tell me when my laptop is online",
                                       hub.make_tool_executor(None, "m"), session_id="m")
        assert [t["name"] for t in res["tools"]] == ["schedule_reminder", "watch_device"]
        assert create.await_count == 3 and len(hub.watchdog.watches) == 1
        hub.watchdog.remove()
        for rem in hub.morning_service.find_reminders("call the bank"):
            hub.morning_service.delete_reminder(rem["id"])

    _run(scenario())


class _RateLimited(Exception):
    status_code = 429


def test_rate_limited_model_falls_back_and_cools_down():
    from types import SimpleNamespace
    from server.orchestrator import ServerOrchestrator

    def completion(content):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=None))])

    async def scenario():
        orch = ServerOrchestrator(provider="groq", model="qwen/qwen3.8-27b")
        orch.models = ["qwen/qwen3.8-27b", "openai/gpt-oss-120b", "openai/gpt-oss-20b"]
        calls = []

        async def create(**kwargs):
            calls.append(kwargs["model"])
            if kwargs["model"] == "qwen/qwen3.8-27b":
                raise _RateLimited("Rate limit reached for model on tokens per day (TPD). Please try again in 18m21.168s.")
            return completion(f"answer from {kwargs['model']}")

        orch.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        res = await orch.process_query("hello", None, session_id="a")
        assert res["reply"] == "answer from openai/gpt-oss-120b" and orch.last_model == "openai/gpt-oss-120b"
        assert 18 * 60 < orch._cooldown["qwen/qwen3.8-27b"] - time.time() < 19 * 60
        calls.clear()
        await orch.process_query("again", None, session_id="b")
        assert calls == ["openai/gpt-oss-120b"]  # the rate-limited model is skipped while cooling down

    _run(scenario())


def test_everything_rate_limited_gives_a_plain_answer():
    from types import SimpleNamespace
    from server.orchestrator import ServerOrchestrator

    async def scenario():
        orch = ServerOrchestrator(provider="groq", model="m1")
        orch.models = ["m1", "m2"]

        async def create(**kwargs):
            raise _RateLimited("Rate limit reached. Please try again in 17m2s.")

        orch.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        res = await orch.process_query("hello", None, session_id="c")
        assert res["success"] is False
        assert res["reply"].startswith("My AI quota is used up for about 17 minutes")
        assert "Rate limit" not in res["reply"] and "429" not in res["reply"]

    _run(scenario())


def test_scheduler_fires_exact_timers():
    from server.scheduler import ReminderScheduler
    from server import timeutil

    class Morning:
        config = {"reminders": [{"id": "t1", "text": "Timer: eggs", "kind": "timer", "time": "", "date": "",
                                 "due_at": time.time() - 1}], "alarms": [], "call_enabled": False}

        def save_config(self):
            pass

    events = _run(ReminderScheduler(Morning(), AsyncMock()).tick(timeutil.user_now()))
    assert [(e["kind"], e["title"], e["message"]) for e in events] == [("timer", "Timer done", "Timer: eggs")]


def test_text_written_tool_calls_are_recognised():
    from server.orchestrator import text_tool_calls
    xml = ("<tool_call> <function=execute_powershell> <parameter=command> winget install --id Python.Python.3.12 "
           "</parameter> </function> </tool_call>")
    calls = text_tool_calls(xml)
    assert [c.function.name for c in calls] == ["execute_powershell"]
    assert json.loads(calls[0].function.arguments)["command"].startswith("winget install")
    js = '<tool_call>{"name": "volume_control", "arguments": {"action": "set", "level": 30}}</tool_call>'
    assert json.loads(text_tool_calls(js)[0].function.arguments) == {"action": "set", "level": 30}
    assert text_tool_calls("<function=rm_rf_everything></function>") == []  # unknown tools ignored
    assert text_tool_calls("plain answer") == []


def test_risky_powershell_needs_a_real_yes():
    hub = _fresh_hub()

    async def scenario():
        risky = {"command": "winget install --id Python.Python.3.12 -e --silent"}
        blocked = await hub.make_tool_executor("pc_x", "s", "t", "you can do these")("execute_powershell", risky)
        assert blocked["error"] == "NEEDS_CONFIRMATION"
        claimed = await hub.make_tool_executor("pc_x", "s", "t", "install python")("execute_powershell",
                                                                               {**risky, "confirmed": True})
        assert claimed["error"] == "NEEDS_CONFIRMATION"  # the model saying "confirmed" isn't enough
        allowed = await hub.make_tool_executor("pc_x", "s", "t", "yes go ahead")("execute_powershell",
                                                                              {**risky, "confirmed": True})
        assert allowed["error"] == "DEVICE_OFFLINE"  # passed the guard (no PC in the test)
        harmless = await hub.make_tool_executor("pc_x", "s", "t", "check my ip")("execute_powershell",
                                                                             {"command": "Get-NetIPAddress"})
        assert harmless["error"] == "DEVICE_OFFLINE"

    _run(scenario())


def test_voice_noise_is_not_a_command():
    from server.app import is_meaningful_transcript
    for noise in (".", " ... ", "Thank you.", "you", "", None, "Thanks for watching!"):
        assert is_meaningful_transcript(noise) is False, noise
    for speech in ("open chrome", "Go desktop", "what's my battery?"):
        assert is_meaningful_transcript(speech) is True


# ------------------------------------------------------------------ file relay & missed replies

def test_file_store_saves_serves_and_expires(tmp_path):
    from server.file_store import FileStore, FileTooLarge, safe_name

    class Upload:
        def __init__(self, name, data):
            self.filename, self.content_type, self._data = name, "text/plain", data

        async def read(self, n):
            chunk, self._data = self._data[:n], self._data[n:]
            return chunk

    store = FileStore(tmp_path, ttl_sec=60, max_bytes=10)
    meta = asyncio.run(store.save(Upload("..\evil/notes?.txt", b"hello"), sender="pc_x"))
    assert meta["name"] == "notes_.txt" and meta["size"] == 5 and meta["url"] == f"/api/v1/files/{meta['id']}"
    assert open(store.get(meta["id"])["path"], "rb").read() == b"hello"
    assert store.get("../../etc") is None and store.get("0" * 24) is None
    with pytest.raises(FileTooLarge):
        asyncio.run(store.save(Upload("big.bin", b"x" * 11)))
    assert len(store.list()) == 1  # the oversized upload left nothing behind
    store.ttl_sec = -1
    assert store.get(meta["id"]) is None and store.list() == []
    assert safe_name("") == "file" and safe_name("C:" + chr(92) + "a" + chr(92) + "b.pdf") == "b.pdf"


def test_upload_relays_file_to_the_phone(tmp_path):
    from fastapi.testclient import TestClient
    import server.app as hub
    from server.file_store import FileStore

    hub.file_store = FileStore(tmp_path)
    sent = []

    async def fake_send(device_id, action, payload, timeout=None):
        sent.append((device_id, action, payload))
        return {"success": True, "message": f"Saved {payload['name']} to Downloads/Willy on your phone."}

    phone = SimpleNamespace(device_id="mobile_1", device_type="mobile", name="Pixel")
    with patch.object(hub.device_manager, "get_first_online_mobile", return_value=phone), \
            patch.dict(hub.device_manager.active_sockets, {"mobile_1": object()}), \
            patch.object(hub.device_manager, "send_to_device", side_effect=fake_send):
        client = TestClient(hub.app)
        headers = {"Authorization": f"Bearer {hub._expected_token()}"}
        res = client.post("/api/v1/files?target=phone", files={"file": ("report.pdf", b"%PDF-1", "application/pdf")},
                          headers=headers).json()
        assert res["delivered"] is True and res["reply"] == "Saved report.pdf to Downloads/Willy on your phone."
        assert sent[0][0] == "mobile_1" and sent[0][1] == "receive_file" and sent[0][2]["name"] == "report.pdf"
        got = client.get(res["file"]["url"], headers=headers)
        assert got.status_code == 200 and got.content == b"%PDF-1"
        assert client.get(res["file"]["url"]).status_code == 401  # needs the token


def test_missed_command_result_is_resent_after_reconnect():
    import server.app as hub

    class Sock:
        def __init__(self, ok):
            self.ok, self.got = ok, []

    async def fake_send(ws, data):
        if ws.ok:
            ws.got.append(data)
        return ws.ok

    async def scenario():
        dead, fresh = Sock(False), Sock(True)
        dev = SimpleNamespace(device_id="mobile_9", device_type="mobile")
        with patch.object(hub.device_manager, "send_to_socket", side_effect=fake_send), \
                patch.object(hub, "run_command", AsyncMock(return_value={"success": True, "reply": "Done late."})), \
                patch.object(hub.file_store, "list", return_value=[]):
            await hub._handle_device_ws_command(dead, dev, {"payload": {"query": "hi"}, "request_id": "r1"})
            assert hub._outbox["mobile_9"]
            await hub._on_device_connected(fresh, dev)
        assert fresh.got == [{"type": "command_result", "request_id": "r1",
                              "result": {"success": True, "reply": "Done late."}}]
        assert "mobile_9" not in hub._outbox

    _run(scenario())


def test_brain_waits_out_a_short_per_minute_limit():
    from server.orchestrator import ServerOrchestrator

    class RateLimited(Exception):
        status_code = 429

    async def scenario():
        orch = ServerOrchestrator()
        orch.models = ["a", "b"]
        calls = []

        async def create(**kw):
            calls.append(kw["model"])
            if len(calls) <= 2:
                raise RateLimited("Rate limit reached ... Please try again in 0.05s.")
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="hi", tool_calls=None))])

        orch.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        with patch("server.orchestrator._unavailable_for", return_value=0.05):
            res = await orch.process_query("hello", None, session_id="w")
        assert res["reply"] == "hi" and calls == ["a", "b", "a"]

    _run(scenario())


# ------------------------------------------------------------------ two phones, files, live info

def test_phone_actions_go_to_the_phone_in_use_or_the_named_one():
    mgr = DeviceManager()
    a3 = DeviceInfo("mobile_a3", "mobile", "OPPO A3 Pro 5G", "CPH2665")
    a16 = DeviceInfo("mobile_a16", "mobile", "OPPO A16", "CPH2269")
    mgr.devices = {d.device_id: d for d in (a3, a16)}
    mgr.active_sockets = {"mobile_a3": object(), "mobile_a16": object()}
    a16.last_seen = a3.last_seen + 5  # the A16 happened to heartbeat last
    mgr.note_used("mobile_a3")
    assert mgr.get_first_online_mobile() is a3
    assert mgr.get_first_online_mobile("my A16") is a16
    assert mgr.find_phone("a3 pro") is a3 and mgr.find_phone("pixel") is None and mgr.find_phone("my phone") is None
    assert DeviceInfo.from_record(a3.to_record()).last_used_at == a3.last_used_at


def test_named_phone_that_is_offline_is_not_swapped_for_another():
    import server.app as hub

    a3 = DeviceInfo("mobile_a3", "mobile", "OPPO A3 Pro 5G")
    a16 = DeviceInfo("mobile_a16", "mobile", "OPPO A16")
    with patch.dict(hub.device_manager.devices, {"mobile_a3": a3, "mobile_a16": a16}, clear=True), \
            patch.dict(hub.device_manager.active_sockets, {"mobile_a3": object()}, clear=True), \
            patch.object(hub.device_manager, "send_to_device", AsyncMock(return_value={"success": True})) as send:
        res = asyncio.run(hub._phone_tool("phone_call", {"to": "Mom", "phone": "A16"}))
        assert res["success"] is False and "OPPO A16 isn't online" in res["error"] and not send.called
        asyncio.run(hub._phone_tool("phone_call", {"to": "Mom", "phone": "A3"}))
        assert send.call_args.args[0] == "mobile_a3" and "phone" not in send.call_args.args[2]


def test_file_delete_needs_the_users_yes():
    import server.app as hub

    with patch.object(hub.device_manager, "send_to_device", AsyncMock(return_value={"success": True})) as send:
        ask = asyncio.run(hub.make_tool_executor("pc_1", user_text="delete old.txt")("manage_file",
                                                                                     {"op": "delete", "path": "old.txt"}))
        assert ask["error"] == "NEEDS_CONFIRMATION" and not send.called
        asyncio.run(hub.make_tool_executor("pc_1", user_text="yes")("manage_file",
                                                                    {"op": "delete", "path": "old.txt", "confirmed": True}))
        assert send.called
        asyncio.run(hub.make_tool_executor("pc_1", user_text="move it")("manage_file",
                                                                        {"op": "move", "path": "a.txt", "destination": "Documents"}))
        assert send.call_count == 2  # other operations need no confirmation


def test_live_info_tools_are_always_offered():
    from server.pc_tools_schema import select_tools, SERVER_SIDE_TOOLS
    names = {t["function"]["name"] for t in select_tools("what's the weather in new york")}
    assert {"get_weather", "place_time", "web_lookup"} <= names <= set(names)
    assert {"get_weather", "place_time", "web_lookup"} <= SERVER_SIDE_TOOLS


# ------------------------------------------------------------------ call panels & voice replies

@pytest.mark.parametrize("text,panel,path", [
    ("show my files", "files", ""), ("open d drive", "files", "D:\\"), ("browse the c drive", "files", "C:\\"),
    ("show my screen", "screenshot", None), ("open the camera", "camera", None), ("take a photo", "camera", None),
])
def test_panel_intents(text, panel, path):
    intent = match_fast_intent(text)
    assert intent is not None and intent.tool == "show_panel" and intent.target == "hub"
    assert intent.args["panel"] == panel and intent.args.get("path") == path


def test_panel_directive_reaches_the_reply():
    import server.app as hub

    pc = SimpleNamespace(device_id="pc_x")
    with patch.object(hub.device_manager, "resolve_pc", return_value=pc):
        res = asyncio.run(hub.run_command("open d drive", source="mobile"))
        assert res["ui"] == {"panel": "files", "device_id": "pc_x", "path": "D:\\", "title": None}
        tool = asyncio.run(hub.make_tool_executor("pc_x")("show_panel", {"panel": "camera"}))
        assert tool["ui"]["panel"] == "camera" and tool["success"]


def test_spoken_text_is_short_and_plain():
    from server.app import spoken_text
    long_reply = "**Sure.** " + " ".join(f"Point number {i} is here." for i in range(40))
    spoken = spoken_text(long_reply)
    assert "**" not in spoken and len(spoken) < 380 and spoken.endswith("The details are on screen.")
    assert spoken_text("Done.") == "Done."


def test_voice_calls_get_the_short_answer_rules():
    from server.orchestrator import ServerOrchestrator, VOICE_RULES

    async def scenario():
        orch = ServerOrchestrator()
        create = AsyncMock(return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Sure.", tool_calls=None))]))
        orch.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        await orch.process_query("hi", None, session_id="v", voice=True)
        kw = create.await_args.kwargs
        assert VOICE_RULES in kw["messages"][0]["content"] and kw["max_tokens"] < 600

    _run(scenario())


def test_feedback_notes_are_saved_and_listed(tmp_path):
    from fastapi.testclient import TestClient
    import server.app as hub

    with patch.object(hub, "_feedback_path", lambda: tmp_path / "feedback.jsonl"):
        res = asyncio.run(hub.make_tool_executor("pc_1", user_text="note that you can't play videos")(
            "save_feedback", {"note": "Couldn't play a Telegram video in VLC on screen 2"}))
        assert res["success"]
        hub.save_feedback("Tools failed: open_file", "auto", asked="play x")
        client = TestClient(hub.app)
        items = client.get("/api/v1/feedback", headers={"Authorization": f"Bearer {hub._expected_token()}"}).json()["feedback"]
        assert [i["kind"] for i in items] == ["auto", "user"] and "VLC" in items[1]["note"]
        assert items[1]["asked"] == "note that you can't play videos"


# ------------------------------------------------------------------ push notifications

def test_push_goes_only_to_disconnected_phones_and_forgets_dead_tokens():
    import server.app as hub

    online = DeviceInfo("mobile_on", "mobile", "A3")
    asleep = DeviceInfo("mobile_off", "mobile", "A16")
    gone = DeviceInfo("mobile_gone", "mobile", "Old")
    online.push_token, asleep.push_token, gone.push_token = "tok-on", "tok-off", "tok-gone"
    sent = []

    async def fake_send(token, title="", body="", data=None, silent=False):
        sent.append((token, title, data))
        return {"success": False, "token_gone": True} if token == "tok-gone" else {"success": True}

    with patch.dict(hub.device_manager.devices, {d.device_id: d for d in (online, asleep, gone)}, clear=True), \
            patch.dict(hub.device_manager.active_sockets, {"mobile_on": object()}, clear=True), \
            patch.object(hub.push_service, "send", side_effect=fake_send), \
            patch.object(hub.device_manager, "save_registry"):
        n = asyncio.run(hub.push_to_offline_phones("Reminder", "Stretch", {"kind": "reminder"}))
        assert n == 1 and sorted(t for t, _, _ in sent) == ["tok-gone", "tok-off"]  # the connected phone is skipped
        assert gone.push_token is None and asleep.push_token == "tok-off"


def test_a_sleeping_phone_is_woken_by_push_before_a_phone_action():
    import server.app as hub

    phone = DeviceInfo("mobile_a3", "mobile", "A3")
    phone.push_token = "tok"
    sockets = {}

    async def fake_send(token, title="", body="", data=None, silent=False):
        assert silent and data["kind"] == "wake"
        sockets["mobile_a3"] = object()  # the app reconnects
        return {"success": True}

    with patch.dict(hub.device_manager.devices, {"mobile_a3": phone}, clear=True), \
            patch.object(hub.device_manager, "active_sockets", sockets), \
            patch.object(type(hub.push_service), "configured", new=property(lambda self: True)), \
            patch.object(hub.push_service, "send", side_effect=fake_send), \
            patch.object(hub.device_manager, "online_phones", side_effect=lambda: [phone] if sockets else []), \
            patch.object(hub.device_manager, "send_to_device", AsyncMock(return_value={"success": True, "message": "Calling Mom."})):
        res = asyncio.run(hub._phone_tool("phone_call", {"to": "Mom"}))
        assert res["success"] and res["message"] == "Calling Mom."


def test_phone_registers_its_push_token(tmp_path):
    from fastapi.testclient import TestClient
    import server.app as hub

    phone = DeviceInfo("mobile_x", "mobile", "A3")
    with patch.dict(hub.device_manager.devices, {"mobile_x": phone}, clear=True), \
            patch.object(hub.device_manager, "save_registry"):
        client = TestClient(hub.app)
        h = {"Authorization": f"Bearer {hub._expected_token()}"}
        assert client.post("/api/v1/push/register", json={"device_id": "mobile_x", "token": "abc"}, headers=h).json()["success"]
        assert phone.push_token == "abc" and DeviceInfo.from_record(phone.to_record()).push_token == "abc"
        assert client.post("/api/v1/push/register", json={"device_id": "nope", "token": "abc"}, headers=h).status_code == 404


# ------------------------------------------------------------------ server monitoring

def _snap(cpu=10, ram=40, disk=30, app_status="online", restarts=0, nginx="active", site_up=True, cert=60):
    return {"system": {"cpu_pct": cpu, "ram_pct": ram, "disk_pct": disk, "ram_used_mb": 800, "ram_total_mb": 1900,
                       "disk_free_gb": 40, "uptime_sec": 86400 * 61},
            "apps": [{"name": "pm-tool", "status": app_status, "restarts": restarts}],
            "services": {"nginx": nginx}, "containers": [{"name": "n8n", "state": "running", "status": "Up"}],
            "sites": [{"domain": "truewilly.com", "up": site_up, "status": 200 if site_up else 502, "cert_days": cert}],
            "checked_at": time.time()}


def test_server_alerts_need_two_bad_checks_and_announce_recovery():
    from server.server_monitor import ServerMonitor

    alerts = []
    mon = ServerMonitor(on_alert=alerts.append)
    states = iter([_snap(site_up=False), _snap(site_up=False), _snap(site_up=False), _snap()])

    def fake_collect(include_sites=False):
        mon.snapshot = next(states)
        return mon.snapshot

    mon.collect = fake_collect
    for _ in range(4):
        asyncio.run(mon.check())
    assert [(a["state"], a["key"]) for a in alerts] == [("problem", "site:truewilly.com"), ("resolved", "site:truewilly.com")]
    assert "truewilly.com is down (HTTP 502)" in alerts[0]["message"] and "back up" in alerts[1]["message"]


def test_server_problem_rules():
    from server.server_monitor import ServerMonitor

    mon = ServerMonitor()
    found = mon.problems(_snap(cpu=97, ram=95, disk=91, app_status="errored", nginx="failed", cert=5))
    assert set(found) == {"cpu", "ram", "disk", "app:pm-tool", "svc:nginx", "cert:truewilly.com"}
    assert mon.problems(_snap()) == {}
    mon.problems(_snap(restarts=10))
    assert "loop:pm-tool" in mon.problems(_snap(restarts=14))  # 4 restarts within the window


def test_server_status_question_is_answered_without_the_ai():
    import server.app as hub

    intent = match_fast_intent("how's my server")
    assert intent is not None and intent.tool == "server_status" and intent.target == "hub"
    assert match_fast_intent("are my websites up").tool == "server_status"
    hub.server_monitor.snapshot = _snap()
    res = asyncio.run(hub.run_command("is my server ok", source="mobile"))
    assert res["reply"].startswith("Your server is healthy: CPU 10%") and "1 of 1 sites up" in res["reply"]


def test_restarting_a_server_app_needs_the_users_yes():
    import server.app as hub

    hub.server_monitor.snapshot = _snap()
    ask = asyncio.run(hub.make_tool_executor(None, user_text="restart pm-tool")("restart_server_app", {"app": "pm-tool"}))
    assert ask["error"] == "NEEDS_CONFIRMATION"
    unknown = asyncio.run(hub.make_tool_executor(None, user_text="yes")("restart_server_app", {"app": "nope", "confirmed": True}))
    assert "no server app called" in unknown["error"]


def test_muted_problems_never_alert_and_alerts_are_batched(tmp_path):
    from server.server_monitor import ServerMonitor

    alerts = []
    mon = ServerMonitor(on_alert=alerts.append, settings_path=tmp_path / "mon.json")
    mon.set_ignored("site:old.example.com", True)
    snap = _snap(site_up=False)
    snap["sites"].append({"domain": "old.example.com", "up": False, "status": 502, "cert_days": 60})
    snap["sites"].append({"domain": "shop.example.com", "up": False, "status": 502, "cert_days": 60})
    mon.collect = lambda include_sites=False: setattr(mon, "snapshot", snap) or snap
    asyncio.run(mon.check()); asyncio.run(mon.check())
    assert len(alerts) == 1 and "truewilly.com is down" in alerts[0]["message"] and "shop.example.com" in alerts[0]["message"]
    assert "old.example.com" not in alerts[0]["message"]
    assert ServerMonitor(settings_path=tmp_path / "mon.json").ignored == {"site:old.example.com"}


# ------------------------------------------------------------------ the server as a device

def test_server_tools_route_to_the_agent_with_confirmation_rules():
    import server.app as hub

    srv = DeviceInfo("server_ip_1", "server", "Willy Server")
    calls = []

    async def fake_send(device_id, action, payload, timeout=None):
        calls.append((device_id, action, payload))
        return {"success": True, "stdout": "ok"}

    with patch.object(hub.device_manager, "get_server", return_value=srv), \
            patch.object(hub.device_manager, "send_to_device", side_effect=fake_send):
        ex = hub.make_tool_executor("pc_1", user_text="how much disk is used")
        asyncio.run(ex("server_shell", {"command": "df -h"}))
        assert calls[-1][:2] == ("server_ip_1", "run_shell")
        ask = asyncio.run(ex("server_shell", {"command": "pm2 restart pm-tool"}))
        assert ask["error"] == "NEEDS_CONFIRMATION" and len(calls) == 1
        assert "won't run" in asyncio.run(hub.make_tool_executor("pc_1", user_text="yes")("server_shell",
                                                                    {"command": "rm -rf /", "confirmed": True}))["error"]
        asyncio.run(hub.make_tool_executor("pc_1", user_text="yes do it")("server_control",
                                                                         {"kind": "app", "name": "pm-tool", "action": "restart", "confirmed": True}))
        assert calls[-1][1] == "control" and "confirmed" not in calls[-1][2]
        asyncio.run(ex("manage_file", {"op": "list", "path": "/var/www", "device": "server"}))
        assert calls[-1][1] == "manage_file" and "device" not in calls[-1][2]
        panel = asyncio.run(ex("show_panel", {"panel": "files", "device": "server", "path": "/var/www"}))
        assert panel["ui"]["device_id"] == "server_ip_1" and panel["ui"]["path"] == "/var/www"


def test_server_agent_file_operations(tmp_path, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("agent", Path(__file__).resolve().parent.parent / "server_agent" / "agent.py")
    agent = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(agent)
    monkeypatch.setattr(agent, "TRASH", tmp_path / ".trash")
    f = tmp_path / "a.txt"
    assert agent.manage_file("write", str(f), content="hi")["success"]
    assert agent.manage_file("read", str(f))["content"] == "hi"
    assert agent.list_dir(str(tmp_path))["entries"][0]["name"] == "a.txt"
    assert agent.manage_file("delete", str(f))["success"] and not f.exists() and any((tmp_path / ".trash").iterdir())


def test_git_push_needs_a_yes_and_status_goes_straight_through():
    import server.app as hub

    pc = DeviceInfo("pc_1", "pc", "HariG")
    calls = []

    async def fake_send(device_id, action, payload, timeout=None):
        calls.append((device_id, action, payload))
        return {"success": True, "message": "ok"}

    with patch.dict(hub.device_manager.devices, {"pc_1": pc}), \
            patch.object(hub.device_manager, "send_to_device", side_effect=fake_send), \
            patch.object(hub.device_manager, "spawn"):
        asyncio.run(hub.make_tool_executor("pc_1", user_text="status of willy")("git", {"action": "status", "repo": "willy"}))
        assert calls[-1][1] == "git" and calls[-1][2]["action"] == "status"
        ask = asyncio.run(hub.make_tool_executor("pc_1", user_text="push willy")("git", {"action": "push", "repo": "willy"}))
        assert ask["error"] == "NEEDS_CONFIRMATION" and "push it to GitHub" in ask["ask"] and len(calls) == 1
        asyncio.run(hub.make_tool_executor("pc_1", user_text="yes")("git", {"action": "push", "repo": "willy", "confirmed": True}))
        assert calls[-1][2]["action"] == "push" and "confirmed" not in calls[-1][2]


def test_server_history_is_recorded_downsampled_and_persisted(tmp_path):
    from server.server_monitor import ServerMonitor

    mon = ServerMonitor(settings_path=tmp_path / "server_monitor.json")
    now = time.time()
    mon.history = [[now - 3600 + i * 60, 10 + i % 5, 50, 25, 40, 0.1, 1000 * i, 500 * i] for i in range(60)]
    series = mon.history_series(hours=2, points=12)
    assert 10 <= len(series["points"]) <= 13 and series["points"][0]["ram"] == 50
    assert series["points"][1]["rx_kbps"] is not None
    mon._history_saved = 0
    mon.record(_snap())
    assert (tmp_path / "server_history.json").exists()
    assert len(ServerMonitor(settings_path=tmp_path / "server_monitor.json").history) == 61


def test_server_admin_changes_need_a_yes_and_views_go_to_the_agent():
    import server.app as hub

    srv = DeviceInfo("server_1", "server", "Willy Server")
    calls = []

    async def fake_send(device_id, action, payload, timeout=None):
        calls.append((action, payload, timeout))
        return {"success": True, "message": "ok"}

    with patch.object(hub.device_manager, "get_server", return_value=srv), \
            patch.object(hub.device_manager, "send_to_device", side_effect=fake_send):
        ex = hub.make_tool_executor(None, user_text="any updates on the server?")
        asyncio.run(ex("server_admin", {"topic": "updates"}))
        assert calls[-1][0] == "sys_updates" and calls[-1][2] >= 200
        ask = asyncio.run(ex("server_admin", {"topic": "reboot"}))
        assert ask["error"] == "NEEDS_CONFIRMATION" and "reboot the server" in ask["ask"] and len(calls) == 1
        asyncio.run(hub.make_tool_executor(None, user_text="yes")("server_admin",
                                                               {"topic": "cleanup", "what": "journal", "confirmed": True}))
        assert calls[-1][0] == "cleanup" and calls[-1][1] == {"what": "journal"}


def test_failed_system_services_are_problems():
    from server.server_monitor import ServerMonitor
    snap = _snap()
    snap["failed_units"] = ["certbot-renew"]
    assert ServerMonitor().problems(snap) == {"failed:certbot-renew": "The system service certbot-renew has failed."}


# ------------------------------------------------------------------ token rotation

def test_retired_tokens_still_connect_and_receive_the_new_one(monkeypatch):
    from fastapi.testclient import TestClient
    import server.app as hub

    monkeypatch.setenv("WILLY_REMOTE_TOKEN", "new-token-0123456789-abcdef")
    monkeypatch.setenv("WILLY_LEGACY_TOKENS", "old-token-0123456789-abcdef")
    assert hub._token_ok("new-token-0123456789-abcdef") and hub._token_ok("old-token-0123456789-abcdef")
    assert not hub._token_ok("wrong") and not hub._token_ok("")
    client = TestClient(hub.app)
    with client.websocket_connect("/ws/devices?token=old-token-0123456789-abcdef&device_id=pc_rot&device_type=pc&name=T") as ws:
        first, second = ws.receive_json(), ws.receive_json()
        assert first["type"] == "snapshot"
        assert second == {"type": "token_update", "token": "new-token-0123456789-abcdef"}
    with client.websocket_connect("/ws/devices?token=new-token-0123456789-abcdef&device_id=pc_rot2&device_type=pc&name=T") as ws:
        assert ws.receive_json()["type"] == "snapshot"  # current token: no update sent


def test_tokens_are_masked_in_server_logs():
    import logging
    import server.app as hub

    record = logging.LogRecord("uvicorn.error", logging.INFO, "", 0, '%s - "WebSocket %s" [accepted]',
                               ("1.2.3.4", "/ws/devices?token=SECRET123456&device_id=x"), None)
    hub._MaskTokens().filter(record)
    assert "SECRET123456" not in record.getMessage() and "token=***" in record.getMessage()
