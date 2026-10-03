"""
Willy Central Server Hub & Brain.
Provides device discovery, command routing between Mobile and PC,
Groq Whisper STT, Edge-TTS synthesis, and real-time Web Voice Call.

Realtime model:
- PCs and phones hold a WebSocket on /ws/devices; dashboards subscribe on /ws/events.
- Every heartbeat is fanned out as a `device_update` event, every command as `activity`
  events (running -> done), so all UIs update live without polling.
- Short unambiguous commands take the fast path (no LLM); everything else goes to the
  async LLM brain with per-client conversation sessions.
"""

import os
import logging
import re
import sys
import time
import json
import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Optional, Dict, Any, List
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fastapi import (
    FastAPI,
    Request,
    WebSocket,
    WebSocketDisconnect,
    HTTPException,
    Header,
    Depends,
    Security,
    UploadFile,
    File,
    Query,
)
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, FileResponse
from pydantic import BaseModel

from server.config import settings, DATA_DIR
from server.orchestrator import ServerOrchestrator
from server.device_manager import DeviceManager, describe_duration, describe_when
from server.voice_service import ServerVoiceService
from server.morning_briefing import MorningBriefingService
from server.activity_log import ActivityLog
from server.fast_router import FastIntent, match_fast_intent, telemetry_reply
from server.scheduler import ReminderScheduler
from server.watchdog import Watchdog, normalize_device
from server.pc_tools_schema import PHONE_TOOLS, PHONE_TOOL_ACTIONS
from server import timeutil, live_info
from server.file_store import FileStore, FileTooLarge, describe_size
from server.push import push_service, wake_data
from server.server_monitor import ServerMonitor, pm2_apps, tail_app_log
from server.shell_policy import is_destructive, is_read_only
from server import spaces as _spaces
from server import ai_config
from server import broker as _broker
from server import registry as _registry
from server import usage as _usage
from server.spaces import SpaceAttr
from server.accounts import Accounts, AuthError, Principal, HOME_SPACE

APP_VERSION = "3.1.0"
DEFAULT_TOKEN = settings.WILLY_REMOTE_TOKEN
SERVER_STARTED_AT = time.time()

voice_service = ServerVoiceService()
accounts = Accounts()
usage_counter = _usage.UsageCounter(DATA_DIR / "usage.json")
master_registry = _registry.Registry(accounts.engine)
broker_signer = _broker.BrokerSigner()
jwks_cache = _broker.JwksCache()

# Each account has its own space (server/spaces.py): these names resolve to the objects of
# the space the current request or socket belongs to; outside a request, the owner's.
device_manager = SpaceAttr("device_manager")
orchestrator = SpaceAttr("orchestrator")
morning_service = SpaceAttr("morning_service")
activity_log = SpaceAttr("activity_log")
scheduler = SpaceAttr("scheduler")
watchdog = SpaceAttr("watchdog")
file_store = SpaceAttr("file_store")
# Command results a device missed because its socket dropped while the brain was still
# working: re-sent (same request_id) when it reconnects. device_id -> [(queued_at, message)]
_outbox = SpaceAttr("outbox")
# device_id -> (fetched_at, repos): what the brain knows about each machine's git projects
_repo_index = SpaceAttr("repo_index")


async def push_to_offline_phones(title: str, body: str, data: Dict[str, Any]) -> int:
    """System notification on every phone that isn't connected right now (the connected ones
    already got the event over their socket). Returns how many were sent."""
    sent = 0
    for phone in device_manager.phones_for_push():
        res = await push_service.send(phone.push_token, title, body, data)
        if res.get("success"):
            sent += 1
        elif res.get("token_gone"):
            device_manager.set_push_token(phone.device_id, None)
    return sent


async def _broadcast_and_push(event: Dict[str, Any]) -> None:
    """Scheduler events: live screens get the event, disconnected phones a push notification."""
    await device_manager.broadcast_event(event)
    if event.get("type") in ("reminder_due", "morning_call_due"):
        item = event.get("reminder") or event.get("alarm") or {}
        await push_to_offline_phones(event.get("title") or "Willy", event.get("message") or "",
                                     {"kind": event.get("kind") or "reminder", "id": item.get("id") or "",
                                      "screen": "reminders"})


async def wake_phone_by_push(wait_sec: float = 12.0):
    """Asks a disconnected phone to reconnect (silent push) and waits for it; the phone or None."""
    targets = device_manager.phones_for_push()
    if not targets or not push_service.configured:
        return None
    target = max(targets, key=lambda d: (d.last_used_at, d.last_seen))
    res = await push_service.send(target.push_token, data=wake_data(), silent=True)
    if not res.get("success"):
        if res.get("token_gone"):
            device_manager.set_push_token(target.device_id, None)
        return None
    deadline = time.time() + wait_sec
    while time.time() < deadline:
        await asyncio.sleep(0.5)
        if target.device_id in device_manager.active_sockets:
            return device_manager.devices.get(target.device_id)
    return None


async def _deliver_presence_alert(alert: Dict[str, Any]) -> None:
    """Watchdog alert: every screen gets the event; the phone also gets a system notification."""
    await device_manager.broadcast_event(alert)
    text = alert["message"] + (f"\n{alert['reply']}" if alert.get("reply") else "")
    phone = device_manager.get_first_online_mobile()
    if phone is not None and phone.device_id != alert.get("device_id"):
        await device_manager.send_to_device(phone.device_id, "notify", {"title": alert["title"], "message": text},
                                            timeout=10)
    await push_to_offline_phones(alert["title"], text, {"kind": "alert", "screen": "devices"})
    activity_log.record("watchdog", alert["title"], reply=text, success=True, fast_path=True, latency_ms=0,
                        device_id=alert.get("device_id"))


async def _server_alert(alert: Dict[str, Any]) -> None:
    """A server problem (or its recovery): the same path as watchdog alerts - every screen,
    a notification on the phone (push when its app is asleep), the activity log."""
    problem = alert["state"] == "problem"
    await _deliver_presence_alert({
        "type": "presence_alert", "kind": "server", "event": alert["state"], "device_id": "server",
        "title": "Server problem" if problem else "Server OK again",
        "message": alert["message"], "timestamp": time.time(),
    })


server_monitor = ServerMonitor(on_alert=_server_alert, settings_path=DATA_DIR / "server_monitor.json")


async def _run_watch_query(query: str) -> Dict[str, Any]:
    return await run_command(query, source="watchdog", session_id="watchdog")


def _feedback_path() -> Path:
    return _spaces.current().data_dir / "feedback.jsonl"


def save_feedback(note: str, kind: str = "user", **extra) -> Dict[str, Any]:
    """Improvement notes for the developer: what the user asked for and what went wrong."""
    note = str(note or "").strip()[:1000]
    if not note:
        return {"success": False, "error": "Nothing to note."}
    record = {"at": time.time(), "when": timeutil.user_now().strftime("%Y-%m-%d %H:%M"), "kind": kind, "note": note, **extra}
    try:
        path = _feedback_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
    except OSError as e:
        return {"success": False, "error": f"Couldn't save the note: {e}"}
    return {"success": True, "message": "Noted - I've saved that so it can be improved."}
def _build_space(space) -> None:
    """Creates one account's objects, stored under its own data folder."""
    folder = space.data_dir
    dm = DeviceManager(
        command_timeout=settings.DEVICE_COMMAND_TIMEOUT_SEC,
        stale_after_sec=settings.DEVICE_STALE_SEC,
        registry_path=folder / "devices.json",
    )
    log = ActivityLog(folder / "activity_log.json")
    log.listeners.append(lambda entry, dm=dm: dm.publish({"type": "activity", "entry": entry}))
    brain = ServerOrchestrator()
    morning = MorningBriefingService(folder)
    morning.orchestrator = brain
    watch = Watchdog(
        folder / "watches.json",
        deliver=_deliver_presence_alert,
        run_query=_run_watch_query,
        is_online=lambda device_id, dm=dm: getattr(dm.devices.get(device_id), "status", None) == "online",
    )
    dm.presence_listeners.append(watch.on_presence)
    space.objects.update(
        device_manager=dm, orchestrator=brain, morning_service=morning, activity_log=log,
        scheduler=ReminderScheduler(morning, broadcast=_broadcast_and_push), watchdog=watch,
        file_store=FileStore(folder / "files"), outbox={}, repo_index={},
    )


def _start_space(space) -> None:
    """The space's background work (runs with the space current, so its tasks keep it)."""
    space.objects["tasks"] = [asyncio.create_task(space.objects["scheduler"].run())]


space_registry = _spaces.configure(DATA_DIR, _build_space, _start_space)


def _enter_space(principal: Principal):
    """Makes the caller's space current for the rest of this request / socket."""
    space = space_registry.get(principal.space_id)
    space_registry.ensure_started(space)
    _spaces.use(space)
    return space


HOST_ONLY = ("That's the Willy hub's own server, which only the hub's owner can manage. "
             "To manage your own server, install the Willy server agent on it.")


def _is_host_space() -> bool:
    return _spaces.current().is_home


OUTBOX_TTL_SEC = 10 * 60

# Actions a client may invoke directly (no LLM). Remote PowerShell stays behind the brain.
DIRECT_ACTIONS = {
    "launch_application", "close_application", "open_url", "power_action", "volume_control",
    "media_control", "window_action", "take_screenshot", "get_pc_status", "set_brightness",
    "list_processes", "kill_process", "notify", "speak", "open_folder", "set_clipboard",
    "get_clipboard", "quickdrop", "ring_device", "stop_ring", "get_screen_snapshot",
    "type_text", "press_keys", "system_info", "list_windows", "vibrate", "flashlight",
    "telemetry", "show_note", "read_window_text", "list_dir", "open_file", "file_to_hub", "manage_file", "find_files",
    "run_shell", "control", "server_status", "git", "git_repos", "sys_updates", "apply_updates", "storage", "cleanup",
    "network", "security", "services_list", "service_boot", "timers", "journal", "reboot", "cancel_reboot",
    # phone skills (run on the Android app)
    "phone_call", "phone_sms", "phone_whatsapp", "phone_open_app", "phone_alarm", "phone_timer",
    "phone_volume", "phone_media", "phone_navigate", "phone_notifications", "phone_contacts",
    "phone_open_url", "phone_install_app", "phone_uninstall_app", "phone_camera", "send_file_to_phone",
}
# High-frequency read-only actions that would flood the activity feed.
QUIET_ACTIONS = {
    "get_screen_snapshot", "list_processes", "get_clipboard", "get_pc_status",
    "system_info", "list_windows", "telemetry", "phone_notifications", "phone_contacts", "read_window_text",
    "list_dir", "server_status", "find_files", "git_repos", "sys_updates", "storage", "network", "security",
    "services_list", "timers", "journal",
}


def _ping_payload() -> Dict[str, Any]:
    counts = {"pc": 0, "mobile": 0, "server": 0}
    for space in list(space_registry.spaces.values()):
        dm = space.objects.get("device_manager")
        for dev in (dm.devices.values() if dm else []):
            if dev.device_type in counts:
                counts[dev.device_type] += 1
    owner = accounts.home_owner()
    public = os.getenv("WILLY_PUBLIC_URL", "").strip()
    return _registry.build_ping(
        APP_VERSION, _broker.clean_origin(public) or "" if public else "",
        owner["email"] if owner and not owner["email"].endswith("@local.willy") else None,
        len(accounts.list_users()), counts, ai_config.current().provider, usage_counter.unsent())


async def _registry_loop():
    """Once a day this hub tells the master it exists (see server/registry.py for exactly what is sent;
    WILLY_TELEMETRY=off stops it)."""
    await asyncio.sleep(90)
    while True:
        delay = _registry.PING_EVERY_SEC
        if _registry.telemetry_enabled():
            payload = await asyncio.to_thread(_ping_payload)
            ok = await asyncio.to_thread(_registry.send_ping_sync, _broker.master_url(), payload)
            if ok and payload["usage"]:
                usage_counter.mark_sent(max(payload["usage"]))
            elif not ok:
                delay = 3600
        await asyncio.sleep(delay)


async def _sweep_loop():
    while True:
        await asyncio.sleep(5)
        for space in list(space_registry.spaces.values()):
            token = _spaces.use(space)
            try:
                await device_manager.sweep_stale()
            except Exception as e:
                print(f"[Hub] Stale sweep error ({space.space_id}): {e}")
            finally:
                _spaces.reset(token)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if settings.using_default_token:
        print("!" * 72)
        print("[Security] WILLY_REMOTE_TOKEN is the public default. Anyone who knows it can")
        print("           control your PC. Set a long random WILLY_REMOTE_TOKEN in server/.env")
        print("           and in the PC client / mobile app settings.")
        print("!" * 72)
    # The owner's space plus every account space that exists (their reminders must fire even
    # when none of their devices is connected).
    space_registry.ensure_started(space_registry.home())
    spaces_dir = DATA_DIR / "spaces"
    if spaces_dir.is_dir():
        for folder in sorted(spaces_dir.iterdir()):
            if folder.is_dir():
                space_registry.ensure_started(space_registry.get(folder.name))
    print(f"[Hub] Accounts: {accounts.backend} database, sign-up {accounts.signup_mode()}, "
          f"Google sign-in {'on' if accounts.firebase.project_id else 'off'}, {len(space_registry.spaces)} space(s).")
    tasks = [asyncio.create_task(_sweep_loop()), asyncio.create_task(server_monitor.run()),
             asyncio.create_task(_registry_loop())]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        for space in space_registry.spaces.values():
            for task in space.objects.get("tasks", []):
                task.cancel()


app = FastAPI(
    title="Willy Central Server Hub",
    description="Central Brain & Device Discovery Gateway for Mobile App and Windows PC",
    version=APP_VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1500)

security = HTTPBearer(auto_error=False)


def _expected_token() -> str:
    return os.getenv("WILLY_REMOTE_TOKEN", DEFAULT_TOKEN).strip()


def _legacy_tokens() -> List[str]:
    """Previous tokens, still accepted while devices switch over (WILLY_LEGACY_TOKENS, comma
    separated). A device that connects with one is sent the current token right away."""
    return [t.strip() for t in os.getenv("WILLY_LEGACY_TOKENS", "").split(",") if t.strip()]


def _is_legacy_token(provided: Optional[str]) -> bool:
    import hmac

    p = (provided or "").strip().encode()
    return bool(p) and any(hmac.compare_digest(p, t.encode()) for t in _legacy_tokens())


def _token_ok(provided: Optional[str]) -> bool:
    import hmac

    expected = _expected_token()
    p = (provided or "").strip()
    if not p:
        return False
    return (bool(expected) and hmac.compare_digest(p.encode(), expected.encode())) or _is_legacy_token(p)


class _MaskTokens(logging.Filter):
    """Keeps tokens out of the server logs (devices pass them in the WebSocket URL)."""

    _RE = re.compile(r"(token=)[^&\s\"']+", re.I)

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str) and "token=" in record.msg.lower():
            record.msg = self._RE.sub(r"\1***", record.msg)
        if record.args:
            record.args = tuple(self._RE.sub(r"\1***", a) if isinstance(a, str) else a for a in
                                (record.args if isinstance(record.args, tuple) else (record.args,)))
        return True


for _logger_name in ("uvicorn.access", "uvicorn.error", "uvicorn"):
    logging.getLogger(_logger_name).addFilter(_MaskTokens())


_owner_cache: List[Any] = [0.0, None]


def _principal_for(raw: Optional[str]) -> Optional[Principal]:
    """The hub token (or a retired one) means the owner; a session token or device key means
    its account. None when nothing valid was given."""
    raw = (raw or "").strip()
    if not raw:
        return None
    if _token_ok(raw):
        if time.time() - _owner_cache[0] > 60 or _owner_cache[1] is None:
            _owner_cache[:] = [time.time(), accounts.owner_principal()]
        return _owner_cache[1]
    return accounts.resolve(raw)


def _provided_token(auth, x_willy_token, token) -> Optional[str]:
    if auth and auth.credentials:
        return auth.credentials
    return x_willy_token or token


async def verify_auth(
    request: Request,
    auth: Optional[HTTPAuthorizationCredentials] = Security(security),
    x_willy_token: Optional[str] = Header(None),
    token: Optional[str] = None,
) -> Principal:
    """Every API call: who is it, and which space do they work in. (Async on purpose: the
    space it makes current carries over into the endpoint.)"""
    principal = _principal_for(_provided_token(auth, x_willy_token, token))
    if principal is None:
        raise HTTPException(status_code=401, detail="Unauthorized: sign in again (invalid or expired token).")
    _enter_space(principal)
    request.state.principal = principal
    return principal


async def verify_host_admin(principal: Principal = Depends(verify_auth)) -> Principal:
    """The hub's own server and the account list: the owner only."""
    if not (principal.is_home and principal.is_admin):
        raise HTTPException(status_code=403, detail=HOST_ONLY)
    return principal


# --- Models ---
class CommandRequest(BaseModel):
    query: str
    target_device_id: Optional[str] = None
    speak_on_pc: bool = False


class RingRequest(BaseModel):
    message: Optional[str] = "Find My Device"
    duration_sec: Optional[int] = 10


class ClipboardRequest(BaseModel):
    text: str


class QuickDropRequest(BaseModel):
    url: Optional[str] = None
    text: Optional[str] = None
    title: Optional[str] = None


class MediaRequest(BaseModel):
    action: str  # play_pause, next, prev, mute, volume_set
    level: Optional[int] = None


class DeviceActionRequest(BaseModel):
    action: str
    payload: Dict[str, Any] = {}
    source: Optional[str] = None


# --- Helpers ---

def _server_info() -> Dict[str, Any]:
    counts = device_manager.online_counts()
    return {
        "version": APP_VERSION,
        "started_at": SERVER_STARTED_AT,
        "uptime_sec": round(time.time() - SERVER_STARTED_AT),
        "server_time": time.time(),
        "user_time": timeutil.user_now().isoformat(),
        "llm": {
            "provider": orchestrator.provider,
            "model": orchestrator.model,
            "ready": orchestrator.client is not None,
            "last_error": orchestrator.last_error,
        },
        "fast_path_enabled": settings.FAST_PATH_ENABLED,
        "using_default_token": settings.using_default_token,
        "pcs_online": counts.get("pc", 0),
        "phones_online": counts.get("mobile", 0),
        "observers": len(device_manager.observers),
    }


def _snapshot() -> Dict[str, Any]:
    return {
        "type": "snapshot",
        "devices": device_manager.get_all_devices(),
        "activity": activity_log.recent(25),
        "stats": activity_log.stats(),
        "server": _server_info(),
        "watches": watchdog.list(),
        "timestamp": time.time(),
    }


def _slim(result: Any) -> Any:
    """Drops base64 payloads from tool results echoed back to clients."""
    if not isinstance(result, dict):
        return result
    return {k: v for k, v in result.items() if k not in ("image_base64", "audio_base64")}


def _source_of(request: Optional[Request], body: Optional[Dict[str, Any]] = None, fallback: str = "api") -> str:
    source = None
    if body and isinstance(body.get("source"), str):
        source = body["source"]
    elif request is not None:
        source = request.headers.get("x-willy-client") or request.query_params.get("source")
    return (source or fallback).strip().lower()[:24] or fallback


# What speech-to-text returns for silence, breathing or background noise.
_SILENCE_TRANSCRIPTS = {
    "", "you", "thank you", "thanks", "thank you for watching", "thanks for watching", "bye",
    "subscribe", "please subscribe", "like and subscribe", "music", "applause", "silence",
}


def is_meaningful_transcript(text: Optional[str]) -> bool:
    """False for transcripts with no words (".", "...") and the phrases speech-to-text
    invents on silence, so noise never reaches the brain as a command."""
    words = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in (text or "").lower()).split()
    return bool(words) and " ".join(words) not in _SILENCE_TRANSCRIPTS


def _notify_reminders_changed() -> None:
    device_manager.publish({
        "type": "reminders_changed",
        "reminders": morning_service.list_reminders(),
        "alarms": morning_service.list_alarms(),
        "timestamp": time.time(),
    })


PC_STATUS_KEYS = ("battery_pct", "is_charging", "cpu_pct", "ram_pct", "disk_free_gb", "volume_level", "is_muted",
                  "active_window", "uptime_hours", "wifi_ssid", "last_ping_ms")
PHONE_STATUS_KEYS = ("battery_pct", "is_charging", "network_type", "storage_free_gb", "ram_pct", "screen_on",
                     "unread_notifications", "unread_whatsapp", "missed_calls", "last_ping_ms")


def _kind(device: Optional[str]) -> str:
    """'pc' / 'phone' (tool vocabulary) -> device_type ('pc' / 'mobile')."""
    return "mobile" if normalize_device(device) == "phone" else "pc"


def _offline_result(device_type: str, device_id: Optional[str] = None) -> Dict[str, Any]:
    """Result for a request that needs a device that isn't connected: says when and why."""
    dev = device_manager.last_known(device_type, device_id)
    code = "PC_OFFLINE" if device_type == "pc" else "PHONE_OFFLINE"
    if dev is None:
        what = "Windows PC" if device_type == "pc" else "phone"
        return {"success": False, "error": code, "reply": f"Your {what} hasn't connected to Willy yet."}
    return {
        "success": False,
        "error": code,
        "reply": dev.presence_sentence(),
        "device": dev.name,
        "offline_reason": dev.offline_reason,
        "offline_since": describe_when(dev.offline_since or dev.last_seen),
        "hint": "Tell the user in one sentence; offer to alert them when it is back (watch_device).",
    }


def _device_status(device_type: str) -> Dict[str, Any]:
    dev = device_manager.last_known(device_type)
    if dev is None:
        what = "PC" if device_type == "pc" else "phone"
        return {"success": False, "error": "NEVER_CONNECTED", "reply": f"Your {what} hasn't connected to Willy yet."}
    keys = PC_STATUS_KEYS if device_type == "pc" else PHONE_STATUS_KEYS
    telemetry = {k: dev.telemetry.get(k) for k in keys if dev.telemetry.get(k) not in (None, "", [])}
    top = dev.telemetry.get("top_processes") or []
    if device_type == "pc" and top:
        telemetry["top_processes"] = [p.get("name") for p in top[:5] if isinstance(p, dict)]
    online = dev.status == "online"
    return {"success": True, "device": dev.name, "online": online, "presence": dev.presence_sentence(),
            "values_are_last_known": not online, "telemetry": telemetry}


def _when_text(rem: Dict[str, Any]) -> str:
    today = timeutil.user_now().date()
    day = rem.get("date")
    if day == today.isoformat():
        return f"at {rem.get('time')}"
    if day == (today + timedelta(days=1)).isoformat():
        return f"tomorrow at {rem.get('time')}"
    return f"on {day} at {rem.get('time')}"


def _schedule_reminder(args: Dict[str, Any]) -> Dict[str, Any]:
    text = str(args.get("text") or "").strip() or "Reminder"
    minutes = args.get("in_minutes")
    try:
        minutes = float(minutes) if minutes not in (None, "") else None
    except (TypeError, ValueError):
        minutes = None
    if not minutes and timeutil.parse_clock(args.get("time")) is None:
        return {"success": False, "error": "I need a time for that reminder (a clock time or 'in N minutes')."}
    rem = morning_service.add_reminder(text=text, remind_time=str(args.get("time") or ""),
                                       date=args.get("date"), in_minutes=minutes)
    _notify_reminders_changed()
    return {"success": True, "action": "schedule_reminder", "reminder": rem,
            "message": f"Reminder set {_when_text(rem)}: {text}."}


async def _set_timer(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        minutes = float(args.get("minutes"))
    except (TypeError, ValueError):
        return {"success": False, "error": "How long should the timer be?"}
    if not 0 < minutes <= 24 * 60:
        return {"success": False, "error": "Timers can run from a second up to 24 hours."}
    label = str(args.get("label") or "").strip()
    seconds = max(1, round(minutes * 60))
    length = describe_duration(seconds)
    phone = device_manager.get_first_online_mobile()
    if phone is not None:
        res = await device_manager.send_to_device(phone.device_id, "phone_timer",
                                                  {"seconds": seconds, "label": label or "Willy timer"})
        if res.get("success"):
            return {**res, "message": res.get("message") or f"Timer set for {length} on your phone."}
    rem = morning_service.add_reminder(text=f"Timer: {label}" if label else "Your timer is done.",
                                       in_minutes=seconds / 60, kind="timer")
    _notify_reminders_changed()
    return {"success": True, "reminder": rem, "message": f"Timer set for {length}. I'll alert you when it's done."}


def _watch(args: Dict[str, Any], source: str) -> Dict[str, Any]:
    device = normalize_device(args.get("device"))
    event = args.get("event") if args.get("event") in ("online", "offline", "both") else "online"
    repeat = bool(args.get("repeat"))
    then = str(args.get("then") or "").strip() or None
    dev = device_manager.last_known(_kind(device)) if device in ("pc", "phone") else device_manager.devices.get(device)
    name = dev.name if dev else ("your PC" if device == "pc" else "your phone")
    if event == "online" and not repeat and dev is not None and dev.status == "online":
        return {"success": True, "already_online": True, "message": f"{dev.name} is online right now."}
    watch = watchdog.add(device, event, repeat, then, source)
    if repeat and event == "both":
        message = f"Watchdog on: I'll alert your phone whenever {name} goes offline or comes back online."
    elif event == "offline":
        message = f"OK, I'll alert you when {name} goes offline."
    else:
        message = f"OK, I'll alert your phone when {name} comes online" + (f", then: {then}." if then else ".")
    return {"success": True, "watch": watch, "message": message}


def _cancel_reminder(args: Dict[str, Any]) -> Dict[str, Any]:
    rid = str(args.get("reminder_id") or "").strip()
    if rid:
        ok = morning_service.delete_reminder(rid)
        if ok:
            _notify_reminders_changed()
        return {"success": ok, "message": "Reminder cancelled." if ok else None,
                "error": None if ok else "I couldn't find that reminder."}
    matches = morning_service.find_reminders(str(args.get("text") or ""))
    if not matches:
        return {"success": False, "error": "I couldn't find a matching upcoming reminder."}
    for rem in matches:
        morning_service.delete_reminder(rem["id"])
    _notify_reminders_changed()
    names = ", ".join(r.get("text", "") for r in matches[:3])
    return {"success": True, "cancelled": len(matches), "message": f"Cancelled: {names}."}


def _upcoming() -> Dict[str, Any]:
    reminders = [{"id": r["id"], "text": r.get("text"), "when": _when_text(r), "kind": r.get("kind", "reminder")}
                 for r in morning_service.upcoming_reminders()]
    alarms = [{"id": a["id"], "time": a.get("time"), "label": a.get("label"), "enabled": a.get("enabled", True)}
              for a in morning_service.list_alarms()]
    return {"success": True, "reminders": reminders, "alarms": alarms}


# PowerShell that installs software, deletes things, downloads, or changes system/security
# settings only runs after the user said yes to it.
_RISKY_POWERSHELL = re.compile(
    r"\b(?:winget|choco|scoop|msiexec|install-(?:module|package|script)|uninstall|remove-item|rmdir|rd|del|erase"
    r"|format-volume|clear-disk|set-executionpolicy|reg(?:\.exe)?\s+(?:add|delete|import)|set-itemproperty"
    r"|new-itemproperty|remove-itemproperty|bcdedit|netsh|stop-computer|restart-computer|disable-\w+|enable-\w+"
    r"|set-mppreference|add-mppreference|invoke-webrequest|iwr|invoke-restmethod|irm|curl|wget|start-bitstransfer"
    r"|takeown|icacls|net\s+(?:user|localgroup)|set-service|stop-service|new-service|schtasks)\b", re.I)
_YES = re.compile(r"\b(?:yes|yeah|yep|ok|okay|sure|confirm(?:ed)?|go ahead|do it|proceed|approved?|install it)\b", re.I)


def _powershell_blocked(args: Dict[str, Any], user_text: str) -> Optional[Dict[str, Any]]:
    command = str(args.get("command") or "")
    if not _RISKY_POWERSHELL.search(command):
        return None
    if args.get("confirmed") and _YES.search(user_text or ""):
        return None
    return {"success": False, "error": "NEEDS_CONFIRMATION", "command": command,
            "reply": "This command installs, deletes or changes something on the PC. Show the user the exact "
                     "command and ask them to confirm before running it."}


def _is_unsupported(res: Dict[str, Any]) -> bool:
    text = f"{res.get('error', '')} {res.get('reply', '')}".lower()
    return res.get("success") is False and ("not supported" in text or "unknown action" in text)


async def _show_note_on_pc(pc_id: str, title: str, text: str) -> Dict[str, Any]:
    res = await device_manager.send_to_device(pc_id, "show_note", {"title": title, "text": text})
    if _is_unsupported(res):  # an older PC client: pop-up + clipboard instead
        await device_manager.send_to_device(pc_id, "set_clipboard", {"text": text})
        res = await device_manager.send_to_device(pc_id, "notify", {"title": title, "message": text[:240]})
        if res.get("success", True) is not False:
            res = {"success": True, "message": "Shown on your PC and copied to its clipboard."}
    return res


async def _share_conversation(args: Dict[str, Any], session_key: str) -> Dict[str, Any]:
    pairs = orchestrator.recent_exchanges(session_key, int(args.get("messages") or 6))
    if not pairs:
        return {"success": False, "error": "There's no conversation to share yet."}
    text = "\n\n".join(f"You: {p['user']}\nWilly: {p['assistant']}" for p in pairs)
    if _kind(args.get("device")) == "mobile":
        phone = device_manager.get_first_online_mobile()
        if phone is None:
            return _offline_result("mobile")
        return await device_manager.send_to_device(phone.device_id, "quickdrop", {"text": text, "title": "Willy chat"})
    pc = device_manager.get_first_online_pc()
    if pc is None:
        return _offline_result("pc")
    return await _show_note_on_pc(pc.device_id, "Willy chat", text)


async def _phone_tool(tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    args = dict(args)
    wanted = str(args.pop("phone", "") or "").strip()
    phone = device_manager.get_first_online_mobile(wanted or None)
    if wanted and (phone is None or device_manager.find_phone(wanted, [phone]) is None):
        named = device_manager.find_phone(wanted)
        if named is None:
            names = ", ".join(d.name for d in device_manager.online_phones()) or "none"
            return {"success": False, "error": f"I don't know a phone called '{wanted}'. Online phones: {names}."}
        return {"success": False, "error": f"Your {named.name} isn't online right now, so I didn't use another phone."}
    if phone is None:
        phone = await wake_phone_by_push()  # the app was asleep: a silent push wakes it
    if phone is None:
        return _offline_result("mobile")
    action = PHONE_TOOL_ACTIONS[tool_name]
    if tool_name == "ring_phone":
        payload: Dict[str, Any] = {"message": "Find My Phone", "duration_sec": 20}
    elif tool_name == "send_to_phone":
        payload = {"url": args.get("url"), "text": args.get("text"), "title": "From Willy"}
    elif tool_name == "set_phone_alarm":
        clock = timeutil.parse_clock(args.get("time"))
        if clock is None:
            return {"success": False, "error": f"I couldn't read the time '{args.get('time')}'."}
        payload = {"hour": clock[0], "minute": clock[1], "label": args.get("label") or "Willy alarm"}
    elif tool_name == "read_phone_notifications":
        payload = {"limit": max(1, min(int(args.get("limit") or 10), 30))}
    elif tool_name == "phone_flashlight":
        payload = {"on": bool(args.get("on", True))}
    else:
        payload = {k: v for k, v in args.items() if v is not None}
    res = await device_manager.send_to_device(phone.device_id, action, payload)
    if _is_unsupported(res):
        res["hint"] = "The Willy app on the phone is an older version without this skill; it needs updating."
    return res


ADMIN_TOPICS = {"updates": "sys_updates", "storage": "storage", "network": "network", "security": "security",
                "services": "services_list", "timers": "timers", "journal": "journal", "apply_updates": "apply_updates",
                "cleanup": "cleanup", "service_boot": "service_boot", "reboot": "reboot", "cancel_reboot": "cancel_reboot"}
ADMIN_NEEDS_YES = {"apply_updates", "cleanup", "service_boot", "reboot"}
ADMIN_SLOW = {"sys_updates": 240, "apply_updates": 1900, "storage": 120, "cleanup": 320, "journal": 45, "security": 75}


async def _server_admin_tool(args: Dict[str, Any], user_text: str) -> Dict[str, Any]:
    topic = str(args.get("topic") or "")
    if topic == "history":
        if not _is_host_space():
            return {"success": False, "error": "History graphs are kept for the hub's own server only."}
        series = server_monitor.history_series(float(args.get("hours") or 24), 48)
        pts = [p for p in series["points"] if p.get("cpu") is not None]
        if not pts:
            return {"success": False, "error": "No history recorded yet; it fills up a minute at a time."}
        peak = lambda k: max((p[k] or 0) for p in pts)
        mean = lambda k: round(sum((p[k] or 0) for p in pts) / len(pts), 1)
        return {"success": True, "hours": series["hours"], "points": pts[-48:],
                "message": f"Last {series['hours']:g} h: CPU avg {mean('cpu')}% (peak {peak('cpu'):.0f}%), "
                           f"RAM avg {mean('ram')}% (peak {peak('ram'):.0f}%), swap peak {peak('swap'):.0f}%."}
    action = ADMIN_TOPICS.get(topic)
    if not action:
        return {"success": False, "error": f"Unknown topic '{topic}'."}
    srv = device_manager.get_server()
    if srv is None:
        return {"success": False, "error": "The Willy server agent isn't connected."}
    if topic in ADMIN_NEEDS_YES and not (args.get("confirmed") and _YES.search(user_text or "")):
        what = {"apply_updates": "install the OS updates on the server" + (" (security only)" if args.get("security_only") else ""),
                "cleanup": f"clean up {args.get('what') or 'that'} on the server",
                "service_boot": f"{'enable' if args.get('enable') else 'disable'} {args.get('name')} at boot",
                "reboot": "reboot the server (your websites go down for about a minute)"}[topic]
        return {"success": False, "error": "NEEDS_CONFIRMATION", "ask": f"Shall I {what}? Say yes to confirm.",
                "reply": "Ask the user to confirm first."}
    payload = {k: v for k, v in args.items() if k not in ("topic", "confirmed")}
    return await device_manager.send_to_device(srv.device_id, action, payload, timeout=ADMIN_SLOW.get(action, 60))


GIT_NEEDS_YES = {"push", "commit", "restore", "discard"}


async def refresh_repo_index(dev) -> None:
    res = await device_manager.send_to_device(dev.device_id, "git_repos", {"refresh": True}, timeout=60)
    if isinstance(res, dict) and res.get("success"):
        _repo_index[dev.device_id] = (time.time(), res.get("repos") or [])


def repo_context() -> str:
    lines = []
    for device_id, (_at, items) in _repo_index.items():
        dev = device_manager.devices.get(device_id)
        if not dev or not items:
            continue
        where = "server" if dev.device_type == "server" else "PC"
        # Compact: name, then the branch only when it isn't main/master, * for uncommitted changes.
        parts = [r["name"] + ("" if r.get("branch") in ("main", "master") else f"@{r.get('branch')}")
                 + ("*" if r.get("changed_files") else "") + (f"(-{r['behind']})" if r.get("behind") else "")
                 for r in items[:40]]
        lines.append(f"- GIT PROJECTS on the {where}: " + ", ".join(parts) + ("" if len(items) <= 40 else f" +{len(items) - 40} more"))
    return "\n".join(lines)


async def _git_tool(pc_id: Optional[str], args: Dict[str, Any], user_text: str) -> Dict[str, Any]:
    on_server = str(args.get("device") or "").lower() == "server"
    dev = device_manager.get_server() if on_server else (device_manager.devices.get(pc_id) if pc_id else None)
    if dev is None:
        return _offline_result("pc") if not on_server else {"success": False, "error": "The server agent isn't connected."}
    action = str(args.get("action") or "status").lower()
    if action in GIT_NEEDS_YES and not (args.get("confirmed") and _YES.search(user_text or "")):
        what = {"push": "push it to GitHub", "commit": "commit the changes", "restore": "throw away the uncommitted changes",
                "discard": "throw away the uncommitted changes"}[action]
        return {"success": False, "error": "NEEDS_CONFIRMATION",
                "ask": f"Shall I {what} in {args.get('repo') or 'that project'} on the {'server' if on_server else 'PC'}? Say yes to confirm.",
                "reply": "Ask the user to confirm first."}
    payload = {k: v for k, v in args.items() if k not in ("device", "confirmed")}
    payload["action"] = "list" if action == "list" else action
    res = await device_manager.send_to_device(dev.device_id, "git", payload, timeout=320)
    if isinstance(res, dict) and res.get("success") and action not in ("status", "log", "diff", "branches"):
        device_manager.spawn(refresh_repo_index(dev))  # the brain's project list follows the change
    return res


async def _server_tool(tool_name: str, args: Dict[str, Any], user_text: str) -> Dict[str, Any]:
    """Tools that act on the server through its agent. Read-only things run directly; changes
    need the user's yes (confirmed=true after they said it)."""
    srv = device_manager.get_server()
    if srv is None:
        return {"success": False, "error": "The Willy server agent isn't connected (pm2 app willy-server-agent)."}
    confirmed = bool(args.get("confirmed")) and bool(_YES.search(user_text or ""))
    args = {k: v for k, v in args.items() if k not in ("device", "confirmed")}
    if tool_name == "server_shell":
        command = str(args.get("command") or "")
        if is_destructive(command):
            return {"success": False, "error": "Willy won't run that: it could wipe or power off the server."}
        if not is_read_only(command) and not confirmed:
            return {"success": False, "error": "NEEDS_CONFIRMATION", "command": command,
                    "ask": f"That changes the server: `{command}`. Shall I run it? Say yes to confirm.",
                    "reply": "This command changes the server. Show the user the exact command and ask them to confirm."}
        return await device_manager.send_to_device(srv.device_id, "run_shell", args, timeout=float(args.get("timeout") or 60) + 10)
    if tool_name == "server_control":
        action = str(args.get("action") or "status").lower()
        if action not in ("status", "logs") and not confirmed:
            return {"success": False, "error": "NEEDS_CONFIRMATION",
                    "ask": f"{action.capitalize()} the {args.get('kind')} {args.get('name')} on the server? Say yes to confirm.",
                    "reply": f"Ask the user to confirm: {action} {args.get('kind')} '{args.get('name')}' on the server."}
        return await device_manager.send_to_device(srv.device_id, "control", args, timeout=100)
    if tool_name == "manage_file" and str(args.get("op", "")).lower() == "delete" and not confirmed:
        return {"success": False, "error": "NEEDS_CONFIRMATION",
                "reply": "Deleting needs the user's OK: say which server file goes to ~/.willy-trash and ask to confirm."}
    if tool_name == "show_panel":
        ui = _panel({**args, "device": "server"})
        return {"success": True, "message": "Here are the server's files.", "ui": ui}
    action = {"send_file_to_phone": "send_file_to_phone", "manage_file": "manage_file", "find_files": "find_files",
              "list_processes": "list_processes", "get_device_status": "server_status",
              "get_pc_status": "server_status"}.get(tool_name, tool_name)
    return await device_manager.send_to_device(srv.device_id, action, args, timeout=120)


def make_tool_executor(target_id: Optional[str], session_key: str = "default", source: str = "api",
                       user_text: str = ""):
    """Routes LLM tool calls to the hub itself, the phone, or the target PC. Tools for a
    device that is offline come back with when/why it went offline instead of running."""

    async def executor(tool_name: str, tool_args: Dict[str, Any]) -> Dict[str, Any]:
        args = tool_args or {}
        if tool_name == "schedule_reminder":
            return _schedule_reminder(args)
        if tool_name == "set_alarm":
            if timeutil.parse_clock(args.get("time")) is None:
                return {"success": False, "error": f"I couldn't read the alarm time '{args.get('time')}'."}
            label = str(args.get("label") or "").strip()
            alm = morning_service.add_alarm(alarm_time=args.get("time", ""), label=label or "Willy alarm")
            _notify_reminders_changed()
            return {"success": True, "action": "set_alarm", "alarm": alm,
                    "message": f"Daily alarm set for {alm['time']}" + (f": {label}." if label else ".")}
        if tool_name == "get_morning_briefing":
            briefing = await morning_service.generate_briefing(
                include_audio=False, pc_online=device_manager.get_first_online_pc() is not None
            )
            return {"success": True, "briefing": briefing}
        if tool_name == "get_device_status":
            return _device_status(_kind(args.get("device")))
        if tool_name == "watch_device":
            return _watch(args, source)
        if tool_name == "list_watches":
            return {"success": True, "watches": watchdog.list()}
        if tool_name == "cancel_watch":
            removed = watchdog.remove(args.get("watch_id"), args.get("device"))
            return {"success": True, "removed": removed,
                    "message": f"Cancelled {removed} alert{'s' if removed != 1 else ''}." if removed else "No alerts were set."}
        if tool_name == "list_reminders":
            return _upcoming()
        if tool_name == "cancel_reminder":
            return _cancel_reminder(args)
        if tool_name == "set_timer":
            return await _set_timer(args)
        if tool_name == "share_conversation":
            return await _share_conversation(args, session_key)
        if tool_name == "send_to_pc":
            if not target_id:
                return _offline_result("pc")
            return await _show_note_on_pc(target_id, str(args.get("title") or "From Willy"), str(args.get("text") or ""))
        if tool_name in PHONE_TOOLS:
            return await _phone_tool(tool_name, args)
        if tool_name == "show_panel":
            ui = _panel(args)
            if ui is None:
                return {"success": False, "error": "Panels are files, screenshot or camera."}
            what = {"files": "Here are your files.", "screenshot": "Here's your PC screen.",
                    "camera": "Camera's open. Tap the shutter when you're ready."}[ui["panel"]]
            return {"success": True, "message": what, "ui": ui}
        if tool_name in ("fix_site", "get_server_status", "server_app_logs", "restart_server_app") and not _is_host_space():
            return {"success": False, "error": HOST_ONLY}
        if tool_name == "fix_site":
            from server import site_doctor

            domain = str(args.get("domain") or "")
            if str(args.get("action") or "diagnose") == "start":
                if not (args.get("confirmed") and _YES.search(user_text or "")):
                    return {"success": False, "error": "NEEDS_CONFIRMATION",
                            "ask": f"Start the app behind {domain} again? Say yes to confirm.",
                            "reply": "Ask the user to confirm starting the site's app."}
                return await asyncio.to_thread(site_doctor.start, domain)
            info = await asyncio.to_thread(site_doctor.diagnose, domain)
            if info.get("success"):
                info["message"] = info.get("explanation")
            return info
        if tool_name == "get_server_status":
            if args.get("refresh") or not server_monitor.snapshot:
                await asyncio.to_thread(server_monitor.collect, bool(args.get("refresh")))
            snap = server_monitor.snapshot
            return {"success": True, "message": server_monitor.summary(), "system": snap.get("system"),
                    "apps": snap.get("apps"), "services": snap.get("services"), "containers": snap.get("containers"),
                    "sites": [{k: v for k, v in s.items() if k != "error" or v} for s in snap.get("sites") or []]}
        if tool_name == "server_app_logs":
            text = await asyncio.to_thread(tail_app_log, str(args.get("app") or ""), int(args.get("lines") or 40))
            return {"success": bool(text), "logs": text[-3000:] or "", "error": None if text else "No logs for that app."}
        if tool_name == "restart_server_app":
            app_name = str(args.get("app") or "")
            apps = server_monitor.snapshot.get("apps") or await asyncio.to_thread(pm2_apps)
            known = {a.get("name") for a in apps}
            if app_name not in known:
                return {"success": False, "error": f"There's no server app called '{app_name}'. Apps: {', '.join(sorted(n for n in known if n))}."}
            if not (args.get("confirmed") and _YES.search(user_text or "")):
                return {"success": False, "error": "NEEDS_CONFIRMATION",
                        "reply": f"Restarting {app_name} interrupts it briefly: ask the user to confirm first."}
            from server.server_monitor import _pm2_binary, _run
            import subprocess

            pm2 = _pm2_binary()
            if app_name == "willy-server":  # restarting ourselves: answer first, restart a moment later
                asyncio.get_running_loop().call_later(2, lambda: subprocess.Popen([pm2, "restart", app_name]))
                return {"success": True, "message": "Restarting the Willy hub now; it'll be back in a few seconds."}
            ok = await asyncio.to_thread(_run, [pm2, "restart", app_name], 30) is not None
            return {"success": ok, "message": f"Restarted {app_name}." if ok else None,
                    "error": None if ok else f"pm2 couldn't restart {app_name}."}
        if tool_name == "save_feedback":
            return save_feedback(args.get("note"), "user", asked=user_text[:300])
        if tool_name == "get_weather":
            return await live_info.get_weather(str(args.get("place") or ""))
        if tool_name == "place_time":
            return await live_info.place_time(str(args.get("place") or ""))
        if tool_name == "web_lookup":
            return await live_info.web_lookup(str(args.get("query") or ""))

        if tool_name == "git":
            return await _git_tool(target_id, args, user_text)
        if tool_name == "server_admin":
            return await _server_admin_tool(args, user_text)
        if tool_name in ("server_shell", "server_control") or str(args.get("device") or "").lower() == "server":
            return await _server_tool(tool_name, args, user_text)
        if tool_name == "execute_powershell":
            blocked = _powershell_blocked(args, user_text)
            if blocked:
                return blocked
            args = {"command": args.get("command", "")}
        if tool_name == "manage_file" and str(args.get("op", "")).lower() == "delete":
            if not (args.get("confirmed") and _YES.search(user_text or "")):
                return {"success": False, "error": "NEEDS_CONFIRMATION",
                        "reply": "Deleting needs the user's OK: say which file will go to the Recycle Bin and ask them to confirm."}
        if not target_id:
            return _device_status("pc") if tool_name == "get_pc_status" else _offline_result("pc")
        return await device_manager.send_to_device(target_device_id=target_id, action=tool_name, payload=args)

    return executor


PANELS = {"files", "screenshot", "camera"}


def _panel(args: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """A `ui` directive for the call screens: which panel to pop open, for which PC."""
    panel = str(args.get("panel") or "").lower()
    if panel not in PANELS:
        return None
    if str(args.get("device") or "").lower() == "server" and panel == "files":
        srv = device_manager.get_server()
        return {"panel": panel, "device_id": srv.device_id if srv else None, "path": args.get("path") or None,
                "title": args.get("title") or "Willy Server"}
    pc = device_manager.resolve_pc(None)
    return {"panel": panel, "device_id": pc.device_id if pc else None,
            "path": args.get("path") or None, "title": args.get("title") or None}


async def _run_hub_intent(intent: FastIntent, base: Dict[str, Any], source: str) -> Dict[str, Any]:
    """Fast-path requests the hub answers itself (no LLM, no device round-trip)."""
    tool = intent.tool
    if tool == "server_status":
        if not _is_host_space():
            srv = device_manager.get_server()
            if srv is None:
                return {**base, "success": False, "reply": "No server is connected to your Willy. Install the Willy server agent on it."}
            res = await device_manager.send_to_device(srv.device_id, "server_status", {}, timeout=30)
            return {**base, "success": bool(res.get("success")), "reply": res.get("message") or res.get("error") or "No answer from the server."}
        if not server_monitor.snapshot or time.time() - server_monitor.snapshot.get("checked_at", 0) > 120:
            await asyncio.to_thread(server_monitor.collect)
        return {**base, "success": True, "reply": server_monitor.summary()}
    if tool == "show_panel":
        return {**base, "success": True, "reply": intent.reply, "ui": _panel(intent.args)}
    if tool == "presence":
        dev = device_manager.last_known(intent.args.get("device", "pc"))
        if dev is None:
            what = "PC" if intent.args.get("device") == "pc" else "phone"
            reply = f"Your {what} hasn't connected to Willy yet."
        elif dev.status == "online":
            ping = dev.telemetry.get("last_ping_ms")
            reply = f"Yes, {dev.name} is online" + (f", {ping} milliseconds from the hub." if ping else ".")
        else:
            reply = dev.presence_sentence() + " Want me to tell you when it's back?"
        return {**base, "success": True, "reply": reply}
    if tool == "watch":
        res = _watch(intent.args, source)
    elif tool == "unwatch":
        removed = watchdog.remove(device=intent.args.get("device"))
        res = {"success": True, "message": "Watchdog off." if removed else "There's no watchdog running."}
    elif tool == "schedule_reminder":
        res = _schedule_reminder(intent.args)
    elif tool == "set_timer":
        res = await _set_timer(intent.args)
    else:
        return {**base, "success": False, "reply": "I can't do that yet."}
    ok = res.get("success", True) is not False
    reply = res.get("message") if ok else (res.get("error") or res.get("reply"))
    return {**base, "success": ok, "reply": reply or "Done.", "tool_used": tool, "tool_args": intent.args,
            "tools": [{"name": tool, "args": intent.args, "success": ok}]}


async def _run_fast_intent(intent: FastIntent, target_id: Optional[str], source: str = "api") -> Optional[Dict[str, Any]]:
    """Executes a fast-path intent; None means 'let the LLM handle it'."""
    base = {"fast_path": True, "tools": [], "tool_used": None}

    if intent.tool == "clock":
        now = timeutil.user_now()
        if intent.args.get("what") == "date":
            reply = f"Today is {now.strftime('%A, %B')} {now.day}, {now.year}."
        else:
            reply = f"It's {now.strftime('%I:%M %p').lstrip('0')}."
        return {**base, "success": True, "reply": reply}

    if intent.target == "hub":
        return await _run_hub_intent(intent, base, source)

    if intent.tool == "telemetry":
        wants_phone = intent.args.get("device") == "mobile"
        if wants_phone:
            dev = device_manager.get_first_online_mobile()
        else:
            dev = device_manager.devices.get(target_id) if target_id else None
        if dev is None:
            known = device_manager.last_known("mobile" if wants_phone else "pc")
            if known is None:
                # Answer directly instead of paying for an LLM call that can only say the same.
                what = "phone" if wants_phone else "Windows PC"
                return {**base, "success": False, "reply": f"Your {what} isn't connected right now, so I can't check that."}
            last = telemetry_reply(intent.args.get("metric", ""), known.to_dict(include_history=False), last_known=True)
            return {**base, "success": True, "reply": known.presence_sentence() + (f" {last}" if last else "")}
        reply = telemetry_reply(intent.args.get("metric", ""), dev.to_dict(include_history=False))
        if reply is None:
            return None
        return {**base, "success": True, "reply": reply}

    if intent.target == "phone":
        phone = device_manager.get_first_online_mobile()
        if phone is None:
            return {**base, "success": False, "reply": _offline_result("mobile")["reply"]}
        device_id, where = phone.device_id, "phone"
    elif not target_id:
        offline = _offline_result("pc")["reply"]
        return {**base, "success": False, "reply": f"{offline} I couldn't do that, but I can tell you when it's back."}
    else:
        device_id, where = target_id, "PC"

    t0 = time.perf_counter()
    res = await device_manager.send_to_device(device_id, intent.tool, intent.args)
    tool_ms = round((time.perf_counter() - t0) * 1000)
    ok = res.get("success", True) is not False
    if ok:
        reply = intent.reply
    elif intent.name == "close_app" and "could not find" in str(res.get("message", "")).lower():
        reply = intent.reply.replace("Closed ", "", 1).rstrip(".") + " isn't running."
    else:
        detail = res.get("reply") or res.get("error") or res.get("message") or "unknown error"
        reply = f"That didn't work on your {where}: {str(detail)[:120]}"
    return {
        **base,
        "success": ok,
        "reply": reply,
        "tool_used": intent.tool,
        "tool_args": intent.args,
        "tool_result": res,
        "tools": [{"name": intent.tool, "args": intent.args, "success": ok}],
        "tool_ms": tool_ms,
    }


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def spoken_text(reply: str, limit: int = 320) -> str:
    """What the voice says: the reply without markdown, cut at a sentence end once it gets
    long (the full text still shows in the transcript)."""
    text = re.sub(r"[*_`#|>]+", "", reply or "")
    text = re.sub(r"^\s*[-•]\s*", "", text, flags=re.M)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    out = ""
    for sentence in _SENTENCE_END.split(text):
        if out and len(out) + len(sentence) > limit:
            break
        out = f"{out} {sentence}".strip()
    return (out or text[:limit]) + " The details are on screen."


async def run_command(
    query: str,
    source: str = "api",
    session_id: Optional[str] = None,
    target_device_id: Optional[str] = None,
    return_audio: bool = False,
    speak_on_pc: bool = False,
    stt_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """Single entry point for every natural-language command (REST, WebSocket, voice)."""
    t0 = time.perf_counter()
    pc = device_manager.resolve_pc(target_device_id)
    target_id = pc.device_id if pc else None
    session_key = (session_id or source or "default")[:64]
    entry = activity_log.start(source, query, target_id)
    timings: Dict[str, Any] = {}
    if stt_ms is not None:
        timings["stt_ms"] = stt_ms

    result: Optional[Dict[str, Any]] = None
    intent = match_fast_intent(query) if settings.FAST_PATH_ENABLED else None
    if intent:
        result = await _run_fast_intent(intent, target_id, source)
        if result is not None and intent.ui and not result.get("ui") and result.get("success", True) is not False:
            result["ui"] = {**intent.ui, "device_id": target_id}
    if result is not None:
        orchestrator.remember_exchange(session_key, query, result["reply"])
        if result.get("tool_ms") is not None:
            timings["tool_ms"] = result["tool_ms"]
    else:
        result = await orchestrator.process_query(
            user_text=query,
            tool_executor=make_tool_executor(target_id, session_key, source, query),
            pc_online=pc is not None,
            session_id=session_key,
            live_context="\n".join(x for x in (device_manager.live_context(),
                                               server_monitor.live_line() if _is_host_space() else "", repo_context()) if x),
            now_text=timeutil.user_now().strftime("%A, %B %d %Y, %I:%M %p"),
            source=source or "",
            voice=return_audio,
        )
        for key in ("llm_ms", "tool_ms"):
            if result.get(key) is not None:
                timings[key] = result[key]

    reply = result.get("reply") or "Done."
    audio_b64 = None
    if return_audio or speak_on_pc:
        t_tts = time.perf_counter()
        try:
            audio_b64 = await voice_service.synthesize_speech_base64(spoken_text(reply))
        except Exception:
            audio_b64 = None
        timings["tts_ms"] = round((time.perf_counter() - t_tts) * 1000)
    if speak_on_pc and target_id:
        device_manager.spawn(device_manager.send_to_device(
            target_id, "speak", {"text": reply, "audio_base64": audio_b64}
        ))

    total_ms = round((time.perf_counter() - t0) * 1000) + (stt_ms or 0)
    timings["total_ms"] = total_ms
    tools: List[Dict[str, Any]] = result.get("tools") or []
    success = result.get("success", True) is not False
    failed = [t["name"] for t in tools if t.get("success") is False and t.get("name") != "save_feedback"]
    if failed and not re.search(r"confirm|which one|several files", reply, re.I):
        save_feedback(f"Tools failed: {', '.join(failed)}. Reply: {reply[:200]}", "auto", asked=query[:300])
    fast_path = bool(result.get("fast_path"))

    activity_log.finish(
        entry["id"],
        reply=reply,
        success=success,
        tools=[t.get("name") for t in tools],
        fast_path=fast_path,
        latency_ms=total_ms,
        timings=timings,
    )
    usage_counter.note([t.get("name") for t in tools] or ([result["tool_used"]] if result.get("tool_used") else []), success)

    return {
        "success": success,
        "query": query,
        "reply": reply,
        "audio_base64": audio_b64 if return_audio else None,
        "tool_used": result.get("tool_used"),
        "tool_args": result.get("tool_args"),
        "tool_result": _slim(result.get("tool_result")),
        "tools": tools,
        "ui": result.get("ui"),
        "fast_path": fast_path,
        "pc_online": pc is not None,
        "target_device_id": target_id,
        "duration_sec": round(total_ms / 1000, 3),
        "timings": timings,
        "activity_id": entry["id"],
        "session_id": session_key,
        "note": "Orchestrated by Central Server Brain",
    }


# Actions that legitimately take longer than the default device timeout (seconds).
SLOW_ACTIONS = {"run_shell": 620, "control": 120, "file_to_hub": 300, "send_file_to_phone": 300, "manage_file": 120,
                "find_files": 30, "list_processes": 30, "sys_updates": 240, "apply_updates": 1900, "storage": 120,
                "cleanup": 320, "journal": 45, "security": 75, "network": 30, "services_list": 30, "timers": 30}


async def run_device_action(device_id: str, action: str, payload: Dict[str, Any], source: str) -> Dict[str, Any]:
    """Direct device action (no LLM) with activity logging for user-visible actions."""
    entry = None
    if action not in QUIET_ACTIONS:
        entry = activity_log.start(source, f"{action.replace('_', ' ')}", device_id)
    t0 = time.perf_counter()
    res = await device_manager.send_to_device(device_id, action, payload, timeout=SLOW_ACTIONS.get(action))
    if entry:
        ok = res.get("success", True) is not False
        activity_log.finish(
            entry["id"],
            reply=res.get("message") or res.get("reply") or ("Done." if ok else res.get("error")),
            success=ok,
            tools=[action],
            fast_path=True,
            latency_ms=round((time.perf_counter() - t0) * 1000),
        )
    return res


# --- File relay between devices ---

def _resolve_target(target: Optional[str], sender_id: Optional[str] = None):
    """'pc' / 'phone' / a device id -> DeviceInfo (online preferred), or None."""
    t = (target or "").strip().lower()
    if t in ("", "pc", "computer", "laptop"):
        online = [d for d in device_manager.get_online_pcs() if d.device_id != sender_id]
        return online[0] if online else device_manager.last_known("pc", None)
    if t in ("phone", "mobile", "android"):
        return device_manager.get_first_online_mobile() or device_manager.last_known("mobile", None)
    return device_manager.get_device(target)


async def _deliver_file(meta: Dict[str, Any], device_id: str) -> Dict[str, Any]:
    payload = {k: meta[k] for k in ("id", "name", "size", "mime", "url")}
    payload["from"] = meta.get("from_name") or "Willy"
    res = await device_manager.send_to_device(device_id, "receive_file", payload, timeout=180)
    if res.get("success", True) is not False:
        _set_file_meta(meta["id"], delivered=True)
    return res


def _set_file_meta(file_id: str, **fields) -> None:
    meta = file_store.get(file_id)
    if meta:
        meta.pop("path", None)
        meta.update(fields)
        (file_store.root / file_id / "meta.json").write_text(json.dumps(meta), encoding="utf-8")


@app.post("/api/v1/files")
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    target: Optional[str] = None,
    sender: Optional[str] = Query(None, alias="from"),
    authorized: bool = Depends(verify_auth),
):
    """Relays a file to another device: uploads here, then the target downloads it."""
    sender_dev = device_manager.get_device(sender) if sender else None
    try:
        meta = await file_store.save(file, sender=sender or "")
    except FileTooLarge as e:
        raise HTTPException(status_code=413, detail=str(e))
    target_dev = _resolve_target(target, sender) if target else None
    from_name = sender_dev.name if sender_dev else "Willy"
    _set_file_meta(meta["id"], from_name=from_name,
                   target_device_id=target_dev.device_id if target_dev else None, delivered=False)
    meta = {k: v for k, v in (file_store.get(meta["id"]) or meta).items() if k != "path"}
    where = "your phone" if target_dev and target_dev.device_type == "mobile" else "your PC"
    entry = activity_log.start(_source_of(request, fallback=sender_dev.device_type if sender_dev else "api"),
                               f"send file {meta['name']}", target_dev.device_id if target_dev else None)
    delivered = False
    if target_dev is None:
        reply = f"Uploaded {meta['name']}, but I couldn't find a device to send it to."
    elif target_dev.device_id not in device_manager.active_sockets:
        reply = (f"{target_dev.name} is offline, so I'm holding {meta['name']} "
                 f"({describe_size(meta['size'])}) and will send it when it's back (within 24 hours).")
    else:
        res = await _deliver_file(meta, target_dev.device_id)
        delivered = res.get("success", True) is not False
        reply = (res.get("message") or f"Sent {meta['name']} to {where}.") if delivered else             f"Couldn't deliver {meta['name']} to {where}: {res.get('reply') or res.get('error') or 'unknown error'}"
    activity_log.finish(entry["id"], reply=reply, success=delivered or target_dev is not None,
                        tools=["send_file"], fast_path=True)
    return {"success": True, "file": meta, "delivered": delivered, "reply": reply}


class PushRegisterRequest(BaseModel):
    device_id: str
    token: str
    platform: Optional[str] = "android"


@app.post("/api/v1/push/register")
def push_register(req: PushRegisterRequest, authorized: bool = Depends(verify_auth)):
    if not device_manager.set_push_token(req.device_id, req.token.strip()):
        raise HTTPException(status_code=404, detail="Unknown device; open the Willy app once while online.")
    return {"success": True}


@app.post("/api/v1/push/test")
async def push_test(device_id: Optional[str] = None, authorized: bool = Depends(verify_auth)):
    """Sends a test notification to a phone (default: every phone with a push token)."""
    phones = [d for d in device_manager.devices.values()
              if d.device_type == "mobile" and d.push_token and (not device_id or d.device_id == device_id)]
    if not phones:
        return {"success": False, "error": "No phone has registered for push yet. Open the new Willy app once."}
    results = {}
    for phone in phones:
        res = await push_service.send(phone.push_token, "Willy", "Push notifications work.",
                                      {"kind": "test", "screen": "devices"})
        results[phone.name] = res.get("success") or res.get("error")
    return {"success": any(v is True for v in results.values()), "results": results}


@app.get("/api/v1/server/status")
async def server_status(refresh: bool = False, authorized: bool = Depends(verify_host_admin)):
    """The Willy server's health (refresh=true also re-checks every website now)."""
    if refresh or not server_monitor.snapshot:
        await asyncio.to_thread(server_monitor.collect, refresh)
    return {"success": True, "summary": server_monitor.summary(),
            "problems": [{"key": k, "message": v} for k, v in server_monitor.current_problems().items()],
            "ignored": sorted(server_monitor.ignored), "snapshot": server_monitor.snapshot}


class ServerIgnoreRequest(BaseModel):
    key: str
    ignore: bool = True


@app.post("/api/v1/server/ignore")
def server_ignore(req: ServerIgnoreRequest, authorized: bool = Depends(verify_host_admin)):
    """Mute (or unmute) a problem, e.g. a site you don't run any more: no alerts for it."""
    server_monitor.set_ignored(req.key.strip(), req.ignore)
    return {"success": True, "ignored": sorted(server_monitor.ignored)}


@app.get("/api/v1/server/history")
def server_history(hours: float = 24, points: int = 240, authorized: bool = Depends(verify_host_admin)):
    """CPU / RAM / disk / swap / load / network over the last `hours` (minute samples, up to 7 days)."""
    return {"success": True, **server_monitor.history_series(max(0.25, min(hours, 168)), max(10, min(points, 1000)))}


@app.get("/api/v1/server/apps/{name}/logs")
async def server_app_logs(name: str, lines: int = 80, authorized: bool = Depends(verify_host_admin)):
    text = await asyncio.to_thread(tail_app_log, name, lines)
    if not text:
        raise HTTPException(status_code=404, detail=f"No logs for '{name}'.")
    return {"success": True, "app": name, "logs": text}


@app.post("/api/v1/server/apps/{name}/restart")
async def server_app_restart(name: str, request: Request, authorized: bool = Depends(verify_host_admin)):
    """Restart button in the PC app / dashboard (the user confirmed in that UI)."""
    from server.server_monitor import _pm2_binary, _run
    import subprocess

    if name not in {a.get("name") for a in await asyncio.to_thread(pm2_apps)}:
        raise HTTPException(status_code=404, detail=f"There's no server app called '{name}'.")
    pm2 = _pm2_binary()
    activity_log.record(_source_of(request, fallback="pc"), f"restart server app {name}", reply="Restarting.",
                        success=True, fast_path=True, latency_ms=0, device_id="server")
    if name == "willy-server":
        asyncio.get_running_loop().call_later(2, lambda: subprocess.Popen([pm2, "restart", name]))
        return {"success": True, "message": "Restarting the Willy hub; it reconnects in a few seconds."}
    ok = await asyncio.to_thread(_run, [pm2, "restart", name], 30) is not None
    if not ok:
        raise HTTPException(status_code=500, detail=f"pm2 couldn't restart {name}.")
    return {"success": True, "message": f"Restarted {name}."}


@app.get("/api/v1/feedback")
def list_feedback(limit: int = 100, authorized: bool = Depends(verify_auth)):
    """Improvement notes: the user's ("note that you can't...") and failed tool calls."""
    try:
        lines = _feedback_path().read_text(encoding="utf-8").splitlines()[-max(1, min(limit, 1000)):]
    except OSError:
        lines = []
    items = []
    for line in reversed(lines):
        try:
            items.append(json.loads(line))
        except ValueError:
            continue
    return {"success": True, "feedback": items}


@app.get("/api/v1/files")
def list_files(authorized: bool = Depends(verify_auth)):
    return {"success": True, "files": [{k: v for k, v in m.items() if k != "path"} for m in file_store.list()]}


@app.get("/api/v1/files/{file_id}")
def download_file(file_id: str, authorized: bool = Depends(verify_auth)):
    meta = file_store.get(file_id)
    if not meta:
        raise HTTPException(status_code=404, detail="That file has expired or doesn't exist.")
    return FileResponse(meta["path"], media_type=meta.get("mime") or "application/octet-stream",
                        filename=meta["name"])


# --- Health, Server Info & Auth ---

@app.get("/health")
def health_check():
    # Public: no devices or account details here (those need sign-in).
    return {
        "status": "ok",
        "service": "Willy Central Server Hub",
        "version": APP_VERSION,
        "timestamp": time.time(),
        "uptime_sec": round(time.time() - SERVER_STARTED_AT),
    }


@app.get("/api/v1/auth/check")
def auth_check(principal: Principal = Depends(verify_auth)):
    """Lets web clients validate a token before storing it."""
    return {"success": True, "server": _server_info(), "account": _account_info(principal)}


# --- Accounts: Google sign-in, device keys, pairing, admin ---

def _account_info(principal: Principal) -> Dict[str, Any]:
    return {"email": principal.email, "name": principal.name, "role": principal.role,
            "owner": principal.is_home, "via": principal.via, "space": principal.space_id}


def _firebase_web_config() -> Optional[Dict[str, str]]:
    cfg = {"apiKey": os.getenv("FIREBASE_API_KEY", "").strip(),
           "authDomain": os.getenv("FIREBASE_AUTH_DOMAIN", "").strip(),
           "projectId": accounts.firebase.project_id,
           "appId": os.getenv("FIREBASE_APP_ID", "").strip()}
    if not (cfg["apiKey"] and cfg["projectId"]):
        return None
    cfg["authDomain"] = cfg["authDomain"] or f"{cfg['projectId']}.firebaseapp.com"
    return cfg


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return (fwd.split(",")[0].strip() if fwd else "") or (request.client.host if request.client else "?")


def _rate_limit(request: Request, bucket: str, limit: int, per_sec: float) -> None:
    if not accounts.allow_rate(f"{bucket}:{_client_ip(request)}", limit, per_sec):
        raise HTTPException(status_code=429, detail="Too many attempts. Wait a minute and try again.")


def _public_url(request: Request) -> str:
    """Where people open this hub in a browser (for pairing links)."""
    configured = os.getenv("WILLY_PUBLIC_URL", "").strip().rstrip("/")
    if configured:
        return configured
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}{request.headers.get('x-forwarded-prefix', '').rstrip('/')}"


def _auth_error(e: AuthError):
    return JSONResponse(status_code=e.status, content={"success": False, "error": str(e)})


class GoogleSignInRequest(BaseModel):
    id_token: str
    client: Optional[str] = None


class DeviceKeyRequest(BaseModel):
    device_id: str
    device_type: str = "mobile"
    name: Optional[str] = None


class PairStartRequest(BaseModel):
    device_id: str
    device_type: str = "pc"
    name: Optional[str] = None


class PairPollRequest(BaseModel):
    code: str
    poll_secret: str


class PairCodeRequest(BaseModel):
    code: str


class PairRedeemRequest(BaseModel):
    code: str
    device_id: str
    device_type: str = "mobile"
    name: Optional[str] = None


def _device_type(value: str) -> str:
    return value if value in ("pc", "mobile", "server") else "pc"


@app.get("/api/v1/auth/config")
def auth_config():
    """Public: what a sign-in page needs (Firebase web config is a public client id)."""
    google = _firebase_web_config()
    broker = None
    if google is None and not _broker.is_master() and _broker.master_url():
        broker = {"url": _broker.master_url()}   # no Firebase here: Google sign-in goes through the master
    return {"success": True, "google": google, "broker": broker, "email": True, "signup": accounts.signup_mode(),
            "version": APP_VERSION, "master": _broker.is_master()}


@app.post("/api/v1/auth/google")
async def auth_google(req: GoogleSignInRequest, request: Request):
    """Google sign-in (web, phone app): a Firebase ID token in, a Willy session token out."""
    _rate_limit(request, "signin", 20, 60)
    try:
        res = await asyncio.to_thread(accounts.sign_in_google, req.id_token, (req.client or _source_of(request))[:60])
    except AuthError as e:
        return _auth_error(e)
    _owner_cache[0] = 0.0  # the first sign-in may have just made the owner
    return {"success": True, **res}


class EmailRegisterRequest(BaseModel):
    email: str
    password: str
    name: str = ""


class EmailLoginRequest(BaseModel):
    email: str
    password: str


class PasswordRequest(BaseModel):
    password: str
    email: Optional[str] = None


@app.post("/api/v1/auth/register")
async def auth_register(req: EmailRegisterRequest, request: Request):
    """Email + password sign-up (for people without a Google account)."""
    _rate_limit(request, "register", 10, 3600)
    try:
        res = await asyncio.to_thread(accounts.register_email, req.email, req.password, req.name, _source_of(request)[:60])
    except AuthError as e:
        return _auth_error(e)
    return {"success": True, **res}


@app.post("/api/v1/auth/login")
async def auth_login(req: EmailLoginRequest, request: Request):
    _rate_limit(request, "login", 30, 600)
    if not accounts.allow_rate(f"login-email:{req.email.strip().lower()}", 8, 900):
        return JSONResponse(status_code=429, content={"success": False,
                            "error": "Too many wrong attempts for this email. Wait 15 minutes and try again."})
    try:
        res = await asyncio.to_thread(accounts.login_email, req.email, req.password, _source_of(request)[:60])
    except AuthError as e:
        return _auth_error(e)
    return {"success": True, **res}


@app.post("/api/v1/account/password")
async def account_password(req: PasswordRequest, request: Request, principal: Principal = Depends(verify_auth),
                           auth: Optional[HTTPAuthorizationCredentials] = Security(security)):
    """Sets (or changes) the password of the signed-in account. The owner, signed in with the hub token
    link, can also give the owner account a real email here to sign in by email later."""
    _rate_limit(request, "password", 10, 600)
    if principal.user_id is None:
        raise HTTPException(status_code=403, detail="Sign in first.")
    if principal.via == "device_key":
        raise HTTPException(status_code=403, detail="Devices can't change passwords.")
    try:
        await asyncio.to_thread(accounts.set_password, principal.user_id, req.password, req.email,
                                principal.via == "hub_token", auth.credentials if (auth and principal.via == "session") else "")
    except AuthError as e:
        return _auth_error(e)
    _owner_cache[0] = 0.0
    return {"success": True}


class BrokerLoginRequest(BaseModel):
    token: str
    state: str = ""


class BrokerTokenRequest(BaseModel):
    id_token: str
    hub: str
    state: str


def _own_origin(request: Request) -> str:
    """This hub's address as the browser sees it (scheme + host), which a broker token must be made for."""
    configured = _broker.clean_origin(os.getenv("WILLY_PUBLIC_URL", ""))
    if configured:
        return configured
    from urllib.parse import urlparse

    u = urlparse(_public_url(request))
    return f"{u.scheme}://{u.netloc.lower()}"


def _require_master() -> None:
    if not _broker.is_master():
        raise HTTPException(status_code=404, detail="This hub is not the Willy master.")


@app.post("/api/v1/auth/broker")
async def auth_broker(req: BrokerLoginRequest, request: Request):
    """Finishes a Google sign-in that went through the master: checks the master's signed token (made for
    this hub, five minutes old at most) and signs the person in."""
    _rate_limit(request, "broker", 20, 60)
    master = _broker.master_url()
    if not master:
        return JSONResponse(status_code=404, content={"success": False, "error": "Sign-in through the master is turned off on this hub."})
    audience = _own_origin(request)
    try:
        claims = await asyncio.to_thread(_broker.verify_token, req.token, master + "/api/v1/broker/jwks", _broker.master_issuer(master),
                                         audience, jwks_cache)
        if req.state and claims.get("state") != req.state:
            raise AuthError("That sign-in doesn't match the one this browser started. Try again.")
        res = await asyncio.to_thread(accounts.sign_in_claims, claims, _source_of(request)[:60])
    except AuthError as e:
        return _auth_error(e)
    _owner_cache[0] = 0.0
    return {"success": True, **res}


@app.get("/api/v1/broker/jwks")
def broker_jwks():
    _require_master()
    return broker_signer.jwks()


@app.post("/api/v1/broker/token")
async def broker_token(req: BrokerTokenRequest, request: Request):
    """Master only: a Google sign-in (Firebase ID token) in, a token for ONE named hub out."""
    _require_master()
    _rate_limit(request, "broker-token", 30, 300)
    origin = _broker.clean_origin(req.hub)
    if origin is None or not (8 <= len(req.state) <= 128):
        return JSONResponse(status_code=400, content={"success": False, "error": "That hub address or state isn't valid."})
    try:
        claims = await asyncio.to_thread(accounts.firebase.verify, req.id_token)
    except AuthError as e:
        return _auth_error(e)
    await asyncio.to_thread(master_registry.record_login, str(claims["email"]).lower(), claims.get("name"), claims["sub"], origin)
    return {"success": True, "token": broker_signer.issue(_broker.master_issuer(_broker.master_url() or _public_url(request)),
                                                           origin, claims, req.state)}


@app.get("/broker/login", response_class=HTMLResponse)
def broker_login_page():
    _require_master()
    from server.broker_html import get_broker_login_html
    return get_broker_login_html()


@app.get("/auth/callback", response_class=HTMLResponse)
def auth_callback_page():
    from server.broker_html import get_auth_callback_html
    return get_auth_callback_html()


@app.post("/api/v1/registry/ping")
async def registry_ping(request: Request):
    """Master only: a hub's daily check-in (build_ping in registry.py lists everything that is in it)."""
    _require_master()
    _rate_limit(request, "ping", 40, 3600)
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse(status_code=400, content={"success": False, "error": "Bad JSON."})
    if not isinstance(body, dict) or len(json.dumps(body)) > 60000:
        return JSONResponse(status_code=400, content={"success": False, "error": "Bad check-in."})
    ok = await asyncio.to_thread(master_registry.record_ping, body)
    return {"success": ok}


@app.get("/api/v1/master/summary")
def master_summary(principal: Principal = Depends(verify_host_admin)):
    _require_master()
    return {"success": True, "summary": master_registry.summary()}


@app.get("/api/v1/master/users")
def master_users_list(principal: Principal = Depends(verify_host_admin)):
    _require_master()
    return {"success": True, "users": master_registry.users()}


@app.get("/api/v1/master/hubs")
def master_hubs_list(principal: Principal = Depends(verify_host_admin)):
    _require_master()
    return {"success": True, "hubs": master_registry.hubs()}


@app.get("/master", response_class=HTMLResponse)
def master_page():
    _require_master()
    from server.broker_html import get_master_html
    return get_master_html()


@app.get("/api/v1/auth/me")
def auth_me(principal: Principal = Depends(verify_auth)):
    return {"success": True, "account": _account_info(principal)}


@app.post("/api/v1/auth/logout")
def auth_logout(request: Request, principal: Principal = Depends(verify_auth),
                auth: Optional[HTTPAuthorizationCredentials] = Security(security)):
    raw = auth.credentials if auth else ""
    if principal.via == "session" and raw:
        accounts.sign_out(raw)
    return {"success": True}


@app.post("/api/v1/auth/device-key")
def auth_device_key(req: DeviceKeyRequest, principal: Principal = Depends(verify_auth)):
    """A device that signed in with Google itself (the phone app) gets its own long-lived key."""
    if principal.via != "session":
        raise HTTPException(status_code=403, detail="Sign in with Google to register this device.")
    try:
        return {"success": True, **accounts.issue_key_for_session(principal, req.device_id[:120],
                                                                  _device_type(req.device_type), (req.name or req.device_id)[:200])}
    except AuthError as e:
        return _auth_error(e)


@app.post("/api/v1/pair/start")
def pair_start(req: PairStartRequest, request: Request):
    """Device-initiated pairing (PC app, server agent): shows a code the user approves while signed in."""
    _rate_limit(request, "pair", 10, 600)
    res = accounts.start_pairing(req.device_id.strip()[:120] or "device", _device_type(req.device_type),
                                 (req.name or req.device_id)[:200])
    url = f"{_public_url(request)}/pair?code={res['code']}"
    return {"success": True, **res, "verify_url": url, "interval": 2}


@app.post("/api/v1/pair/poll")
def pair_poll(req: PairPollRequest, request: Request):
    _rate_limit(request, "pair-poll", 400, 600)
    try:
        return {"success": True, **accounts.poll_pairing(req.code, req.poll_secret)}
    except AuthError as e:
        return _auth_error(e)


@app.get("/api/v1/pair/info")
def pair_info(code: str, principal: Principal = Depends(verify_auth)):
    info = accounts.pairing_info(code)
    if info is None:
        return JSONResponse(status_code=404, content={"success": False, "error": "That code has expired or was already used."})
    return {"success": True, **info}


@app.post("/api/v1/pair/approve")
def pair_approve(req: PairCodeRequest, principal: Principal = Depends(verify_auth)):
    if principal.user_id is None:
        raise HTTPException(status_code=403, detail="Sign in with Google to approve devices.")
    try:
        return {"success": True, **accounts.approve_pairing(req.code, principal.user_id)}
    except AuthError as e:
        return _auth_error(e)


@app.post("/api/v1/pair/link")
def pair_link(request: Request, principal: Principal = Depends(verify_auth)):
    """A one-time code (shown as a QR on the dashboard) that a phone or PC redeems for its key."""
    if principal.user_id is None:
        raise HTTPException(status_code=403, detail="Sign in with Google to add devices.")
    res = accounts.create_link_code(principal.user_id)
    return {"success": True, **res, "hub_url": _public_url(request)}


@app.post("/api/v1/pair/redeem")
def pair_redeem(req: PairRedeemRequest, request: Request):
    _rate_limit(request, "pair-redeem", 20, 600)
    try:
        return {"success": True, **accounts.redeem_link_code(req.code, req.device_id.strip()[:120] or "device",
                                                             _device_type(req.device_type), (req.name or req.device_id)[:200])}
    except AuthError as e:
        return _auth_error(e)


@app.get("/api/v1/account/devices")
def account_devices(principal: Principal = Depends(verify_auth)):
    keys = accounts.list_device_keys(principal.user_id) if principal.user_id else []
    return {"success": True, "devices": keys}


@app.delete("/api/v1/account/devices/{device_id}")
async def account_revoke_device(device_id: str, principal: Principal = Depends(verify_auth)):
    """Signs a device out of this account: its key stops working and its socket is closed."""
    if principal.user_id is None:
        raise HTTPException(status_code=403, detail="Sign in with Google to manage devices.")
    n = accounts.revoke_device(principal.user_id, device_id)
    ws = device_manager.active_sockets.get(device_id)
    if ws is not None and n:
        try:
            await ws.close(code=4001, reason="Device removed")
        except Exception:
            pass
    return {"success": bool(n), "revoked": n}


@app.get("/api/v1/admin/users")
def admin_users(principal: Principal = Depends(verify_host_admin)):
    """Accounts on this hub (no one's data, just who they are)."""
    return {"success": True, "users": accounts.list_users(), "signup": accounts.signup_mode()}


@app.post("/api/v1/admin/users/{user_id}/{action}")
def admin_user_action(user_id: str, action: str, principal: Principal = Depends(verify_host_admin)):
    if action not in ("block", "unblock"):
        raise HTTPException(status_code=404, detail="Unknown action.")
    ok = accounts.set_status(user_id, "blocked" if action == "block" else "active")
    return {"success": ok, "error": None if ok else "Can't change that account."}


@app.get("/pair", response_class=HTMLResponse)
def get_pair_page():
    from server.pair_html import get_pair_html
    return get_pair_html()


# --- AI provider: the hub owner picks the brain (any provider, any key) ---

class AIConfigRequest(BaseModel):
    provider: str
    model: str = ""
    api_key: str = ""
    base_url: str = ""
    skip_test: bool = False


def _reload_ai() -> None:
    """Every account's brain (and speech-to-text) picks up the new choice at once."""
    for space in list(space_registry.spaces.values()):
        brain = space.objects.get("orchestrator")
        if brain is not None:
            brain.reload()
    voice_service.reload()


@app.get("/api/v1/ai")
def ai_get(principal: Principal = Depends(verify_host_admin)):
    return {"success": True, **ai_config.public_view()}


@app.post("/api/v1/ai/test")
async def ai_test(req: AIConfigRequest, request: Request, principal: Principal = Depends(verify_host_admin)):
    _rate_limit(request, "ai-test", 20, 300)
    try:
        cfg = ai_config.candidates(req.provider, req.model, req.api_key, req.base_url)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"success": False, "error": str(e)})
    return {"success": True, **await ai_config.test(cfg)}


class VoiceKeyRequest(BaseModel):
    provider: str
    api_key: str


@app.post("/api/v1/ai/voice")
async def ai_voice_key(req: VoiceKeyRequest, request: Request, principal: Principal = Depends(verify_host_admin)):
    """A Groq or OpenAI key just for speech-to-text, when the main AI is a different provider."""
    _rate_limit(request, "ai-voice", 20, 300)
    if req.provider not in ("groq", "openai"):
        return JSONResponse(status_code=400, content={"success": False, "error": "Voice input works with Groq or OpenAI."})
    res = await ai_config.test(ai_config.candidates(req.provider, "", req.api_key, ""))
    if not res["ok"]:
        return JSONResponse(status_code=422, content={"success": False, "error": res["error"]})
    ai_config.save_key(req.provider, req.api_key)
    _reload_ai()
    return {"success": True, **ai_config.public_view()}


@app.post("/api/v1/ai")
async def ai_save(req: AIConfigRequest, request: Request, principal: Principal = Depends(verify_host_admin)):
    """Saves the choice after proving it works (unless skip_test)."""
    _rate_limit(request, "ai-save", 20, 300)
    try:
        cfg = ai_config.candidates(req.provider, req.model, req.api_key, req.base_url)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"success": False, "error": str(e)})
    if not req.skip_test:
        res = await ai_config.test(cfg)
        if not res["ok"]:
            return JSONResponse(status_code=422, content={"success": False, "error": res["error"]})
    ai_config.save(cfg.provider, cfg.model, req.api_key, req.base_url)
    _reload_ai()
    return {"success": True, **ai_config.public_view()}


@app.get("/api/v1/stats")
def server_stats(authorized: bool = Depends(verify_auth)):
    """Server health, LLM status and command latency statistics."""
    return {"success": True, "server": _server_info(), "stats": activity_log.stats()}


@app.get("/api/v1/activity")
def list_activity(limit: int = 50, authorized: bool = Depends(verify_auth)):
    """Most recent commands (newest first) with tools used, latency and outcome."""
    return {"success": True, "activity": activity_log.recent(limit), "stats": activity_log.stats()}


@app.post("/api/v1/session/reset")
async def reset_session(request: Request, authorized: bool = Depends(verify_auth)):
    """Clears the conversation memory for one client session (or the caller's source)."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    session_id = (body or {}).get("session_id") or _source_of(request, body)
    orchestrator.reset_conversation(session_id)
    return {"success": True, "session_id": session_id}


# --- Device Discovery Endpoints ---

@app.get("/api/v1/devices")
def list_devices(authorized: bool = Depends(verify_auth)):
    """Returns real-time list of all registered PCs and Mobile devices with live telemetry."""
    return {
        "success": True,
        "timestamp": time.time(),
        "devices": device_manager.get_all_devices(),
    }


@app.get("/api/v1/devices/{device_id}")
def get_device(device_id: str, authorized: bool = Depends(verify_auth)):
    dev = device_manager.get_device(device_id)
    if not dev:
        raise HTTPException(status_code=404, detail="Unknown device.")
    return {"success": True, "device": dev.to_dict()}


@app.get("/api/v1/devices/{device_id}/history")
def get_device_history(device_id: str, limit: int = 90, authorized: bool = Depends(verify_auth)):
    """Recent telemetry samples (CPU, RAM, battery, network) for charts."""
    dev = device_manager.get_device(device_id)
    if not dev:
        raise HTTPException(status_code=404, detail="Unknown device.")
    return {"success": True, "device_id": device_id, "history": dev.history_series(max(1, min(limit, 90)))}


@app.post("/api/v1/devices/{device_id}/action")
async def device_action(
    device_id: str,
    req: DeviceActionRequest,
    request: Request,
    authorized: bool = Depends(verify_auth),
):
    """Runs a structured action on a device directly (no LLM), e.g. {"action": "power_action", "payload": {"action": "lock"}}."""
    action = (req.action or "").strip()
    if action not in DIRECT_ACTIONS:
        raise HTTPException(status_code=400, detail=f"Action '{action}' is not allowed.")
    source = (req.source or _source_of(request)).lower()[:24]
    return await run_device_action(device_id, action, req.payload or {}, source)


@app.get("/api/v1/devices/{device_id}/processes")
async def list_device_processes(
    device_id: str,
    sort: str = "cpu",
    limit: int = 15,
    authorized: bool = Depends(verify_auth),
):
    """Top processes on a PC by CPU or memory."""
    return await device_manager.send_to_device(
        device_id, "list_processes", {"sort_by": sort, "limit": max(1, min(limit, 50))}
    )


@app.post("/api/v1/devices/{device_id}/processes/{pid}/kill")
async def kill_device_process(
    device_id: str,
    pid: int,
    request: Request,
    authorized: bool = Depends(verify_auth),
):
    return await run_device_action(device_id, "kill_process", {"pid": pid}, _source_of(request))


@app.post("/api/v1/devices/{device_id}/command")
async def send_device_command(
    device_id: str,
    req: CommandRequest,
    authorized: bool = Depends(verify_auth),
):
    """Directly commands a specific connected device (e.g. Windows PC)."""
    return await device_manager.send_to_device(
        target_device_id=device_id,
        action="command",
        payload={"query": req.query, "speak_on_pc": req.speak_on_pc},
    )


@app.post("/api/v1/devices/{device_id}/ring")
async def ring_device(
    device_id: str,
    request: Request,
    req: Optional[RingRequest] = None,
    authorized: bool = Depends(verify_auth),
):
    """Triggers audible alarm / beacon to locate phone or PC ('Find My Device')."""
    msg = req.message if req else "Find My Device"
    dur = req.duration_sec if req else 10
    return await run_device_action(
        device_id, "ring_device", {"message": msg, "duration_sec": dur}, _source_of(request)
    )


@app.post("/api/v1/devices/{device_id}/clipboard")
async def push_clipboard_to_device(
    device_id: str,
    req: ClipboardRequest,
    request: Request,
    authorized: bool = Depends(verify_auth),
):
    """Pushes clipboard text directly to target Phone or PC."""
    return await run_device_action(device_id, "set_clipboard", {"text": req.text}, _source_of(request))


@app.get("/api/v1/devices/{device_id}/clipboard")
async def read_clipboard_from_device(
    device_id: str,
    authorized: bool = Depends(verify_auth),
):
    """Reads current clipboard text from target Phone or PC."""
    return await device_manager.send_to_device(
        target_device_id=device_id,
        action="get_clipboard",
        payload={},
    )


@app.post("/api/v1/devices/{device_id}/quickdrop")
async def quickdrop_to_device(
    device_id: str,
    req: QuickDropRequest,
    request: Request,
    authorized: bool = Depends(verify_auth),
):
    """Pushes a URL or quick note to open instantly on target Phone or PC."""
    return await run_device_action(
        device_id, "quickdrop", {"url": req.url, "text": req.text, "title": req.title}, _source_of(request)
    )


@app.get("/api/v1/devices/{device_id}/screen")
async def get_device_screen_snapshot(
    device_id: str,
    quality: int = 60,
    max_width: int = 800,
    monitor: str = "active",
    authorized: bool = Depends(verify_auth),
):
    """Retrieves a low-latency JPEG snapshot of the PC monitor for Phone preview.
    Lower quality / width gives faster frames for live mode."""
    return await device_manager.send_to_device(
        target_device_id=device_id,
        action="get_screen_snapshot",
        payload={
            "quality": max(20, min(quality, 90)),
            "max_width": max(320, min(max_width, 1920)),
            "monitor": monitor,
        },
        timeout=10,
    )


@app.post("/api/v1/devices/{device_id}/media")
async def control_device_media(
    device_id: str,
    req: MediaRequest,
    request: Request,
    authorized: bool = Depends(verify_auth),
):
    """Controls volume, play/pause, next/prev track on target PC."""
    return await run_device_action(
        device_id, "media_control", {"action": req.action, "level": req.level}, _source_of(request)
    )


# --- Smart Command Handler ---

@app.post("/api/v1/command")
@app.get("/api/v1/command")
@app.post("/api/v1/commands/smart")
@app.get("/api/v1/commands/smart")
async def execute_smart_command(
    request: Request,
    query: Optional[str] = None,
    q: Optional[str] = None,
    text: Optional[str] = None,
    command: Optional[str] = None,
    target_device_id: Optional[str] = None,
    speak_on_pc: bool = False,
    format: Optional[str] = None,
    session_id: Optional[str] = None,
    authorized: bool = Depends(verify_auth),
):
    """
    Intelligently executes commands from Mobile or Web:
    - Simple commands take the fast path straight to the PC (no LLM).
    - Everything else goes through the LLM brain, which calls PC / phone tools as needed.
    """
    user_query = query or q or text or command or ""
    return_audio_req = False
    body: Dict[str, Any] = {}
    if request.method == "POST":
        try:
            parsed = await request.json()
            if isinstance(parsed, dict):
                body = parsed
                for k in ["query", "text", "command", "message", "prompt", "q"]:
                    if k in body and body[k]:
                        user_query = str(body[k]).strip()
                        break
                target_device_id = body.get("target_device_id", target_device_id)
                speak_on_pc = bool(body.get("speak_on_pc", speak_on_pc))
                return_audio_req = bool(body.get("return_audio", False))
                session_id = body.get("session_id", session_id)
        except Exception:
            pass

    if not user_query:
        fallback = "I am Willy Server. What would you like to run on your PC or ask me?"
        if format == "text":
            return PlainTextResponse(fallback)
        return {"success": True, "reply": fallback}

    res = await run_command(
        user_query,
        source=_source_of(request, body),
        session_id=session_id,
        target_device_id=target_device_id,
        return_audio=return_audio_req,
        speak_on_pc=speak_on_pc,
    )
    if format == "text":
        return PlainTextResponse(res["reply"])
    return res


# --- Voice Call & Interaction Endpoints ---

# How speech-to-text writes the assistant's name (it often mishears "Willy").
WAKE_NAME_RE = re.compile(r"\b(?:willy|willie|wiley|willi|wily|billy|lily|lilly|willy's)\b", re.I)


@app.post("/api/v1/call/interact")
async def call_interact(
    request: Request,
    file: UploadFile = File(...),
    target_device_id: Optional[str] = None,
    session_id: Optional[str] = None,
    wake: bool = False,
    token: Optional[str] = None,
    x_willy_token: Optional[str] = Header(None),
    auth: Optional[HTTPAuthorizationCredentials] = Security(security),
):
    """
    Real-time Web & Mobile Voice Call audio processor:
    Transcribes audio -> reasons via Server Brain -> executes PC tools -> synthesizes neural voice reply.
    """
    verify_auth(auth=auth, x_willy_token=x_willy_token, token=token)

    audio_bytes = await file.read()
    if not audio_bytes or len(audio_bytes) < 300:
        return JSONResponse(status_code=400, content={"success": False, "error": "Empty audio data."})

    t_stt = time.perf_counter()
    transcript = await voice_service.transcribe_audio_async(audio_bytes)
    stt_ms = round((time.perf_counter() - t_stt) * 1000)
    if not is_meaningful_transcript(transcript):
        return {"success": False, "error": "No speech detected in audio.", "reply": "",
                "transcript": (transcript or "").strip(), "timings": {"stt_ms": stt_ms}}

    if wake and not WAKE_NAME_RE.search(transcript):
        # A wake-word clip in which the full speech-to-text doesn't hear the name: the PC's
        # offline detector was fooled by room talk. Drop it without a reply (or AI quota).
        return {"success": True, "ignored": True, "reply": "", "transcript": transcript.strip(),
                "timings": {"stt_ms": stt_ms}}
    source = _source_of(request, fallback="voice")
    res = await run_command(
        transcript.strip(),
        source=source if source != "api" else "voice",
        session_id=session_id or f"voice_{source}",
        target_device_id=target_device_id,
        return_audio=True,
        stt_ms=stt_ms,
    )
    res["transcript"] = transcript.strip()
    return res


# --- Morning Briefing, ChatGPT Tasks & Mobile Telemetry Endpoints ---

@app.get("/api/v1/morning/briefing")
async def get_morning_briefing(
    include_audio: bool = True,
    authorized: bool = Depends(verify_auth),
):
    """Generates the full spoken daily morning briefing script and neural audio."""
    pc = device_manager.get_first_online_pc()
    return await morning_service.generate_briefing(
        include_audio=include_audio,
        pc_online=(pc is not None),
    )


@app.post("/api/v1/morning/sync")
async def sync_morning_tasks(authorized: bool = Depends(verify_auth)):
    """Synchronizes tasks from the configured ChatGPT thread."""
    tasks = await morning_service.sync_chatgpt_thread_tasks()
    return {"success": True, "tasks": tasks}


@app.get("/api/v1/morning/config")
def get_morning_config(authorized: bool = Depends(verify_auth)):
    """Retrieves morning call settings and ChatGPT thread configuration."""
    return {"success": True, "config": morning_service.config}


@app.post("/api/v1/morning/config")
async def update_morning_config(request: Request, authorized: bool = Depends(verify_auth)):
    """Updates morning call settings, schedule, and ChatGPT thread info."""
    body = await request.json()
    if "call_time" in body and body["call_time"] != morning_service.config.get("call_time"):
        morning_service.config["last_call_date"] = ""  # allow the new time to ring today
    cfg = morning_service.update_config(body)
    return {"success": True, "config": cfg}


@app.post("/api/v1/mobile/telemetry")
async def update_mobile_telemetry(request: Request, authorized: bool = Depends(verify_auth)):
    """Receives mobile notification counts, missed calls, and WhatsApp unread messages."""
    body = await request.json()
    telemetry = morning_service.update_telemetry(body)

    device_id = str(body.get("device_id") or "mobile_android_phone")
    existing = device_manager.get_device(device_id)
    dev = device_manager.upsert_http_device(
        device_id=device_id,
        device_type="mobile",
        name=body.get("device_name") or (existing.name if existing else "Android Phone"),
        hostname=body.get("model") or (existing.hostname if existing else None),
        platform=body.get("platform") or (existing.platform if existing else "Android"),
        telemetry={
            k: v for k, v in {
                "battery_pct": body.get("battery_pct"),
                "is_charging": body.get("is_charging"),
                "unread_notifications": body.get("total_notifications_count", 0),
                "unread_whatsapp": body.get("whatsapp_unread_count", 0),
                "missed_calls": body.get("missed_calls_count", 0),
                "ip_address": body.get("ip_address") or (request.client.host if request.client else None),
            }.items() if v is not None
        },
    )
    device_manager.publish({"type": "device_update", "device": dev.to_dict(), "timestamp": time.time()})
    return {"success": True, "telemetry": telemetry}


@app.get("/api/v1/reminders")
def get_reminders(authorized: bool = Depends(verify_auth)):
    """Lists active scheduled reminders."""
    return {"success": True, "reminders": morning_service.list_reminders()}


@app.post("/api/v1/reminders")
async def create_reminder(request: Request, authorized: bool = Depends(verify_auth)):
    """Creates a new scheduled reminder."""
    body = await request.json()
    rem = morning_service.add_reminder(
        text=body.get("text", "Reminder"),
        remind_time=body.get("time", "12:00 PM"),
        date=body.get("date"),
    )
    _notify_reminders_changed()
    return {"success": True, "reminder": rem}


@app.post("/api/v1/reminders/{reminder_id}/complete")
async def complete_reminder(reminder_id: str, request: Request, authorized: bool = Depends(verify_auth)):
    """Marks a reminder done (or not done with {"completed": false})."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    rem = morning_service.complete_reminder(reminder_id, bool((body or {}).get("completed", True)))
    if rem is None:
        raise HTTPException(status_code=404, detail="Unknown reminder.")
    _notify_reminders_changed()
    return {"success": True, "reminder": rem}


@app.delete("/api/v1/reminders/{reminder_id}")
def delete_reminder(reminder_id: str, authorized: bool = Depends(verify_auth)):
    if not morning_service.delete_reminder(reminder_id):
        raise HTTPException(status_code=404, detail="Unknown reminder.")
    _notify_reminders_changed()
    return {"success": True}


@app.get("/api/v1/alarms")
def get_alarms(authorized: bool = Depends(verify_auth)):
    """Lists scheduled alarms."""
    return {"success": True, "alarms": morning_service.list_alarms()}


@app.post("/api/v1/alarms")
async def create_alarm(request: Request, authorized: bool = Depends(verify_auth)):
    """Creates a new scheduled alarm."""
    body = await request.json()
    alm = morning_service.add_alarm(
        alarm_time=body.get("time", "07:00 AM"),
        label=body.get("label", "Morning Alarm"),
    )
    _notify_reminders_changed()
    return {"success": True, "alarm": alm}


@app.post("/api/v1/alarms/{alarm_id}/toggle")
async def toggle_alarm(alarm_id: str, request: Request, authorized: bool = Depends(verify_auth)):
    try:
        body = await request.json()
    except Exception:
        body = {}
    alarm = morning_service.set_alarm_enabled(alarm_id, bool((body or {}).get("enabled", True)))
    if alarm is None:
        raise HTTPException(status_code=404, detail="Unknown alarm.")
    _notify_reminders_changed()
    return {"success": True, "alarm": alarm}


@app.delete("/api/v1/alarms/{alarm_id}")
def delete_alarm(alarm_id: str, authorized: bool = Depends(verify_auth)):
    if not morning_service.delete_alarm(alarm_id):
        raise HTTPException(status_code=404, detail="Unknown alarm.")
    _notify_reminders_changed()
    return {"success": True}


# --- Watchdog (presence alerts) ---

@app.get("/api/v1/watches")
def list_watches(authorized: bool = Depends(verify_auth)):
    """Active 'tell me when my PC is online' alerts and watchdogs."""
    return {"success": True, "watches": watchdog.list()}


@app.post("/api/v1/watches")
async def create_watch(request: Request, authorized: bool = Depends(verify_auth)):
    """{"device": "pc"|"phone", "event": "online"|"offline"|"both", "repeat": bool, "then": "<request>"}"""
    try:
        body = await request.json()
    except Exception:
        body = {}
    return _watch(body if isinstance(body, dict) else {}, _source_of(request, body if isinstance(body, dict) else None))


@app.delete("/api/v1/watches/{watch_id}")
def delete_watch(watch_id: str, authorized: bool = Depends(verify_auth)):
    if not watchdog.remove(watch_id=watch_id):
        raise HTTPException(status_code=404, detail="Unknown watch.")
    return {"success": True}


# --- WebSocket Hub for Devices (PC Client & Mobile App) ---

async def _handle_device_ws_command(websocket: WebSocket, dev, data: Dict[str, Any]) -> None:
    """Runs a command a device sent over its socket (in the background, so the device's
    receive loop keeps flowing heartbeats and tool results while the brain works)."""
    target_id = data.get("target_device_id")
    payload = data.get("payload") or {}
    query = payload.get("query")
    request_id = data.get("request_id")
    if dev.device_type == "mobile":
        device_manager.note_used(dev.device_id)  # phone actions go to the phone being used
    if query:
        res = await run_command(
            str(query),
            source=dev.device_type,
            session_id=payload.get("session_id") or dev.device_id,
            target_device_id=target_id,
            return_audio=bool(payload.get("return_audio")),
            speak_on_pc=bool(payload.get("speak_on_pc")),
        )
    else:
        action = data.get("action", "command")
        if action not in DIRECT_ACTIONS and action != "command":
            res = {"success": False, "error": f"Action '{action}' is not allowed."}
        else:
            res = await run_device_action(target_id, action, payload, dev.device_type)
    message = {"type": "command_result", "request_id": request_id, "result": res}
    if not await device_manager.send_to_socket(websocket, message):
        # The socket dropped while the brain worked: try the device's newer socket, else
        # keep the answer until it reconnects.
        current = device_manager.active_sockets.get(dev.device_id)
        if current is None or current is websocket or not await device_manager.send_to_socket(current, message):
            _outbox.setdefault(dev.device_id, []).append((time.time(), message))
            del _outbox[dev.device_id][:-20]


async def _on_device_connected(websocket: WebSocket, dev) -> None:
    """Catch-up after a (re)connect: missed command results, files waiting for it, and (PC /
    server) a fresh list of its git projects for the brain."""
    if dev.device_type in ("pc", "server"):
        cached = _repo_index.get(dev.device_id)
        if not cached or time.time() - cached[0] > 1800:
            device_manager.spawn(refresh_repo_index(dev))
    now = time.time()
    for queued_at, message in _outbox.pop(dev.device_id, []):
        if now - queued_at <= OUTBOX_TTL_SEC:
            await device_manager.send_to_socket(websocket, message)
    for meta in file_store.list():
        if meta.get("target_device_id") == dev.device_id and not meta.get("delivered"):
            asyncio.create_task(_deliver_file(meta, dev.device_id))


@app.websocket("/ws/devices")
async def websocket_device_endpoint(
    websocket: WebSocket,
    token: Optional[str] = None,
    device_id: Optional[str] = None,
    device_type: Optional[str] = "pc",  # 'pc' or 'mobile'
    name: Optional[str] = "Device",
    platform: Optional[str] = None,
    hostname: Optional[str] = None,
    specs: Optional[str] = None,
    boot_time: Optional[float] = None,
):
    """
    Unified WebSocket Bus for Windows PCs and Flutter Mobile Apps.
    Provides live device discovery, heartbeats, and action routing.
    """
    principal = _principal_for(token)
    if principal is None:
        await websocket.close(code=4001, reason="Unauthorized Token")
        return
    _enter_space(principal)

    await websocket.accept()
    dev_type = device_type if device_type in ("pc", "mobile", "server") else "pc"
    # A device key belongs to one device: it always connects as that device.
    dev_id = principal.device_id or device_id or f"{dev_type}_{int(time.time() * 1000)}"
    plat = platform or {"pc": "Windows 11", "server": "Linux"}.get(dev_type, "Android")
    host = hostname or name

    parsed_specs = None
    if specs:
        try:
            parsed_specs = json.loads(specs)
        except Exception:
            parsed_specs = {"info": specs}

    dev = await device_manager.register_device(
        websocket=websocket,
        device_id=dev_id,
        device_type=dev_type,
        name=name or dev_id,
        hostname=host,
        platform=plat,
        specs=parsed_specs,
        boot_time=boot_time,
    )
    await device_manager.send_to_socket(websocket, _snapshot())
    dev.auth_legacy = _is_legacy_token(token)
    if dev.auth_legacy:
        # Signed in with a retired token: hand over the current one; the device saves it and
        # reconnects with it.
        await device_manager.send_to_socket(websocket, {"type": "token_update", "token": _expected_token()})
    asyncio.create_task(_on_device_connected(websocket, dev))

    close_code: Optional[int] = None
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(data, dict):
                continue
            msg_type = data.get("type")

            # 1. Device Heartbeat & Telemetry update (acked with the client's timestamp for RTT)
            if msg_type == "heartbeat":
                await device_manager.handle_heartbeat(dev, data.get("telemetry"))
                await device_manager.send_to_socket(websocket, {
                    "type": "heartbeat_ack",
                    "time": time.time(),
                    "client_ts": data.get("ts", data.get("timestamp")),
                })

            # 2. Command execution response from a device
            elif "req_id" in data and msg_type in (None, "result", "execution_result"):
                device_manager.handle_device_response(data["req_id"], data.get("result", {}))

            # 3. Static device details (CPU model, RAM size, OS build...)
            elif msg_type == "device_info":
                if isinstance(data.get("specs"), dict):
                    dev.specs.update(data["specs"])
                    device_manager.publish({"type": "device_update", "device": dev.to_dict(), "timestamp": time.time()})

            # 4. Device requesting the device list / activity / full snapshot
            elif msg_type == "get_devices":
                await device_manager.send_to_socket(websocket, {
                    "type": "devices_list",
                    "devices": device_manager.get_all_devices(),
                })
            elif msg_type == "get_snapshot":
                await device_manager.send_to_socket(websocket, _snapshot())

            # 5. Device sending a command (natural language or direct action) to another device
            elif msg_type == "send_to_device":
                device_manager.spawn(_handle_device_ws_command(websocket, dev, data))

            elif msg_type == "push_token":
                device_manager.set_push_token(dev.device_id, str(data.get("token") or "") or None)
            elif msg_type == "ping":
                await device_manager.send_to_socket(websocket, {
                    "type": "pong",
                    "ts": data.get("ts"),
                    "server_time": time.time(),
                })

            # 6. The device is about to disconnect (app closing, sleep, shutdown, sign-out)
            elif msg_type == "going_offline":
                await device_manager.announce_going_offline(dev_id, str(data.get("reason") or ""))

    except WebSocketDisconnect as e:
        close_code = e.code
    except Exception as e:
        print(f"[Hub] Device socket error ({dev_id}): {e}")
    finally:
        await device_manager.unregister_device(dev_id, websocket, close_code=close_code)


@app.websocket("/ws/events")
async def websocket_events_endpoint(websocket: WebSocket, token: Optional[str] = None):
    """Read-only live event stream for web dashboards (devices, telemetry, activity)."""
    await websocket.accept()
    principal = _principal_for(token)
    if principal is None:
        # Accept first so browsers receive close code 4001 instead of an anonymous 1006.
        await websocket.close(code=4001, reason="Unauthorized Token")
        return
    _enter_space(principal)
    device_manager.add_observer(websocket)
    await device_manager.send_to_socket(websocket, _snapshot())
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except ValueError:
                continue
            msg_type = data.get("type") if isinstance(data, dict) else None
            if msg_type == "ping":
                await device_manager.send_to_socket(websocket, {
                    "type": "pong",
                    "ts": data.get("ts"),
                    "server_time": time.time(),
                })
            elif msg_type == "get_snapshot":
                await device_manager.send_to_socket(websocket, _snapshot())
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        device_manager.remove_observer(websocket)


# --- Web UIs (Voice Call & Dashboard) ---
# The pages never embed the access token: they ask for it once and keep it in the browser.

@app.get("/call", response_class=HTMLResponse)
def get_call_ui():
    from server.web_call_html import get_web_voice_call_html
    return get_web_voice_call_html()


@app.get("/", response_class=HTMLResponse)
@app.get("/dashboard", response_class=HTMLResponse)
def get_dashboard_ui():
    from server.dashboard_html import get_dashboard_html
    return get_dashboard_html()


def main():
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    print("=" * 65)
    print("⚡ WILLY CENTRAL SERVER HUB RUNNING")
    print(f"   Port: {port}")
    print(f"   Device Discovery: http://localhost:{port}/api/v1/devices")
    print(f"   Web Voice Call: http://localhost:{port}/call")
    print("=" * 65)
    uvicorn.run("server.app:app", host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
