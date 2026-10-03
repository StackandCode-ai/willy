"""
Willy Watchdog: presence alerts.

"Tell me when my PC is online" / "turn on the PC watchdog": a watch fires when a PC or phone
comes online or goes offline and alerts the user on their phone (and every other screen).
A watch can also run a follow-up request once the device is back ("... then tell me what's
using my CPU"); the answer rides along with the alert.

Offline alerts wait a short grace period, so a quick reconnect (network blip, app restart)
never pings the user; a repeating watchdog only reports a return after it reported the drop.
"""

import json
import time
import uuid
import asyncio
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from server.device_manager import DeviceInfo, describe_duration, describe_when, reason_text

OFFLINE_GRACE_SEC = 20
THEN_QUERY_DELAY_SEC = 6  # let the device's first heartbeat arrive before running the follow-up
DEVICE_WORDS = {"pc": "pc", "computer": "pc", "laptop": "pc", "phone": "phone", "mobile": "phone"}

Deliver = Callable[[Dict[str, Any]], Awaitable[None]]
RunQuery = Callable[[str], Awaitable[Dict[str, Any]]]


def normalize_device(device: Optional[str]) -> str:
    raw = (device or "pc").strip().lower()
    return DEVICE_WORDS.get(raw, raw)


class Watchdog:
    def __init__(self, path: Optional[Path], deliver: Deliver, run_query: Optional[RunQuery] = None,
                 is_online: Callable[[str], bool] = lambda _device_id: False,
                 grace_sec: float = OFFLINE_GRACE_SEC, then_delay_sec: float = THEN_QUERY_DELAY_SEC):
        self.path = Path(path) if path else None
        self.deliver = deliver
        self.run_query = run_query
        self.is_online = is_online
        self.grace_sec = grace_sec
        self.then_delay_sec = then_delay_sec
        self.watches: List[Dict[str, Any]] = self._load()
        self._alerted_offline: Dict[str, float] = {}  # device_id -> when its offline alert went out
        self._tasks: set = set()

    # ------------------------------------------------------------ persistence

    def _load(self) -> List[Dict[str, Any]]:
        if not self.path or not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return [w for w in data if isinstance(w, dict) and w.get("id")] if isinstance(data, list) else []
        except (OSError, ValueError) as e:
            print(f"[Watchdog] Could not read watches: {e}")
            return []

    def _save(self) -> None:
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.watches, indent=2), encoding="utf-8")
        except OSError as e:
            print(f"[Watchdog] Could not save watches: {e}")

    # ------------------------------------------------------------- management

    def add(self, device: str = "pc", event: str = "online", repeat: bool = False,
            then: Optional[str] = None, source: str = "") -> Dict[str, Any]:
        device = normalize_device(device)
        event = event if event in ("online", "offline", "both") else "online"
        then = (then or "").strip() or None
        for w in self.watches:  # asking twice doesn't create two alerts
            if (w["device"], w["event"], w.get("repeat", False), w.get("then")) == (device, event, bool(repeat), then):
                return w
        watch = {
            "id": f"watch_{uuid.uuid4().hex[:8]}",
            "device": device,
            "event": event,
            "repeat": bool(repeat),
            "then": then,
            "source": source,
            "created_at": time.time(),
            "fired": 0,
        }
        self.watches.append(watch)
        self._save()
        return watch

    def remove(self, watch_id: Optional[str] = None, device: Optional[str] = None) -> int:
        before = len(self.watches)
        if watch_id:
            self.watches = [w for w in self.watches if w["id"] != watch_id]
        elif device and normalize_device(device) != "all":
            target = normalize_device(device)
            self.watches = [w for w in self.watches if w["device"] != target]
        else:
            self.watches = []
        removed = before - len(self.watches)
        if removed:
            self._save()
        return removed

    def list(self) -> List[Dict[str, Any]]:
        return [dict(w, summary=self.describe(w)) for w in self.watches]

    @staticmethod
    def describe(watch: Dict[str, Any]) -> str:
        who = {"pc": "your PC", "phone": "your phone"}.get(watch["device"], watch["device"])
        what = {"online": "comes online", "offline": "goes offline", "both": "goes offline or comes back"}[watch["event"]]
        text = f"{'Every time' if watch.get('repeat') else 'Once'} {who} {what}"
        return text + (f", then: {watch['then']}" if watch.get("then") else "")

    # --------------------------------------------------------------- presence

    @staticmethod
    def _matches(watch: Dict[str, Any], kind: str, dev: DeviceInfo) -> bool:
        if watch["event"] not in (kind, "both"):
            return False
        target = watch["device"]
        if target in ("pc", "phone"):
            return dev.device_type == ("pc" if target == "pc" else "mobile")
        return target == dev.device_id

    def on_presence(self, kind: str, dev: DeviceInfo, info: Dict[str, Any]) -> None:
        """DeviceManager presence listener (runs on the event loop)."""
        matching = [w for w in self.watches if self._matches(w, kind, dev)]
        if not matching:
            if kind == "online":
                self._alerted_offline.pop(dev.device_id, None)
            return
        task = asyncio.get_running_loop().create_task(self._handle(kind, dev, dict(info), matching))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _handle(self, kind: str, dev: DeviceInfo, info: Dict[str, Any], matching: List[Dict[str, Any]]) -> None:
        try:
            if kind == "offline":
                await asyncio.sleep(self.grace_sec)
                if self.is_online(dev.device_id):
                    return  # came straight back: not worth a ping
                self._alerted_offline[dev.device_id] = time.time()
                await self._fire(matching, self._offline_alert(dev))
                return

            alerted = self._alerted_offline.pop(dev.device_id, None)
            # A repeating watchdog reports a return only after it reported the drop (skips
            # blips); a one-off "tell me when it's online" always fires.
            firing = [w for w in matching if not (w.get("repeat") and w["event"] == "both" and alerted is None)]
            if not firing:
                return
            alert = self._online_alert(dev, info)
            follow_ups = [w["then"] for w in firing if w.get("then")]
            if follow_ups and self.run_query:
                await asyncio.sleep(self.then_delay_sec)
                replies = []
                for query in follow_ups:
                    try:
                        res = await self.run_query(query)
                        replies.append(str(res.get("reply") or "").strip())
                    except Exception as e:  # noqa: BLE001 - reported in the alert
                        replies.append(f"I couldn't run '{query}': {e}")
                alert["reply"] = " ".join(r for r in replies if r) or None
            await self._fire(firing, alert)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[Watchdog] Alert failed: {e}")

    async def _fire(self, watches: List[Dict[str, Any]], alert: Dict[str, Any]) -> None:
        alert["watch_ids"] = [w["id"] for w in watches]
        for w in watches:
            w["fired"] = int(w.get("fired", 0)) + 1
            w["last_fired_at"] = time.time()
        fired_once = {w["id"] for w in watches if not w.get("repeat")}
        if fired_once:
            self.watches = [w for w in self.watches if w["id"] not in fired_once]
        self._save()
        await self.deliver(alert)

    @staticmethod
    def _base(dev: DeviceInfo, event: str) -> Dict[str, Any]:
        return {
            "type": "presence_alert",
            "device_id": dev.device_id,
            "device_name": dev.name,
            "device_type": dev.device_type,
            "event": event,
            "reply": None,
            "timestamp": time.time(),
        }

    def _offline_alert(self, dev: DeviceInfo) -> Dict[str, Any]:
        why = reason_text(dev.device_type, dev.offline_reason)
        since = dev.offline_since or dev.last_seen
        message = f"Offline since {describe_when(since)}" + (f": {why}." if why else ".")
        return {**self._base(dev, "offline"), "title": f"{dev.name} went offline", "message": message,
                "reason": dev.offline_reason, "reason_text": why, "offline_for_sec": None}

    def _online_alert(self, dev: DeviceInfo, info: Dict[str, Any]) -> Dict[str, Any]:
        offline_for = info.get("offline_for")
        previous = info.get("previous_reason")
        why = reason_text(dev.device_type, previous)
        if offline_for:
            title = f"{dev.name} is back online"
            message = f"Back after {describe_duration(offline_for)}" + (f" ({why})." if why else ".")
        else:
            title = f"{dev.name} is online"
            message = "It just connected to Willy."
        return {**self._base(dev, "online"), "title": title, "message": message, "reason": previous,
                "reason_text": why, "offline_for_sec": round(offline_for) if offline_for else None}
