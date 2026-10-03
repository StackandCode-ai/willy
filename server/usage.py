"""
What this hub is used for, as counts only: how many commands ran per day, which tools (by name) and how
many failed. No commands, replies, files or names are recorded. A hub that registers with the master
sends these daily counts (see registry.py); they also show the owner their own usage.
"""

import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

KEEP_DAYS = 45
MAX_TOOLS_PER_DAY = 120


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


class UsageCounter:
    def __init__(self, path: Optional[Path]):
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self.days: Dict[str, Dict[str, Any]] = {}
        self.sent_through = ""   # last day (inclusive) already reported
        self._load()

    def _load(self) -> None:
        if not self.path or not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.days = data.get("days") or {}
            self.sent_through = data.get("sent_through") or ""
        except (OSError, ValueError):
            pass

    def _save(self) -> None:
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"days": self.days, "sent_through": self.sent_through}), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass

    def _day(self) -> Dict[str, Any]:
        day = self.days.setdefault(_today(), {"commands": 0, "failures": 0, "tools": {}})
        if len(self.days) > KEEP_DAYS:
            for old in sorted(self.days)[:-KEEP_DAYS]:
                del self.days[old]
        return day

    def note(self, tools: Iterable[str] = (), success: bool = True, command: bool = True) -> None:
        with self._lock:
            day = self._day()
            if command:
                day["commands"] += 1
            if not success:
                day["failures"] += 1
            for name in tools:
                name = str(name or "")[:60]
                if name and (name in day["tools"] or len(day["tools"]) < MAX_TOOLS_PER_DAY):
                    day["tools"][name] = day["tools"].get(name, 0) + 1
            self._save()

    def unsent(self) -> Dict[str, Dict[str, Any]]:
        """Completed days (not today) that haven't been reported yet."""
        today = _today()
        with self._lock:
            return {d: v for d, v in sorted(self.days.items()) if d < today and d > self.sent_through}

    def mark_sent(self, through_day: str) -> None:
        with self._lock:
            self.sent_through = max(self.sent_through, through_day)
            self._save()

    def totals(self, days: int = 7) -> Dict[str, Any]:
        with self._lock:
            keys = sorted(self.days)[-days:]
            tools: Dict[str, int] = {}
            for k in keys:
                for t, n in self.days[k]["tools"].items():
                    tools[t] = tools.get(t, 0) + n
            return {"days": len(keys), "commands": sum(self.days[k]["commands"] for k in keys),
                    "failures": sum(self.days[k]["failures"] for k in keys),
                    "tools": dict(sorted(tools.items(), key=lambda kv: -kv[1])[:20])}
