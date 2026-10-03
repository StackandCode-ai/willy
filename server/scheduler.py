"""
Willy Reminder & Alarm Scheduler.
Fires due reminders, daily alarms and the scheduled morning call by pushing events to
every connected device (the PC shows a toast and speaks; the phone pops an alert).
"""

import time
import asyncio
import datetime as dt
from typing import Any, Awaitable, Callable, Dict, List, Optional

from server import timeutil

CHECK_INTERVAL_SEC = 10
MAX_LATE_SEC = 6 * 3600        # reminders overdue by more than this are marked missed, not fired
DAILY_FIRE_WINDOW_SEC = 5 * 60   # alarms / morning call fire within 5 min of their time

Broadcast = Callable[[Dict[str, Any]], Awaitable[None]]


class ReminderScheduler:
    def __init__(self, morning_service, broadcast: Broadcast):
        self.morning = morning_service
        self.broadcast = broadcast
        self.last_tick: Optional[float] = None

    async def run(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception as e:
                print(f"[Scheduler] Tick error: {e}")
            await asyncio.sleep(CHECK_INTERVAL_SEC)

    async def tick(self, now: Optional[dt.datetime] = None) -> List[Dict[str, Any]]:
        now = now or timeutil.user_now()
        self.last_tick = time.time()
        cfg = self.morning.config
        events: List[Dict[str, Any]] = []
        changed = False

        for rem in cfg.get("reminders", []):
            if rem.get("completed") or rem.get("fired_at"):
                continue
            if isinstance(rem.get("due_at"), (int, float)):  # exact (timers, "in 30 minutes")
                due = dt.datetime.fromtimestamp(rem["due_at"], now.tzinfo) if now.tzinfo \
                    else dt.datetime.fromtimestamp(rem["due_at"])
            else:
                due = timeutil.due_datetime(rem.get("date"), rem.get("time"), now)
            if due is None:
                continue
            lateness = (now - due).total_seconds()
            if lateness < 0:
                continue
            rem["fired_at"] = time.time()
            changed = True
            if lateness > MAX_LATE_SEC:
                rem["missed"] = True
                continue
            is_timer = rem.get("kind") == "timer"
            events.append({
                "type": "reminder_due",
                "kind": "timer" if is_timer else "reminder",
                "title": "Timer done" if is_timer else "Reminder",
                "message": rem.get("text") or ("Your timer is done." if is_timer else "Reminder"),
                "reminder": dict(rem),
                "timestamp": time.time(),
            })

        today = now.date().isoformat()
        for alarm in cfg.get("alarms", []):
            if not alarm.get("enabled", True) or alarm.get("last_fired_date") == today:
                continue
            if self._daily_due(alarm.get("time"), now, created_at=alarm.get("created_at")):
                alarm["last_fired_date"] = today
                changed = True
                events.append({
                    "type": "reminder_due",
                    "kind": "alarm",
                    "title": "Alarm",
                    "message": alarm.get("label") or "Alarm",
                    "alarm": dict(alarm),
                    "timestamp": time.time(),
                })

        if cfg.get("call_enabled", True) and cfg.get("last_call_date") != today:
            if self._daily_due(cfg.get("call_time"), now):
                cfg["last_call_date"] = today
                changed = True
                events.append({
                    "type": "morning_call_due",
                    "title": "Morning call",
                    "message": "Your daily Willy briefing is ready.",
                    "call_time": cfg.get("call_time"),
                    "timestamp": time.time(),
                })

        if changed:
            self.morning.save_config()
        for event in events:
            await self.broadcast(event)
        if changed:
            # Fired / missed flags changed: let open reminder lists refresh without refetching.
            await self.broadcast({
                "type": "reminders_changed",
                "reminders": cfg.get("reminders", []),
                "alarms": cfg.get("alarms", []),
                "timestamp": time.time(),
            })
        return events

    @staticmethod
    def _daily_due(time_text: Any, now: dt.datetime, created_at: Any = None) -> bool:
        clock = timeutil.parse_clock(time_text)
        if clock is None:
            return False
        at = now.replace(hour=clock[0], minute=clock[1], second=0, microsecond=0)
        if isinstance(created_at, (int, float)) and created_at > at.timestamp():
            return False  # set after today's time already passed: first ring is tomorrow
        lateness = (now - at).total_seconds()
        return 0 <= lateness <= DAILY_FIRE_WINDOW_SEC
