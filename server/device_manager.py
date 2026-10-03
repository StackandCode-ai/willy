"""
Willy Device Discovery & Connection Manager.
Tracks connected Windows PCs, Mobile clients and dashboard observers, streams live
telemetry to every observer in real time, and routes commands between devices.
"""

import json
import re
import time
import uuid
import asyncio
import itertools
import datetime as dt
from collections import deque
from pathlib import Path
from typing import Callable, Dict, Any, Optional, List

from fastapi import WebSocket

from server import timeutil

# Telemetry keys accepted from devices; anything else is dropped to keep broadcasts small.
TELEMETRY_KEYS = {
    # shared
    "battery_pct", "is_charging", "battery_secs_left", "ip_address", "wifi_ssid",
    "network_type", "last_ping_ms", "tz_offset_min", "uptime_hours",
    # pc
    "cpu_pct", "cpu_freq_mhz", "ram_pct", "ram_used_gb", "ram_total_gb",
    "disk_free_gb", "disk_total_gb", "disk_pct", "net_up_kbps", "net_down_kbps",
    "active_window", "active_process", "volume_level", "is_muted", "idle_sec",
    "process_count", "top_processes", "clipboard_preview", "brightness",
    # mobile
    "unread_notifications", "unread_whatsapp", "missed_calls", "storage_free_gb",
    "storage_total_gb", "screen_on", "model", "android_version", "torch_on",
}
MAX_TOP_PROCESSES = 8
HISTORY_LEN = 90            # samples kept per device (~3 min at 2 s heartbeats)
HISTORY_IN_SNAPSHOT = 40    # samples included in every device payload for sparklines
SOCKETLESS_STALE_SEC = 90   # devices that only report over HTTP
OFFLINE_RETENTION_SEC = 7 * 24 * 3600  # offline devices stay listed (with last-known state) a week
UPDATE_THROTTLE_SEC = 0.9
SEND_TIMEOUT_SEC = 5.0
REGISTRY_SAVE_EVERY_SEC = 60

# Why a device is offline: announced by the device just before it disconnects
# (app_closed / sleep / shutdown / restart / logoff) or inferred by the hub.
ANNOUNCED_REASONS = {"app_closed", "sleep", "shutdown", "restart", "logoff"}
_REASON_TEXT = {
    "pc": {
        "app_closed": "the Willy app was closed",
        "sleep": "the PC went to sleep",
        "shutdown": "the PC was shut down",
        "restart": "the PC was restarting",
        "logoff": "you signed out of Windows",
        "connection_lost": "its connection dropped (no network, or it was turned off or went to sleep)",
        "no_heartbeat": "it stopped responding (probably asleep or without network)",
        "hub_restarted": "the hub restarted",
        "restarted": "the PC was restarted",
    },
    "mobile": {
        "app_closed": "the Willy app was closed on the phone",
        "connection_lost": "the phone lost its connection",
        "no_heartbeat": "the phone stopped responding (app in the background or no network)",
        "hub_restarted": "the hub restarted",
        "restarted": "the phone was restarted",
    },
}

PresenceListener = Callable[[str, "DeviceInfo", Dict[str, Any]], None]


def reason_text(device_type: str, reason: Optional[str]) -> Optional[str]:
    if not reason:
        return None
    table = _REASON_TEXT.get(device_type) or _REASON_TEXT["pc"]
    return table.get(reason) or _REASON_TEXT["pc"].get(reason) or reason.replace("_", " ")


def _num(value) -> Optional[float]:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def describe_when(ts: Optional[float], now: Optional[float] = None) -> str:
    """'9:42 PM', 'yesterday at 9:42 PM' or 'Mon 28 Sep at 9:42 PM' in the user's time zone."""
    if not ts:
        return "an unknown time"
    tz = timeutil.user_tz()
    at = dt.datetime.fromtimestamp(ts, tz)
    today = dt.datetime.fromtimestamp(now or time.time(), tz).date()
    clock = at.strftime("%I:%M %p").lstrip("0")
    if at.date() == today:
        return clock
    if (today - at.date()).days == 1:
        return f"yesterday at {clock}"
    return f"{at.strftime('%a')} {at.day} {at.strftime('%b')} at {clock}"


def describe_duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return "a while"
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds} second{'s' if seconds != 1 else ''}"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        tail = f" {minutes} min" if minutes else ""
        return f"{hours} hour{'s' if hours != 1 else ''}{tail}"
    days = hours // 24
    return f"{days} day{'s' if days != 1 else ''}"


class DeviceInfo:
    def __init__(
        self,
        device_id: str,
        device_type: str,  # 'pc' or 'mobile'
        name: str,
        hostname: Optional[str] = None,
        platform: Optional[str] = None,
        specs: Optional[Dict[str, Any]] = None,
    ):
        self.device_id = device_id
        self.device_type = device_type
        self.name = name
        self.hostname = hostname or name
        self.platform = platform or "unknown"
        self.specs: Dict[str, Any] = dict(specs or {})
        self.status = "online"
        self.connection = "websocket"
        self.connected_at = time.time()
        self.last_seen = time.time()
        self.commands_handled = 0
        # Presence: when/why it went offline, and whether it announced going offline itself.
        self.offline_since: Optional[float] = None
        self.offline_reason: Optional[str] = None
        self.pending_reason: Optional[str] = None
        self.announced_offline_at: Optional[float] = None
        self.boot_time: Optional[float] = None
        # When the user last sent a command from this device: with two phones signed in,
        # phone actions go to the one actually in use.
        self.last_used_at: float = 0.0
        self.push_token: Optional[str] = None  # Firebase token: reaches the phone while it's disconnected
        self.history: deque = deque(maxlen=HISTORY_LEN)
        self.telemetry: Dict[str, Any] = {
            "battery_pct": None,
            "is_charging": None,
            "cpu_pct": None,
            "ram_pct": None,
            "active_window": None,
            "ip_address": None,
            "wifi_ssid": None,
            "disk_free_gb": None,
            "volume_level": None,
            "unread_notifications": 0,
            "unread_whatsapp": 0,
            "missed_calls": 0,
            "clipboard_preview": "",
            "last_ping_ms": None,
        }

    def update_telemetry(self, data: Dict[str, Any], record_history: bool = True):
        self.last_seen = time.time()
        for k, v in (data or {}).items():
            if k in TELEMETRY_KEYS:
                self.telemetry[k] = v
        top = self.telemetry.get("top_processes")
        if isinstance(top, list) and len(top) > MAX_TOP_PROCESSES:
            self.telemetry["top_processes"] = top[:MAX_TOP_PROCESSES]
        if record_history:
            t = self.telemetry
            self.history.append({
                "t": round(self.last_seen, 1),
                "cpu": _num(t.get("cpu_pct")),
                "ram": _num(t.get("ram_pct")),
                "battery": _num(t.get("battery_pct")),
                "net_down": _num(t.get("net_down_kbps")),
                "net_up": _num(t.get("net_up_kbps")),
            })

    def history_series(self, limit: int = HISTORY_IN_SNAPSHOT) -> Dict[str, List[Optional[float]]]:
        samples = list(self.history)[-limit:]
        return {
            key: [s.get(key) for s in samples]
            for key in ("t", "cpu", "ram", "battery", "net_down", "net_up")
        }

    def presence(self) -> Dict[str, Any]:
        online = self.status == "online"
        reason = None if online else self.offline_reason
        went = None if online else (self.offline_since or self.last_seen)
        return {
            "state": "online" if online else "offline",
            "since": self.connected_at if online else went,
            "reason": reason,
            "reason_text": reason_text(self.device_type, reason),
            "last_online_at": went,
        }

    def presence_sentence(self, now: Optional[float] = None) -> str:
        """Spoken summary, e.g. "HariG has been offline since 9:42 PM (12 minutes) - the PC went to sleep."."""
        now = now or time.time()
        if self.status == "online":
            return f"{self.name} is online."
        since = self.offline_since or self.last_seen
        text = f"{self.name} has been offline since {describe_when(since, now)} ({describe_duration(now - since)})"
        why = reason_text(self.device_type, self.offline_reason)
        return text + (f": {why}." if why else ".")

    def to_dict(self, include_history: bool = True) -> Dict[str, Any]:
        data = {
            "device_id": self.device_id,
            "device_type": self.device_type,
            "legacy_token": bool(getattr(self, "auth_legacy", False)),  # still on a retired token
            "name": self.name,
            "hostname": self.hostname,
            "platform": self.platform,
            "status": self.status,
            "connection": self.connection,
            "connected_at": self.connected_at,
            "last_seen": self.last_seen,
            "online": self.status == "online",
            "commands_handled": self.commands_handled,
            "specs": self.specs,
            "telemetry": self.telemetry,
            "presence": self.presence(),
        }
        if include_history:
            data["history"] = self.history_series()
        return data

    # Persistence (the hub remembers devices across restarts, with their last-known state).
    def to_record(self) -> Dict[str, Any]:
        return {
            "device_id": self.device_id, "device_type": self.device_type, "name": self.name,
            "hostname": self.hostname, "platform": self.platform, "specs": self.specs,
            "telemetry": self.telemetry, "last_seen": self.last_seen, "connected_at": self.connected_at,
            "commands_handled": self.commands_handled, "status": self.status,
            "offline_since": self.offline_since, "offline_reason": self.offline_reason,
            "boot_time": self.boot_time, "last_used_at": self.last_used_at, "push_token": self.push_token,
        }

    @classmethod
    def from_record(cls, rec: Dict[str, Any]) -> "DeviceInfo":
        dev = cls(rec["device_id"], rec.get("device_type") or "pc", rec.get("name") or rec["device_id"],
                  rec.get("hostname"), rec.get("platform"), rec.get("specs") or {})
        dev.telemetry.update(rec.get("telemetry") or {})
        dev.last_seen = float(rec.get("last_seen") or 0)
        dev.connected_at = float(rec.get("connected_at") or dev.last_seen)
        dev.commands_handled = int(rec.get("commands_handled") or 0)
        dev.boot_time = rec.get("boot_time")
        dev.last_used_at = float(rec.get("last_used_at") or 0)
        dev.push_token = rec.get("push_token") or None
        dev.status = "offline"
        dev.connection = "none"
        if rec.get("status") == "online":  # was connected when the hub stopped
            dev.offline_since, dev.offline_reason = dev.last_seen, "hub_restarted"
        else:
            dev.offline_since = rec.get("offline_since") or dev.last_seen
            dev.offline_reason = rec.get("offline_reason")
        return dev


class DeviceManager:
    """
    Central hub managing all active devices (PCs and Mobile apps) plus read-only
    observers (web dashboards). Routes commands and fans out live events.
    """

    def __init__(self, command_timeout: float = 20.0, stale_after_sec: float = 20.0,
                 registry_path: Optional[Path] = None):
        self.devices: Dict[str, DeviceInfo] = {}
        self.active_sockets: Dict[str, WebSocket] = {}
        self.observers: Dict[int, WebSocket] = {}
        self.pending_requests: Dict[str, asyncio.Future] = {}
        self.command_timeout = command_timeout
        self.stale_after_sec = stale_after_sec
        self._pending_owner: Dict[str, str] = {}
        self._req_counter = itertools.count(1)
        self._send_locks: Dict[int, asyncio.Lock] = {}
        self._last_update_broadcast: Dict[str, float] = {}
        self._went_offline: set = set()
        self._background: set = set()
        # Presence transitions ("online"/"offline", device, info) for listeners such as the
        # watchdog. Transitions noticed off the event loop are queued and flushed by sweep_stale().
        self.presence_listeners: List[PresenceListener] = []
        self._presence_queue: deque = deque()
        self.registry_path = Path(registry_path) if registry_path else None
        self._registry_saved_at = 0.0
        self._load_registry()

    # ----------------------------------------------------------------- registry

    def _load_registry(self) -> None:
        if not self.registry_path or not self.registry_path.exists():
            return
        try:
            records = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            print(f"[DeviceManager] Could not read the device registry: {e}")
            return
        cutoff = time.time() - OFFLINE_RETENTION_SEC
        for rec in records if isinstance(records, list) else []:
            try:
                dev = DeviceInfo.from_record(rec)
            except (KeyError, TypeError, ValueError):
                continue
            if dev.last_seen >= cutoff:
                self.devices[dev.device_id] = dev
        # Reminders and "since 9:42 PM" use the user's time zone, which devices report in
        # their heartbeats; reuse the last one until a device reconnects after a hub restart.
        latest = max((d for d in self.devices.values() if d.telemetry.get("tz_offset_min") is not None),
                     key=lambda d: d.last_seen, default=None)
        if latest is not None:
            timeutil.set_reported_offset(latest.telemetry.get("tz_offset_min"))

    def save_registry(self) -> None:
        if not self.registry_path:
            return
        try:
            self.registry_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.registry_path.with_suffix(".tmp")
            tmp.write_text(json.dumps([d.to_record() for d in self.devices.values()], default=str),
                           encoding="utf-8")
            tmp.replace(self.registry_path)
            self._registry_saved_at = time.time()
        except OSError as e:
            print(f"[DeviceManager] Could not save the device registry: {e}")

    # ----------------------------------------------------------------- presence

    def _presence_changed(self, kind: str, dev: DeviceInfo, info: Dict[str, Any]) -> None:
        """Queues an online/offline transition; delivered on the event loop (now, or by the
        next sweep when a threadpool request noticed it)."""
        self._presence_queue.append((kind, dev, info))
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        self._flush_presence()

    def _flush_presence(self) -> None:
        delivered = False
        while self._presence_queue:
            kind, dev, info = self._presence_queue.popleft()
            delivered = True
            for listener in list(self.presence_listeners):
                try:
                    listener(kind, dev, info)
                except Exception as e:
                    print(f"[DeviceManager] Presence listener error: {e}")
        if delivered:
            self.save_registry()

    def _mark_offline(self, dev: DeviceInfo, reason: str, since: Optional[float] = None,
                      queue_announce: bool = True) -> None:
        """queue_announce: let sweep_stale() broadcast device_offline (callers that
        broadcast it themselves pass False)."""
        dev.status = "offline"
        dev.offline_since = since or time.time()
        dev.offline_reason = reason
        if queue_announce:
            self._went_offline.add(dev.device_id)
        self._presence_changed("offline", dev, {"reason": reason})

    def _mark_online(self, dev: DeviceInfo, boot_time: Optional[float] = None) -> None:
        """Clears offline state; tells listeners how long it was gone and why."""
        now = time.time()
        was_known_offline = dev.offline_since is not None
        info: Dict[str, Any] = {"first_seen": not was_known_offline and dev.commands_handled == 0}
        if was_known_offline:
            reason = dev.offline_reason
            if boot_time and dev.offline_since and boot_time > dev.offline_since:
                reason = "restarted"
            info.update({"offline_for": now - dev.offline_since, "previous_reason": reason,
                         "offline_since": dev.offline_since})
        dev.status = "online"
        dev.offline_since = dev.offline_reason = None
        dev.pending_reason = dev.announced_offline_at = None
        self._went_offline.discard(dev.device_id)
        self._presence_changed("online", dev, info)

    # ------------------------------------------------------------------ sockets

    async def send_to_socket(self, ws: WebSocket, data: Dict[str, Any]) -> bool:
        """Serialised, time-bounded send so one slow client never stalls the hub."""
        lock = self._send_locks.setdefault(id(ws), asyncio.Lock())
        try:
            async with lock:
                await asyncio.wait_for(ws.send_json(data), timeout=SEND_TIMEOUT_SEC)
            return True
        except Exception:
            return False

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    def spawn(self, coro) -> bool:
        """Runs a coroutine in the background (keeps a reference so it isn't GC'd).
        Returns False, closing the coroutine, when no event loop is running."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            coro.close()
            return False
        self._spawn(coro)
        return True

    def publish(self, event: Dict[str, Any]) -> None:
        """Fire-and-forget broadcast, callable from sync code running on the event loop."""
        self.spawn(self.broadcast_event(event))

    @staticmethod
    async def _close_quietly(ws: WebSocket, code: int, reason: str) -> None:
        try:
            await ws.close(code=code, reason=reason)
        except Exception:
            pass

    def add_observer(self, ws: WebSocket) -> None:
        self.observers[id(ws)] = ws

    def remove_observer(self, ws: WebSocket) -> None:
        self.observers.pop(id(ws), None)
        self._send_locks.pop(id(ws), None)

    # ------------------------------------------------------------ registration

    async def register_device(
        self,
        websocket: WebSocket,
        device_id: str,
        device_type: str,
        name: str,
        hostname: Optional[str] = None,
        platform: Optional[str] = None,
        specs: Optional[Dict[str, Any]] = None,
        boot_time: Optional[float] = None,
    ) -> DeviceInfo:
        """Registers (or re-attaches) a PC or Mobile client connection."""
        now = time.time()
        dev = self.devices.get(device_id)
        was_online = dev is not None and dev.status == "online" and device_id in self.active_sockets
        if dev is None:
            dev = DeviceInfo(
                device_id=device_id,
                device_type=device_type,
                name=name,
                hostname=hostname,
                platform=platform,
                specs=specs,
            )
            self.devices[device_id] = dev
        else:
            # Keep telemetry history across reconnects.
            dev.device_type = device_type
            dev.name = name
            dev.hostname = hostname or name
            dev.platform = platform or dev.platform
            if specs:
                dev.specs.update(specs)
        if boot_time:
            dev.boot_time = boot_time
        dev.connection = "websocket"
        dev.connected_at = now
        dev.last_seen = now

        old_ws = self.active_sockets.get(device_id)
        self.active_sockets[device_id] = websocket
        if old_ws is not None and old_ws is not websocket:
            # A newer connection replaces a stale one: fail its in-flight requests and close it.
            self._fail_pending_for(device_id, "DEVICE_RECONNECTED")
            self._send_locks.pop(id(old_ws), None)
            self._spawn(self._close_quietly(old_ws, 4000, "Replaced by newer connection"))

        if was_online:
            dev.status = "online"
        else:
            self._mark_online(dev, boot_time)
        print(f"[DeviceManager] Registered {device_type.upper()}: '{name}' (ID: {device_id})")
        # The new device itself gets a full snapshot from the hub instead.
        await self.broadcast_event({
            "type": "device_discovered",
            "device": dev.to_dict(),
            "timestamp": now,
        }, exclude_id=device_id)
        return dev

    async def unregister_device(self, device_id: str, websocket: Optional[WebSocket] = None,
                                close_code: Optional[int] = None):
        """Marks a device as disconnected and notifies remaining clients.

        When `websocket` is given, only that exact connection is unregistered, so a stale
        socket closing late cannot knock a freshly reconnected device offline. The offline
        reason is what the device announced, else inferred from how the socket closed.
        """
        current = self.active_sockets.get(device_id)
        if websocket is not None and current is not None and current is not websocket:
            return
        if current is not None:
            self.active_sockets.pop(device_id, None)
            self._send_locks.pop(id(current), None)
        self._fail_pending_for(device_id, "DEVICE_OFFLINE")

        dev = self.devices.get(device_id)
        if dev:
            clean = close_code in (1000, 1001, 1005)
            reason = dev.pending_reason or ("app_closed" if clean else "connection_lost")
            dev.connection = "none"
            if dev.status == "online":
                self._mark_offline(dev, reason, since=dev.announced_offline_at, queue_announce=False)
            elif dev.offline_reason in (None, "no_heartbeat") and dev.pending_reason:
                dev.offline_reason = dev.pending_reason
            dev.pending_reason = None
            print(f"[DeviceManager] Device Disconnected: '{dev.name}' ({device_id}): {dev.offline_reason}")
            await self.broadcast_event({
                "type": "device_offline",
                "device_id": device_id,
                "device": dev.to_dict(),
                "timestamp": time.time(),
            })

    async def announce_going_offline(self, device_id: str, reason: str) -> None:
        """A device said it is about to disconnect (app closing, sleep, shutdown, sign-out):
        it is shown offline at once with that reason, even while its socket lingers."""
        dev = self.devices.get(device_id)
        if dev is None or reason not in ANNOUNCED_REASONS:
            return
        dev.pending_reason = reason
        dev.announced_offline_at = time.time()
        if dev.status != "online":
            dev.offline_reason = reason
            return
        self._mark_offline(dev, reason, since=dev.announced_offline_at, queue_announce=False)
        print(f"[DeviceManager] {dev.name} is going offline: {reason}")
        await self.broadcast_event({
            "type": "device_offline",
            "device_id": device_id,
            "device": dev.to_dict(),
            "timestamp": time.time(),
        })

    def upsert_http_device(
        self,
        device_id: str,
        device_type: str,
        name: str,
        hostname: Optional[str] = None,
        platform: Optional[str] = None,
        telemetry: Optional[Dict[str, Any]] = None,
    ) -> DeviceInfo:
        """Tracks a device that reports over plain HTTP rather than a socket."""
        dev = self.devices.get(device_id)
        if dev is None:
            dev = DeviceInfo(device_id, device_type, name, hostname, platform)
            dev.connection = "http"
            dev.status = "offline"  # announced as coming online just below
            self.devices[device_id] = dev
        if device_id not in self.active_sockets:
            dev.connection = "http"
        dev.update_telemetry(telemetry or {})
        if dev.status != "online":
            self._mark_online(dev)
        return dev

    # --------------------------------------------------------------- telemetry

    async def handle_heartbeat(self, dev: DeviceInfo, telemetry: Optional[Dict[str, Any]]) -> None:
        now = time.time()
        if dev.announced_offline_at is not None and now - dev.announced_offline_at < 5:
            # A beat already in flight when the device announced it was going offline.
            dev.update_telemetry(telemetry or {}, record_history=False)
            return
        was_offline = dev.status != "online"
        dev.update_telemetry(telemetry or {})
        if telemetry and "tz_offset_min" in telemetry:
            timeutil.set_reported_offset(telemetry.get("tz_offset_min"))

        if was_offline:
            self._mark_online(dev)  # it stopped beating for a while, or woke up again
            self._spawn(self.broadcast_event({
                "type": "device_discovered",
                "device": dev.to_dict(),
                "timestamp": now,
            }))
        elif now - self._last_update_broadcast.get(dev.device_id, 0) >= UPDATE_THROTTLE_SEC:
            self._last_update_broadcast[dev.device_id] = now
            self._spawn(self.broadcast_event({
                "type": "device_update",
                "device": dev.to_dict(),
                "timestamp": now,
            }))

    def _refresh_statuses(self) -> None:
        """Recomputes online/offline. Online->offline transitions are queued for
        sweep_stale() to announce, whichever caller happened to notice them first."""
        now = time.time()
        stale_ids = [
            d_id for d_id, dev in self.devices.items()
            if d_id not in self.active_sockets and now - dev.last_seen > OFFLINE_RETENTION_SEC
        ]
        for d_id in stale_ids:
            self.devices.pop(d_id, None)
            self._last_update_broadcast.pop(d_id, None)

        for dev in list(self.devices.values()):
            if dev.device_id in self.active_sockets:
                online = now - dev.last_seen <= self.stale_after_sec and dev.announced_offline_at is None
            elif dev.connection == "http":
                online = now - dev.last_seen <= SOCKETLESS_STALE_SEC
            else:
                online = False  # its socket closed: offline right away, however recent the last beat
            if dev.status == "online" and not online:
                silent = dev.device_id in self.active_sockets
                reason = dev.pending_reason or ("no_heartbeat" if silent else "connection_lost")
                self._mark_offline(dev, reason, since=dev.announced_offline_at or dev.last_seen)

    async def sweep_stale(self) -> None:
        """Periodic task body: announces devices whose heartbeats stopped, delivers queued
        presence changes, and saves the device registry now and then."""
        self._refresh_statuses()
        self._flush_presence()
        if self.registry_path and time.time() - self._registry_saved_at >= REGISTRY_SAVE_EVERY_SEC:
            self.save_registry()
        went, self._went_offline = self._went_offline, set()
        for device_id in went:
            dev = self.devices.get(device_id)
            if dev is None or dev.status != "offline":
                continue  # removed, or heartbeats resumed in the meantime
            await self.broadcast_event({
                "type": "device_offline",
                "device_id": dev.device_id,
                "device": dev.to_dict(),
                "timestamp": time.time(),
            })

    # ----------------------------------------------------------------- queries

    def get_all_devices(self) -> List[Dict[str, Any]]:
        """Returns metadata for all registered devices (online first, then by name)."""
        self._refresh_statuses()
        ordered = sorted(
            self.devices.values(),
            key=lambda d: (d.status != "online", d.device_type != "pc", d.name.lower()),
        )
        return [dev.to_dict() for dev in ordered]

    def get_device(self, device_id: str) -> Optional[DeviceInfo]:
        return self.devices.get(device_id)

    def get_online_pcs(self) -> List[DeviceInfo]:
        now = time.time()
        pcs = [
            d for d in self.devices.values()
            if d.device_type == "pc"
            and d.device_id in self.active_sockets
            and now - d.last_seen <= self.stale_after_sec
        ]
        return sorted(pcs, key=lambda d: d.last_seen, reverse=True)

    def get_first_online_pc(self) -> Optional[DeviceInfo]:
        """Returns the most recently active online Windows PC, if any."""
        pcs = self.get_online_pcs()
        return pcs[0] if pcs else None

    def get_server(self) -> Optional[DeviceInfo]:
        """The connected server agent (the machine the hub runs on), if any."""
        servers = [d for d in self.devices.values() if d.device_type == "server" and d.device_id in self.active_sockets]
        return max(servers, key=lambda d: d.last_seen) if servers else None

    def online_phones(self) -> List[DeviceInfo]:
        now = time.time()
        return [
            d for d in self.devices.values()
            if d.device_type == "mobile"
            and d.device_id in self.active_sockets
            and now - d.last_seen <= self.stale_after_sec
        ]

    def get_first_online_mobile(self, name: Optional[str] = None) -> Optional[DeviceInfo]:
        """The phone to act on: the one named ("A3", "OPPO A16", a device id) if given, else the
        phone the user last sent a command from, else the most recently active one."""
        phones = self.online_phones()
        if name:
            match = self.find_phone(name, phones)
            if match is not None:
                return match
        return max(phones, key=lambda d: (d.last_used_at, d.last_seen)) if phones else None

    def find_phone(self, name: str, phones: Optional[List[DeviceInfo]] = None) -> Optional[DeviceInfo]:
        """Matches a spoken phone name against device names/models ("a3" -> "OPPO A3 Pro 5G")."""
        wanted = re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()
        wanted = re.sub(r"\b(?:my|the|phone|mobile|oppo)\b", " ", wanted).split()
        if not wanted:
            return None
        pool = phones if phones is not None else [d for d in self.devices.values() if d.device_type == "mobile"]
        for dev in pool:
            if dev.device_id.lower() == (name or "").lower():
                return dev
            words = re.sub(r"[^a-z0-9]+", " ", f"{dev.name} {dev.hostname}".lower()).split()
            if all(w in words for w in wanted):
                return dev
        return None

    def phones_for_push(self) -> List[DeviceInfo]:
        """Phones that are NOT connected right now but can be reached by push."""
        return [d for d in self.devices.values()
                if d.device_type == "mobile" and d.push_token and d.device_id not in self.active_sockets]

    def set_push_token(self, device_id: str, token: Optional[str]) -> bool:
        dev = self.devices.get(device_id)
        if dev is None:
            return False
        if dev.push_token != (token or None):
            dev.push_token = token or None
            self.save_registry()
        return True

    def note_used(self, device_id: Optional[str]) -> None:
        """Marks a device as the one the user is using right now (a command came from it)."""
        dev = self.devices.get(device_id or "")
        if dev is not None:
            dev.last_used_at = time.time()

    def online_counts(self) -> Dict[str, int]:
        self._refresh_statuses()
        counts = {"pc": 0, "mobile": 0}
        for d in self.devices.values():
            if d.status == "online":
                counts[d.device_type] = counts.get(d.device_type, 0) + 1
        return counts

    def resolve_pc(self, target_device_id: Optional[str]) -> Optional[DeviceInfo]:
        """The requested PC when it is online, else the primary online PC."""
        if target_device_id:
            dev = self.devices.get(target_device_id)
            if dev and dev.device_type == "pc" and target_device_id in self.active_sockets:
                return dev
        return self.get_first_online_pc()

    def last_known(self, device_type: str, device_id: Optional[str] = None) -> Optional[DeviceInfo]:
        """The given device, else the online device of that type, else the one seen most
        recently - so offline devices can still be described ("asleep since 9:42 PM")."""
        self._refresh_statuses()
        if device_id and device_id in self.devices:
            return self.devices[device_id]
        pool = [d for d in self.devices.values() if d.device_type == device_type]
        online = [d for d in pool if d.status == "online"]
        pool = online or pool
        return max(pool, key=lambda d: d.last_seen) if pool else None

    def live_context(self) -> str:
        """Compact live-telemetry summary injected into the LLM prompt (offline devices are
        listed with when/why they went offline and their last-known state)."""
        self._refresh_statuses()
        lines = []
        offline = sorted((d for d in self.devices.values() if d.status != "online"),
                         key=lambda d: d.last_seen, reverse=True)[:3]
        for dev in offline:
            kind = "Windows PC" if dev.device_type == "pc" else "Phone"
            last = dev.telemetry.get("battery_pct")
            known = f" Last known battery {last}%." if last is not None else ""
            lines.append(f"- {kind} '{dev.name}' (id {dev.device_id}): OFFLINE. {dev.presence_sentence()}{known}")
        for dev in self.devices.values():
            if dev.status != "online" or dev.device_type == "server":
                continue  # the server's line comes from the server monitor (with its problems)
            t = dev.telemetry
            parts = []
            if t.get("battery_pct") is not None:
                charging = ", charging" if t.get("is_charging") else ""
                parts.append(f"battery {t['battery_pct']}%{charging}")
            if dev.device_type == "pc":
                for key, label, unit in (
                    ("cpu_pct", "CPU", "%"),
                    ("ram_pct", "RAM", "%"),
                    ("disk_free_gb", "free disk", " GB"),
                    ("volume_level", "volume", "%"),
                    ("uptime_hours", "uptime", " h"),
                ):
                    if t.get(key) is not None:
                        parts.append(f"{label} {t[key]}{unit}")
                if t.get("is_muted"):
                    parts.append("audio muted")
                if t.get("active_window"):
                    parts.append(f"active window '{str(t['active_window'])[:60]}'")
            else:
                if t.get("unread_notifications"):
                    parts.append(f"{t['unread_notifications']} notifications")
                if t.get("missed_calls"):
                    parts.append(f"{t['missed_calls']} missed calls")
            kind = "Windows PC" if dev.device_type == "pc" else "Phone"
            lines.append(f"- {kind} '{dev.name}' (id {dev.device_id}): ONLINE" + (". " + ", ".join(parts) if parts else "."))
        return "\n".join(lines) if lines else "- No PC or phone has connected yet."

    # ---------------------------------------------------------------- commands

    def _fail_pending_for(self, device_id: str, error: str) -> None:
        for req_id, owner in list(self._pending_owner.items()):
            if owner != device_id:
                continue
            fut = self.pending_requests.get(req_id)
            if fut and not fut.done():
                fut.set_result({
                    "success": False,
                    "error": error,
                    "reply": "The device disconnected before it could answer.",
                })

    async def send_to_device(
        self,
        target_device_id: Optional[str],
        action: str,
        payload: dict,
        timeout: Optional[float] = None,
    ) -> dict:
        """Sends a command to a specific device and awaits the result."""
        ws = self.active_sockets.get(target_device_id) if target_device_id else None
        if not ws:
            return {
                "success": False,
                "error": "DEVICE_OFFLINE",
                "reply": f"Target device '{target_device_id}' is offline or not connected.",
            }

        req_id = f"req_{next(self._req_counter)}_{uuid.uuid4().hex[:6]}"
        fut = asyncio.get_running_loop().create_future()
        self.pending_requests[req_id] = fut
        self._pending_owner[req_id] = target_device_id

        msg = {
            "req_id": req_id,
            "action": action,
            "payload": payload or {},
            "timestamp": time.time(),
        }
        t0 = time.perf_counter()
        try:
            if not await self.send_to_socket(ws, msg):
                return {
                    "success": False,
                    "error": "SEND_FAILED",
                    "reply": "Could not reach the device; it may be reconnecting.",
                }
            result = await asyncio.wait_for(fut, timeout=timeout or self.command_timeout)
            if not isinstance(result, dict):
                result = {"success": True, "result": result}
            result = dict(result)
            result.setdefault("round_trip_ms", round((time.perf_counter() - t0) * 1000))
            dev = self.devices.get(target_device_id)
            if dev:
                dev.commands_handled += 1
            return result
        except asyncio.TimeoutError:
            return {
                "success": False,
                "error": "TIMEOUT",
                "reply": "Timed out waiting for response from device.",
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "reply": f"Communication error: {e}",
            }
        finally:
            self.pending_requests.pop(req_id, None)
            self._pending_owner.pop(req_id, None)

    def handle_device_response(self, req_id: str, data: dict):
        """Resolves the pending request future when device replies."""
        fut = self.pending_requests.get(req_id)
        if fut and not fut.done():
            fut.set_result(data)

    # --------------------------------------------------------------- broadcast

    async def broadcast_event(self, event_data: dict, exclude_id: Optional[str] = None):
        """Fans an event out to every connected device and dashboard observer."""
        targets = [
            ws for dev_id, ws in list(self.active_sockets.items())
            if not (exclude_id and dev_id == exclude_id)
        ]
        observers = list(self.observers.values())
        if not targets and not observers:
            return
        results = await asyncio.gather(
            *(self.send_to_socket(ws, event_data) for ws in targets + observers)
        )
        # Dead device sockets are cleaned up by their own receive loop; drop dead observers here.
        for ws, ok in zip(observers, results[len(targets):]):
            if not ok:
                self.remove_observer(ws)
