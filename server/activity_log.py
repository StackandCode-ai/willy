"""
Willy Activity Log.
Records every command handled by the hub (source, tools used, latency, outcome) in a
bounded ring buffer persisted to disk, and notifies listeners so UIs update live.
"""

import json
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional

from server.config import DATA_DIR

ACTIVITY_FILE = DATA_DIR / "activity_log.json"


class ActivityLog:
    def __init__(self, path: Optional[Path] = ACTIVITY_FILE, maxlen: int = 300):
        self.path = path
        self.entries: Deque[Dict[str, Any]] = deque(maxlen=maxlen)
        self._index: Dict[str, Dict[str, Any]] = {}
        self.listeners: List[Callable[[Dict[str, Any]], None]] = []
        self.started_at = time.time()
        self._load()

    def _load(self) -> None:
        if not self.path or not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            for entry in data[-self.entries.maxlen:]:
                if isinstance(entry, dict) and entry.get("id"):
                    if entry.get("status") == "running":
                        entry["status"] = "error"
                        entry["reply"] = entry.get("reply") or "Interrupted by a server restart."
                    self.entries.append(entry)
                    self._index[entry["id"]] = entry
        except Exception as e:
            print(f"[ActivityLog] Could not read {self.path.name}: {e}")

    def _save(self) -> None:
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(list(self.entries)), encoding="utf-8")
            tmp.replace(self.path)
        except Exception as e:
            print(f"[ActivityLog] Could not save activity: {e}")

    def _notify(self, entry: Dict[str, Any]) -> None:
        for listener in list(self.listeners):
            try:
                listener(dict(entry))
            except Exception as e:
                print(f"[ActivityLog] Listener error: {e}")

    def start(self, source: str, query: str, device_id: Optional[str] = None) -> Dict[str, Any]:
        """Creates a 'running' entry and announces it."""
        entry = {
            "id": f"act_{uuid.uuid4().hex[:10]}",
            "ts": time.time(),
            "source": (source or "api")[:24],
            "query": (query or "")[:500],
            "device_id": device_id,
            "status": "running",
            "reply": None,
            "tools": [],
            "success": None,
            "fast_path": False,
            "latency_ms": None,
            "timings": {},
        }
        if len(self.entries) == self.entries.maxlen:
            evicted = self.entries[0]
            self._index.pop(evicted.get("id"), None)
        self.entries.append(entry)
        self._index[entry["id"]] = entry
        self._notify(entry)
        return entry

    def finish(self, entry_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
        """Completes an entry (status defaults to done/error from `success`)."""
        entry = self._index.get(entry_id)
        if entry is None:
            return None
        if isinstance(fields.get("reply"), str):
            fields["reply"] = fields["reply"][:1000]
        entry.update(fields)
        if entry.get("latency_ms") is None:
            entry["latency_ms"] = round((time.time() - entry["ts"]) * 1000)
        if entry.get("status") == "running":
            entry["status"] = "done" if entry.get("success", True) is not False else "error"
        self._save()
        self._notify(entry)
        return entry

    def record(self, source: str, query: str, **fields: Any) -> Dict[str, Any]:
        """One-shot entry for actions that complete immediately."""
        entry = self.start(source, query, fields.pop("device_id", None))
        return self.finish(entry["id"], **fields) or entry

    def recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit), self.entries.maxlen))
        return list(self.entries)[-limit:][::-1]

    def stats(self) -> Dict[str, Any]:
        done = [e for e in self.entries if e.get("status") in ("done", "error")]
        latencies = sorted(e["latency_ms"] for e in done if isinstance(e.get("latency_ms"), (int, float)))
        fast = [e for e in done if e.get("fast_path")]
        fast_lat = sorted(e["latency_ms"] for e in fast if isinstance(e.get("latency_ms"), (int, float)))

        def pct(values: List[float], q: float) -> Optional[int]:
            if not values:
                return None
            return round(values[min(len(values) - 1, int(q * (len(values) - 1)))])

        return {
            "total": len(done),
            "succeeded": sum(1 for e in done if e.get("success") is not False),
            "failed": sum(1 for e in done if e.get("success") is False),
            "fast_path": len(fast),
            "latency_p50_ms": pct(latencies, 0.5),
            "latency_p90_ms": pct(latencies, 0.9),
            "fast_path_p50_ms": pct(fast_lat, 0.5),
            "by_source": _count_by(done, "source"),
        }


def _count_by(entries: List[Dict[str, Any]], key: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for e in entries:
        k = str(e.get(key) or "unknown")
        counts[k] = counts.get(k, 0) + 1
    return counts
