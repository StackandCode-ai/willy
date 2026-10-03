"""
Willy PC — desktop command center for this Windows PC.

The window is a thin, fast UI over the shared realtime engine
(:class:`pc_client.client.PCClientNode`), which owns the hub WebSocket: heartbeats with live
telemetry, actions sent from the phone / dashboard (executed through the local executor),
and the hub's live device + activity events.

Threading model
- The node runs its asyncio loop on a daemon thread. Its callbacks (status, log, events,
  telemetry, pop-up notifications) are queued and drained on the Tk thread, so widgets are
  only ever touched from the Tk thread and the asyncio loop never waits on the UI.
- Blocking local work (executor actions, process sampling, hardware specs, audio) runs in a
  small worker pool and reports back through the same queue.
- Widgets are built once and updated in place; only the visible page refreshes its heavy
  parts (gauges, charts, tables), at the telemetry cadence.

Tray
- Closing the window hides it to the notification-area icon (pc_client.tray); Willy keeps
  serving the phone and dashboard. The icon's menu reopens it, reconnects, toggles Start
  with Windows and quits. While hidden, no page does any drawing work.
"""

import base64
import ctypes
import datetime
import io
import json
import math
import os
import queue
import re
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import webbrowser
import asyncio
import tkinter as tk
import tkinter.font as tkfont
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import customtkinter as ctk
from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps, ImageTk

from pc_client.brand import logo_image
from pc_client.client import PCClientNode
from pc_client.config import HEARTBEAT_INTERVAL_SEC
from pc_client.executor import LocalExecutor
from pc_client.telemetry import collect_static_specs
from pc_client.tray import TrayIcon
from pc_client.hey_willy import HeyWilly
from pc_client import prefs

ctk.set_appearance_mode("dark")
# The theme is fixed to dark, so CTk's 30 ms poll of the OS theme has nothing to do: slow it down.
ctk.AppearanceModeTracker.update_loop_interval = 1000

APP_TITLE = "Willy PC"
HISTORY_POINTS = 60
PROCESS_REFRESH_MS = 3000
PROCESS_ROWS = 40
MAX_LOG_LINES = 500
MAX_CHAT_ROWS = 120
MAX_ACTIVITY_ROWS = 100
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

# ----------------------------------------------------------------------------- palette

BG = "#070913"
SURFACE = "#0B0F19"
CARD = "#0F172A"
CARD_ALT = "#111827"
BORDER = "#1E293B"
BORDER_HI = "#334155"
GRID = "#162033"
CYAN = "#00F2FE"
PURPLE = "#7C3AED"
GREEN = "#10B981"
RED = "#EF4444"
AMBER = "#F59E0B"
SKY = "#38BDF8"
TEXT = "#F8FAFC"
MUTED = "#94A3B8"
DIM = "#64748B"
SOFT = "#CBD5E1"
SOFT_PURPLE = "#A78BFA"
SOFT_RED = "#FCA5A5"
TOAST_KEY = "#010203"  # transparent colour key -> rounded toast corners

# Segoe Fluent Icons (Windows 11) / Segoe MDL2 Assets (Windows 10) code points.
ICONS: Dict[str, int] = {
    "overview": 0xEC4A, "assistant": 0xE8BD, "processes": 0xE9D9, "devices": 0xE703,
    "activity": 0xE81C, "tools": 0xE90F, "log": 0xE756,
    "dashboard": 0xE8A7, "call": 0xE717, "lock": 0xE72E, "camera": 0xE722, "desktop": 0xE7F4,
    "prev": 0xE892, "play": 0xE768, "next": 0xE893, "download": 0xE896,
    "volume": 0xE767, "mute": 0xE74F, "brightness": 0xE706, "refresh": 0xE72C, "send": 0xE724,
    "bell": 0xEA8F, "link": 0xE71B, "close": 0xE711, "search": 0xE721, "copy": 0xE8C8,
    "delete": 0xE74D, "shell": 0xE756, "pulse": 0xE9D9, "globe": 0xE774, "folder": 0xE8B7,
    "info": 0xE946, "chip": 0xE950, "memory": 0xE964, "disk": 0xEDA2, "battery": 0xE83F,
    "wifi": 0xE701, "clock": 0xE823, "window": 0xE737, "moon": 0xE708, "bolt": 0xE945,
    "speed": 0xEC4A, "phone": 0xE8EA, "laptop": 0xE7F8, "check": 0xE930, "error": 0xE783,
    "running": 0xE916, "chevron_down": 0xE70D, "chevron_up": 0xE70E, "calendar": 0xE787,
    "mail": 0xE715, "chat": 0xE8F2, "updown": 0xE8CB, "user": 0xE77B, "power": 0xE7E8,
    "tasks": 0xE9F5, "gear": 0xE713,
    "flashlight": 0xE754, "vibrate": 0xE877, "navigate": 0xE816, "music": 0xE8D6, "paste": 0xE77F,
    "apps": 0xE74C, "stop": 0xE71A, "cellular": 0xEC3B, "ethernet": 0xE839, "timer": 0xE916,
    "message": 0xE8BD, "server": 0xE968, "health": 0xE95E,
}


# ----------------------------------------------------------------------------- helpers

def _rgb(color: str) -> Tuple[int, int, int]:
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def mix(a: str, b: str, t: float) -> str:
    """Blends colour a toward colour b (t=0 -> a, t=1 -> b)."""
    ra, rb = _rgb(a), _rgb(b)
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(ra, rb))


def tint(color: str, amount: float = 0.16, base: str = CARD) -> str:
    """A dark background tinted with an accent colour (badges, pills, hovers)."""
    return mix(base, color, amount)


def _num(value: Any) -> Optional[float]:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def fmt_rate(kbps: Any) -> str:
    v = _num(kbps)
    if v is None:
        return "—"
    if v >= 1024:
        return f"{v / 1024:.1f} MB/s"
    return f"{v:.1f} KB/s" if v < 10 else f"{v:.0f} KB/s"


def fmt_duration(seconds: Any) -> str:
    v = _num(seconds)
    if v is None:
        return "—"
    v = int(max(0, v))
    days, rem = divmod(v, 86400)
    hours, rem = divmod(rem, 3600)
    mins, secs = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {mins:02d}m"
    if mins:
        return f"{mins}m {secs:02d}s"
    return f"{secs}s"


def fmt_ago(ts: Any, now: Optional[float] = None) -> str:
    v = _num(ts)
    if v is None:
        return "—"
    delta = max(0.0, (now or time.time()) - v)
    if delta < 5:
        return "just now"
    if delta < 60:
        return f"{int(delta)}s ago"
    if delta < 3600:
        return f"{int(delta // 60)}m ago"
    if delta < 86400:
        return f"{int(delta // 3600)}h ago"
    return f"{int(delta // 86400)}d ago"


def fmt_mem(mb: Any) -> str:
    v = _num(mb)
    if v is None:
        return "—"
    return f"{v / 1024:.1f} GB" if v >= 1024 else f"{v:.0f} MB"


def ellipsize(text: Any, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: max(1, limit - 1)].rstrip() + "…"


def fit_text(font: tkfont.Font, text: Any, width: int) -> str:
    """Longest prefix of text (plus an ellipsis) that fits in `width` pixels."""
    text = " ".join(str(text or "").split())
    if font.measure(text) <= width:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if font.measure(text[:mid] + "…") <= width:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo].rstrip() + "…"


def _level_color(pct: Optional[float], base: str, warn: float, crit: float) -> str:
    if pct is None:
        return base
    return RED if pct >= crit else AMBER if pct >= warn else base


def style_titlebar(window: tk.Misc) -> None:
    """Paints our own window's title bar in the app surface colour (Windows 11 DWM;
    silently ignored elsewhere) so it doesn't clash with the system accent colour."""
    if sys.platform != "win32":
        return
    try:
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        for attribute, color in ((35, SURFACE), (36, TEXT), (34, BORDER)):  # caption, text, border
            r, g, b = _rgb(color)
            value = ctypes.c_int(r | (g << 8) | (b << 16))
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value))
    except Exception:
        pass


# ----------------------------------------------------------------------------- phone helpers
# Pure functions behind the Phone page (unit-tested in test_phone_page.py).

PHONE_NOTIF_LIMIT = 20
PHONE_NOTIF_MIN_GAP_SEC = 15.0  # automatic notification refreshes: at most this often
PHONE_VOLUME_DEBOUNCE_MS = 300
PHONE_RING = {"message": "Find My Phone", "duration_sec": 20}
CLIPBOARD_SEND_LIMIT = 50_000   # characters sent to the phone's clipboard

_URL_RE = re.compile(r"^(?:https?://|www\.)\S+$", re.I)
_HOST_RE = re.compile(r"^(?:(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}|(?:\d{1,3}\.){3}\d{1,3})"
                      r"(?::\d{1,5})?(?:[/?#]\S*)?$", re.I)
_IPV4_START_RE = re.compile(r"^(?:\d{1,3}\.){3}\d{1,3}(?:[:/?#]|$)")
_MERIDIEM = r"(a\.?m\.?|p\.?m\.?|a|p)"
_ALARM_RE = re.compile(rf"^(\d{{1,2}})(?:\s*[:.h]\s*(\d{{2}}))?\s*{_MERIDIEM}?$")
_ALARM_COMPACT_RE = re.compile(rf"^(\d{{1,2}})(\d{{2}})\s*{_MERIDIEM}?$")  # "730", "1930"
_TIMER_UNITS = {"": 60, "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1, "m": 60, "min": 60, "mins": 60,
                "minute": 60, "minutes": 60, "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600}


def looks_like_url(text: Any) -> bool:
    """A link ("https://…", "www.…") or a bare domain / IPv4 address with an optional port
    and path ("example.com/docs", "192.168.1.5:8080"). Anything with spaces is a note."""
    s = str(text or "").strip()
    return bool(s) and bool(_URL_RE.match(s) or _HOST_RE.match(s))


def as_url(text: Any) -> str:
    """Adds a scheme when it's missing: https for names, http for a bare IPv4 address."""
    s = str(text or "").strip()
    if re.match(r"^https?://", s, re.I):
        return s
    return ("http://" if _IPV4_START_RE.match(s) else "https://") + s


def quickdrop_payload(text: Any, title: str = "From PC") -> Dict[str, Any]:
    """The phone's quickdrop payload: a link opens in its browser, anything else shows as a note."""
    s = str(text or "").strip()
    return {"url": as_url(s)} if looks_like_url(s) else {"text": s, "title": title}


def parse_alarm_time(text: Any) -> Optional[Tuple[int, int]]:
    """"7:30 am", "7.30pm", "19:05", "7 pm", "0730", "noon" -> (hour 0-23, minute); None if invalid."""
    s = re.sub(r"^at\s+", "", " ".join(str(text or "").lower().split()))
    if s in ("noon", "midday"):
        return 12, 0
    if s == "midnight":
        return 0, 0
    m = _ALARM_RE.match(s) or _ALARM_COMPACT_RE.match(s)
    if not m:
        return None
    hour, minute = int(m.group(1)), int(m.group(2) or 0)
    meridiem = (m.group(3) or "")[:1]
    if minute > 59:
        return None
    if meridiem:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if meridiem == "p" else 0)
    elif hour > 23:
        return None
    return hour, minute


def parse_timer_seconds(text: Any) -> Optional[int]:
    """The timer box takes minutes ("5", "2.5"); "90 s" and "1 h" work too. Returns seconds
    (1 s to 24 h, the longest timer Android accepts) or None."""
    s = " ".join(str(text or "").lower().split())
    m = re.fullmatch(r"(\d+(?:[.,]\d+)?)\s*([a-z]*)", s)
    if not m or m.group(2) not in _TIMER_UNITS:
        return None
    seconds = round(float(m.group(1).replace(",", ".")) * _TIMER_UNITS[m.group(2)])
    return seconds if 1 <= seconds <= 24 * 3600 else None


def fmt_clock(hour: int, minute: int) -> str:
    return f"{hour % 12 or 12}:{minute:02d} {'AM' if hour < 12 else 'PM'}"


def fmt_when(ts: Any, now: Optional[float] = None) -> str:
    """A moment the way people say it (local time): "9:42 PM" today, "yesterday 9:42 PM",
    "Mon 9:42 PM" within a week, else "12 Sep 9:42 PM"."""
    v = _num(ts)
    if v is None:
        return "—"
    t = time.localtime(v)
    n = time.localtime(time.time() if now is None else now)
    clock = fmt_clock(t.tm_hour, t.tm_min)
    days = (datetime.date(n.tm_year, n.tm_mon, n.tm_mday) - datetime.date(t.tm_year, t.tm_mon, t.tm_mday)).days
    if days <= 0:
        return clock
    if days == 1:
        return f"yesterday {clock}"
    if days < 7:
        return f"{time.strftime('%a', t)} {clock}"
    return f"{t.tm_mday} {time.strftime('%b', t)} {clock}"


def fmt_last_seen(ts: Any, now: Optional[float] = None) -> str:
    """"Last seen just now" / "Last seen 12m ago", or the moment itself once it's hours old."""
    v = _num(ts)
    if not v or v <= 0:
        return "Never seen"
    now = time.time() if now is None else now
    return f"Last seen {fmt_ago(v, now) if now - v < 6 * 3600 else fmt_when(v, now)}"


def device_online(dev: Any) -> bool:
    """A hub device dict's online flag (the hub's `presence` block when that's all there is)."""
    if not isinstance(dev, dict):
        return False
    if "online" in dev:
        return bool(dev["online"])
    presence = dev.get("presence")
    if isinstance(presence, dict) and presence.get("state") in ("online", "offline"):
        return presence["state"] == "online"
    return dev.get("status") == "online"


def presence_line(dev: Dict[str, Any], now: Optional[float] = None) -> str:
    """"Online · 64 ms", or "Offline since 9:42 PM — <why>". Details come from the hub's
    `presence` block while it agrees with the online flag (older hubs: last_seen only)."""
    online = device_online(dev)
    presence = dev.get("presence") if isinstance(dev.get("presence"), dict) else {}
    if presence.get("state") != ("online" if online else "offline"):
        presence = {}  # missing, or stale (e.g. marked offline locally without a fresh copy)
    if online:
        ping = _num((dev.get("telemetry") or {}).get("last_ping_ms"))
        return f"Online · {ping:.0f} ms" if ping is not None else "Online"
    since = _num(presence.get("since")) or _num(presence.get("last_online_at")) or _num(dev.get("last_seen"))
    text = f"Offline since {fmt_when(since, now)}" if since else "Offline"
    why = " ".join(str(presence.get("reason_text") or "").split())
    return f"{text} — {why}" if why else text


def pick_phone(devices: Any, current_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The phone the Phone page follows: the most recently seen phone, preferring online ones.
    It sticks with `current_id` while that phone is online (or while none is), so two phones
    online at once don't make the page flip between them on every heartbeat."""
    pool = devices.values() if isinstance(devices, dict) else (devices or [])
    phones = [d for d in pool if isinstance(d, dict) and d.get("device_type") == "mobile" and d.get("device_id")]
    if not phones:
        return None
    current = next((d for d in phones if d["device_id"] == current_id), None) if current_id else None
    if current is not None and (device_online(current) or not any(device_online(d) for d in phones)):
        return current
    return max(phones, key=lambda d: (device_online(d), _num(d.get("last_seen")) or 0.0))


def notification_time(value: Any) -> Optional[float]:
    """Epoch seconds from a notification's `time` (the phone sends milliseconds)."""
    v = _num(value)
    if v is None or v <= 0:
        return None
    return v / 1000.0 if v > 1e11 else v


def normalize_notifications(items: Any, limit: int = PHONE_NOTIF_LIMIT) -> List[Dict[str, Any]]:
    """The phone's recent notifications as display rows {app, title, text, ts}, newest first."""
    rows: List[Dict[str, Any]] = []
    for n in items if isinstance(items, list) else []:
        if not isinstance(n, dict):
            continue
        title = " ".join(str(n.get("title") or "").split())
        text = " ".join(str(n.get("text") or "").split())
        if not title and not text:
            continue
        if not title:
            title, text = text, ""
        app = " ".join(str(n.get("app") or n.get("package") or "App").split())
        rows.append({"app": app, "title": title, "text": text, "ts": notification_time(n.get("time"))})
    rows.sort(key=lambda r: r["ts"] or 0.0, reverse=True)
    return rows[:limit]


# ----------------------------------------------------------------------------- server helpers
# The Server page talks to the hub's REST API (/api/v1/server/...). Pure helpers below are
# unit-tested in tests/test_server_page.py.

SERVER_REFRESH_SEC = 30.0   # status re-read while the page is visible (websites are not re-checked)
SERVER_LOG_LINES = 120
SERVER_SITES_TIMEOUT_SEC = 180.0  # "Check sites now" checks every website one after another
_SERVER_SEVERE = ("app", "loop", "svc", "ctr", "site")  # something is down (vs. running hot / cert soon)
_SERVER_KEY_LABELS = {"cpu": "High CPU", "ram": "High memory use", "disk": "Disk almost full",
                      "app": "App {}", "loop": "{} crash loop", "svc": "Service {}", "ctr": "Container {}",
                      "site": "Website {}", "cert": "Certificate for {}"}
_DOMAIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?$")


class HubError(Exception):
    """A hub REST call that failed. `offline`: the hub couldn't be reached at all;
    `status`: the HTTP status when it answered with an error."""

    def __init__(self, message: str, status: Optional[int] = None, offline: bool = False):
        super().__init__(message)
        self.status = status
        self.offline = offline


def hub_json(method: str, path: str, body: Optional[Dict[str, Any]] = None,
             timeout: float = 20.0) -> Dict[str, Any]:
    """Calls the hub's REST API with this PC's token and returns the JSON answer. Blocking:
    run it in the worker pool. Goes through file_relay's connection helpers, so the hub's last
    known address is used when DNS is slow. Error messages never include the token."""
    import http.client

    from pc_client.tools import file_relay

    _scheme, host, _port, base = file_relay._hub()
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {**file_relay._headers(), "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    conn = None
    try:
        conn = file_relay._connect(timeout=timeout)
        conn.request(method, base + path, body=data, headers=headers)
        res = conn.getresponse()
        status, raw = res.status, res.read()
    except (OSError, http.client.HTTPException) as e:
        raise HubError(f"Can't reach your Willy server ({host}).", offline=True) from e
    finally:
        if conn is not None:
            conn.close()
    try:
        payload = json.loads(raw.decode("utf-8", errors="replace")) if raw else {}
    except ValueError:
        payload = {}
    if status >= 400:
        detail = payload.get("detail") if isinstance(payload, dict) else None
        if status in (401, 403):
            raise HubError("The hub rejected this PC's token (check WILLY_REMOTE_TOKEN).", status)
        if status in (502, 503, 504):
            raise HubError(f"Your Willy server isn't answering (HTTP {status}) — it may be restarting.",
                           status, offline=True)
        raise HubError(detail if isinstance(detail, str) and detail.strip() else f"The hub answered HTTP {status}.",
                       status)
    return payload if isinstance(payload, dict) else {}


def server_problem_label(key: Any) -> str:
    """A muted problem key ("site:test.truewilly.com") as words ("Website test.truewilly.com")."""
    kind, _, name = str(key or "").partition(":")
    label = _SERVER_KEY_LABELS.get(kind)
    if label is None:
        return str(key or "Problem")
    return label.format(name) if "{}" in label else label


def server_problem_severe(key: Any) -> bool:
    return str(key or "").partition(":")[0] in _SERVER_SEVERE


def server_problems(status: Any) -> List[Dict[str, str]]:
    """The status payload's active (unmuted) problems as {key, message} dicts."""
    items = status.get("problems") if isinstance(status, dict) else None
    return [{"key": str(p["key"]), "message": str(p.get("message") or server_problem_label(p["key"]))}
            for p in items or [] if isinstance(p, dict) and p.get("key")]


def server_state(status: Any) -> Tuple[str, str]:
    """The header chip: ("Healthy", green), or ("2 problems", red when something is down,
    amber when it's only running hot or a certificate expires soon)."""
    problems = server_problems(status)
    if not problems:
        return "Healthy", GREEN
    n = len(problems)
    return f"{n} problem{'s' if n != 1 else ''}", RED if any(server_problem_severe(p["key"]) for p in problems) \
        else AMBER


def app_status_color(status: Any) -> str:
    """A pm2 app status: online is green, transitions amber, anything else (stopped, errored) red."""
    s = str(status or "").lower()
    if s == "online":
        return GREEN
    return AMBER if s in ("launching", "stopping", "waiting restart", "one-launch-status") else RED


def service_color(state: Any) -> str:
    """A systemd `is-active` state."""
    s = str(state or "").lower()
    if s == "active":
        return GREEN
    if s in ("", "unknown"):
        return DIM
    return AMBER if s in ("activating", "deactivating", "reloading") else RED


def container_color(state: Any) -> str:
    """A Docker container state."""
    s = str(state or "").lower()
    if s == "running":
        return GREEN
    return AMBER if s in ("restarting", "paused", "created", "removing") else RED


def cert_color(days: Any) -> str:
    """Days until a website's HTTPS certificate expires: amber at 30 or less, red at 14 or less."""
    d = _num(days)
    if d is None:
        return DIM
    return RED if d <= 14 else AMBER if d <= 30 else GREEN


def cert_text(days: Any) -> str:
    d = _num(days)
    if d is None:
        return "—"
    d = int(d)
    if d < 0:
        return "Expired"
    if d == 0:
        return "Expires today"
    return f"{d} day{'s' if d != 1 else ''} left"


def site_chip(site: Dict[str, Any]) -> Tuple[str, str]:
    """("Up · 200", green) / ("Down · 502", red) / ("Down", red) when nothing answered."""
    code = site.get("status")
    if site.get("up"):
        return (f"Up · {code}" if code else "Up"), GREEN
    return (f"Down · {code}" if code else "Down"), RED


def fmt_ms(ms: Any) -> str:
    v = _num(ms)
    if v is None:
        return "—"
    return f"{v / 1000:.1f} s" if v >= 1000 else f"{v:.0f} ms"


def fmt_load(load: Any) -> str:
    """Load averages (1, 5, 15 min) as "0.12 · 0.30 · 0.25"."""
    if not isinstance(load, (list, tuple)) or not load:
        return "—"
    values = [_num(x) for x in load[:3]]
    return " · ".join(f"{v:.2f}" if v is not None else "—" for v in values)


def sort_sites(sites: Any) -> List[Dict[str, Any]]:
    """Websites to list: down ones first, then by domain."""
    rows = [s for s in sites or [] if isinstance(s, dict) and s.get("domain")]
    return sorted(rows, key=lambda s: (bool(s.get("up")), str(s["domain"]).lower()))


def site_url(domain: Any) -> Optional[str]:
    """https://domain for a plain host name; None for anything else (never opened)."""
    d = str(domain or "").strip()
    return f"https://{d}" if d and "." in d and _DOMAIN_RE.match(d) else None


def server_app_keys(apps: Any) -> List[Tuple[str, Dict[str, Any]]]:
    """(row key, app) pairs in pm2's order. pm2 lists every cluster instance under the same
    name, so repeats get "name #2", "name #3"…"""
    seen: Dict[str, int] = {}
    out: List[Tuple[str, Dict[str, Any]]] = []
    for a in apps or []:
        if not isinstance(a, dict) or not a.get("name"):
            continue
        name = str(a["name"])
        seen[name] = seen.get(name, 0) + 1
        out.append((name if seen[name] == 1 else f"{name} #{seen[name]}", a))
    return out


def restart_warning(name: str) -> str:
    if name == "willy-server":
        return ("This restarts the Willy hub itself. This PC, your phone and the dashboard disconnect "
                "for a moment — Willy reconnects in a few seconds.")
    return f"pm2 restarts {name}. It's unavailable for a few seconds while it starts again."


# The server agent (server_agent/agent.py) is a hub device of type "server": live telemetry in
# its heartbeats, and actions sent through the hub like phone actions (node.send_action).

SERVER_SHELL_TIMEOUT_SEC = 18   # the hub gives a device action 20 s; the shell stops before that
SERVER_PROCESS_ROWS = 15
SERVER_PROCESS_REFRESH_SEC = 10.0
SERVER_TERMINAL_MAX_LINES = 4000
SERVER_HISTORY_LIMIT = 100


def pick_server(devices: Any, current_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The server device the page follows (like pick_phone: sticky, online preferred)."""
    pool = devices.values() if isinstance(devices, dict) else (devices or [])
    servers = [d for d in pool if isinstance(d, dict) and d.get("device_type") == "server" and d.get("device_id")]
    if not servers:
        return None
    current = next((d for d in servers if d["device_id"] == current_id), None) if current_id else None
    if current is not None and (device_online(current) or not any(device_online(d) for d in servers)):
        return current
    return max(servers, key=lambda d: (device_online(d), _num(d.get("last_seen")) or 0.0))


def server_live_system(dev: Any) -> Dict[str, Any]:
    """The server device's heartbeat telemetry in the status snapshot's `system` shape
    (ram in MB, uptime in seconds, load as a list); {} without telemetry."""
    t = dev.get("telemetry") if isinstance(dev, dict) else None
    if not isinstance(t, dict) or _num(t.get("cpu_pct")) is None:
        return {}

    def mb(key: str) -> Optional[float]:
        v = _num(t.get(key))
        return None if v is None else v * 1000  # the agent's GB are 1e9 bytes, the snapshot's MB 1e6

    loads = [_num(t.get(k)) for k in ("load_1", "load_5", "load_15")]
    hours = _num(t.get("uptime_hours"))
    return {"cpu_pct": t.get("cpu_pct"), "cores": t.get("cores"),
            "load": loads if any(v is not None for v in loads) else None,
            "ram_pct": t.get("ram_pct"), "ram_used_mb": mb("ram_used_gb"), "ram_total_mb": mb("ram_total_gb"),
            "swap_pct": t.get("swap_pct"), "disk_pct": t.get("disk_pct"), "disk_free_gb": t.get("disk_free_gb"),
            "disk_total_gb": t.get("disk_total_gb"), "uptime_sec": hours * 3600 if hours is not None else None,
            "process_count": t.get("process_count"), "hostname": t.get("hostname") or dev.get("hostname")}


def action_ok(res: Any) -> bool:
    return isinstance(res, dict) and res.get("success", True) is not False


def server_action_error(res: Any, fallback: str = "The server didn't answer.", wait: Optional[float] = None) -> str:
    """A failed server action's result as one friendly sentence (`wait`: how long this PC
    waited for it, for the timeout message)."""
    if not isinstance(res, dict):
        return fallback
    code = str(res.get("error") or "")
    if code == "DEVICE_OFFLINE":
        return "The server agent isn't connected to the hub right now."
    if code == "TIMEOUT":
        return f"The server didn't answer within {fmt_wait(wait or 20)} (it may still be working on it)."
    if code in ("OFFLINE", "SEND_FAILED", "DISCONNECTED"):
        return str(res.get("reply") or "This PC isn't connected to the hub.")
    text = res.get("error") or res.get("reply") or res.get("stderr") or fallback
    return " ".join(str(text).split()) or fallback


def fmt_bytes(n: Any) -> str:
    v = _num(n)
    if v is None:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if v < 1024:
            return f"{v:.0f} {unit}" if unit == "B" else f"{v:.1f} {unit}"
        v /= 1024
    return f"{v:.1f} TB"


def server_path_join(folder: Any, name: str) -> str:
    """A child path on the (Linux) server; the top level ("") means the home folder."""
    folder = str(folder or "")
    if folder == "/":
        return "/" + name
    return f"{folder.rstrip('/')}/{name}" if folder else f"~/{name}"


def file_name_error(name: str) -> Optional[str]:
    """Why `name` can't be a file or folder name (None when it's fine)."""
    if name in (".", "..") or "/" in name or "\x00" in name:
        return "A name can't contain “/” or be “.” or “..”."
    return None


def file_row_text(entry: Dict[str, Any], name_w: int, now: Optional[float] = None) -> str:
    """One monospace line of the file browser: name (folders end in /), size, modified."""
    name = str(entry.get("name") or entry.get("path") or "?") + ("/" if entry.get("folder") else "")
    if len(name) > name_w:
        name = name[: max(1, name_w - 1)] + "…"
    size = "" if entry.get("folder") else fmt_bytes(entry.get("size"))
    when = fmt_when(entry.get("modified"), now) if _num(entry.get("modified")) else ""
    return f"{name:<{name_w}}  {size:>9}  {when}"


# Server administration (the agent's admin actions — updates, storage, network, security,
# services, timers, journal, reboot) and the hub's resource history (GET /api/v1/server/history).

# How long this PC waits for a server action's answer. node.send_action gives up after
# client.COMMAND_TIMEOUT_SEC (60 s), too soon for these; the hub gives the agent a little less
# (server/app.py SLOW_ACTIONS), so the hub's own TIMEOUT answer normally arrives first.
SERVER_ACTION_WAIT_SEC = {"sys_updates": 260.0, "apply_updates": 1920.0, "storage": 140.0, "cleanup": 340.0,
                          "security": 95.0, "control": 140.0, "file_to_hub": 320.0, "manage_file": 140.0}
SERVER_GRAPH_RANGES = (("1 h", 1), ("6 h", 6), ("24 h", 24), ("7 d", 168))
SERVER_GRAPH_POINTS = 240
SERVER_GRAPH_REFRESH_SEC = 60.0
SERVER_NETWORK_REFRESH_SEC = 15.0   # the Network section's live rate, while it's on screen
SERVER_REBOOT_DELAY_SEC = 60        # "reboot" schedules `shutdown -r +1`
SERVER_JOURNAL_PRIORITIES = ("any", "emerg", "alert", "crit", "err", "warning", "notice", "info", "debug")
SERVER_JOURNAL_SINCE = ("any time", "15 min ago", "1 hour ago", "6 hours ago", "today", "yesterday", "7 days ago")
SERVER_JOURNAL_LINES = ("100", "200", "500")
SERVER_ALL_UNITS = "All units"
_UNIT_RE = re.compile(r"^[A-Za-z0-9_.@-]+$")
_SINCE_RE = re.compile(r"^[\w :+-]{1,30}$")
_LOG_ERR_RE = re.compile(r"\b(error|err|fail(?:ed|ure)?|crit(?:ical)?|emerg|alert|panic|denied|segfault|fatal)\b", re.I)
_LOG_WARN_RE = re.compile(r"\bwarn(?:ing)?\b", re.I)
CLEANUP_LABELS = {"journal": "System logs (journald)",
                  "docker": "Unused Docker images, stopped containers and build cache",
                  "pm2_logs": "Old pm2 app logs", "dnf_cache": "Package download cache",
                  "trash": "Willy's trash (~/.willy-trash)"}
_CLEANUP_WARNINGS = {
    "journal": "Old system logs are deleted: journald keeps the last 14 days, at most 300 MB.",
    "docker": "Runs `docker system prune`: removes stopped containers, unused networks, dangling images and the "
              "build cache. Volumes are never touched, so your apps' data stays safe.",
    "pm2_logs": "Empties every pm2 app's log files (pm2 flush). The apps keep running.",
    "dnf_cache": "Removes downloaded package files. dnf fetches them again when it needs them.",
    "trash": "Permanently deletes everything in ~/.willy-trash. Files deleted from the Files tab can't be "
             "restored after this.",
}
_BOOT_CRITICAL = ("sshd", "nginx", "docker", "amazon-ssm-agent", "chronyd", "systemd-networkd", "NetworkManager")


def fmt_wait(seconds: Any) -> str:
    """A wait as words: "20 seconds", "4 minutes"."""
    s = _num(seconds) or 0.0
    if s < 120:
        return f"{s:.0f} seconds"
    return f"{s / 60:.0f} minutes"


def server_action_request(node: Any, device_id: str, action: str, payload: Dict[str, Any],
                          wait: Optional[float] = None) -> Any:
    """The coroutine to node.submit() for a server action: node.send_action, or — for the slow
    admin actions (SERVER_ACTION_WAIT_SEC) — the same request with a longer wait. Nodes
    without the request internals (tests' fakes) always get plain send_action."""
    from pc_client import client

    wait = wait if wait is not None else SERVER_ACTION_WAIT_SEC.get(action)
    if wait and wait > client.COMMAND_TIMEOUT_SEC:
        try:
            return node.send_action(device_id, action, payload, timeout=wait)
        except TypeError:  # a node without the timeout argument (tests' fakes)
            pass
    return node.send_action(device_id, action, payload)


def fmt_kbit(kbps: Any) -> str:
    """Network speed in kbit/s as "850 kbit/s", "1.2 Mbit/s"."""
    v = _num(kbps)
    if v is None:
        return "—"
    v = max(0.0, v)
    if v >= 1_000_000:
        return f"{v / 1_000_000:.1f} Gbit/s"
    if v >= 1000:
        return f"{v / 1000:.1f} Mbit/s"
    return f"{v:.1f} kbit/s" if 0 < v < 10 else f"{v:.0f} kbit/s"


def fmt_gb(gb: Any) -> str:
    """The agent's GB (1e9 bytes): "850 MB", "12.3 GB"."""
    v = _num(gb)
    if v is None:
        return "—"
    if v < 1:
        return f"{v * 1000:.0f} MB"
    return f"{v:.1f} GB" if v < 100 else f"{v:.0f} GB"


def history_values(points: Any, key: str) -> List[Tuple[float, Optional[float]]]:
    """(t, value) pairs of one series from the history payload's points, sorted by time
    (points without a time are dropped; a missing or bad value is None)."""
    out = []
    for p in points or []:
        if not isinstance(p, dict):
            continue
        t = _num(p.get("t"))
        if t is not None:
            v = _num(p.get(key))
            out.append((t, max(0.0, v) if v is not None else None))
    out.sort(key=lambda tv: tv[0])
    return out


def history_stats(pairs: Sequence[Tuple[float, Optional[float]]]) -> Optional[Tuple[float, float, float]]:
    """(min, average, max) of a series' values, None when it has none."""
    vals = [v for _t, v in pairs if v is not None]
    if not vals:
        return None
    return min(vals), sum(vals) / len(vals), max(vals)


def history_gap(times: Sequence[float]) -> float:
    """How far apart two samples may be before the line breaks (the server or hub was down):
    three typical steps, at least three minutes."""
    steps = sorted(b - a for a, b in zip(times, times[1:]) if b > a)
    typical = steps[len(steps) // 2] if steps else 60.0
    return max(180.0, typical * 3)


def history_segments(pairs: Sequence[Tuple[float, Optional[float]]], gap: float) -> List[List[Tuple[float, float]]]:
    """Unbroken runs of a series: a missing value or a gap longer than `gap` starts a new one."""
    segments: List[List[Tuple[float, float]]] = []
    run: List[Tuple[float, float]] = []
    for t, v in pairs:
        if v is None or (run and t - run[-1][0] > gap):
            if run:
                segments.append(run)
            run = []
        if v is not None:
            run.append((t, v))
    if run:
        segments.append(run)
    return segments


def nice_ceiling(value: float) -> float:
    """A round axis maximum at or above value (1, 2, 2.5, 5 x 10^n)."""
    if value <= 0:
        return 1.0
    exp = 10 ** math.floor(math.log10(value))
    for m in (1, 2, 2.5, 5, 10):
        if value <= m * exp:
            return m * exp
    return 10 * exp


def history_stats_text(pairs: Sequence[Tuple[float, Optional[float]]], fmt: Callable[[float], str]) -> str:
    stats = history_stats(pairs)
    if stats is None:
        return ""
    lo, avg, hi = stats
    return f"min {fmt(lo)}  ·  avg {fmt(avg)}  ·  max {fmt(hi)}"


def boot_state(boot: Any) -> Optional[bool]:
    """A service's `systemctl list-unit-files` state as the At-boot switch: True (enabled),
    False (disabled), None when it can't be switched (static, indirect, masked, unknown)."""
    s = str(boot or "").lower()
    if s in ("enabled", "enabled-runtime"):
        return True
    if s == "disabled":
        return False
    return None


def filter_services(rows: Any, text: str = "") -> List[Dict[str, Any]]:
    """Services matching the filter (name or description, any case): failed first, then
    running, then the rest, each by name."""
    needle = " ".join(str(text or "").lower().split())
    out = [r for r in rows or [] if isinstance(r, dict) and r.get("name")]
    if needle:
        out = [r for r in out if needle in str(r["name"]).lower() or needle in str(r.get("description") or "").lower()]
    return sorted(out, key=lambda r: (str(r.get("active")) != "failed", str(r.get("active")) != "active",
                                      str(r["name"]).lower()))


def service_line(row: Dict[str, Any], name_w: int) -> str:
    """One monospace line of the services list."""
    name = str(row.get("name") or "?")
    if len(name) > name_w:
        name = name[: max(1, name_w - 1)] + "…"
    state = "/".join(str(x) for x in (row.get("active"), row.get("sub")) if x)
    boot = str(row.get("boot") or "")
    return (f"{name:<{name_w}}  {ellipsize(state, 20):<20}  {ellipsize(boot, 9):<9}  "
            f"{ellipsize(row.get('description'), 70)}")


def ssh_setting(kind: str, value: Any) -> Tuple[str, str]:
    """A security setting as (words, colour): green when it's the safe choice."""
    v = str(value or "unknown").strip().lower()
    if kind == "password":
        return {"yes": ("On", AMBER), "no": ("Off · keys only", GREEN)}.get(v, (v.capitalize(), DIM))
    if kind == "root":
        if v == "no":
            return "Off", GREEN
        if v in ("prohibit-password", "without-password"):
            return "Keys only", GREEN
        if v == "forced-commands-only":
            return "Forced commands only", GREEN
        return ("On", RED) if v == "yes" else (v.capitalize(), DIM)
    if kind == "fail2ban":
        return ("Active", GREEN) if v == "active" else (v.capitalize(), AMBER)
    return v, DIM


def cleanup_warning(what: str) -> str:
    return _CLEANUP_WARNINGS.get(what, f"Cleans up {what} on the server.")


def cleanable_text(value: Any) -> Tuple[str, bool]:
    """A cleanable item's size as (text, worth cleaning): GB numbers, or Docker's text."""
    v = _num(value)
    if v is not None:
        return (fmt_gb(v), True) if v >= 0.005 else ("Nothing to clean", False)
    text = " ".join(str(value or "").split())
    return (ellipsize(text, 90), True) if text else ("Not available", False)


def journal_payload(unit: str, priority: str, since: str, grep: str, lines: Any) -> Tuple[Dict[str, Any],
                                                                                          Optional[str]]:
    """The journal action's payload from the Logs fields, or an error to show instead."""
    payload: Dict[str, Any] = {"lines": int(_num(lines) or 100)}
    unit = str(unit or "").strip()
    if unit and unit != SERVER_ALL_UNITS:
        if not _UNIT_RE.match(unit):
            return {}, "A unit name only has letters, digits and . _ @ - (like nginx or nginx.service)."
        payload["unit"] = unit
    priority = str(priority or "").strip().lower()
    if priority and priority != "any":
        if priority not in SERVER_JOURNAL_PRIORITIES:
            return {}, f"Pick a priority from the list ({', '.join(SERVER_JOURNAL_PRIORITIES[1:])})."
        payload["priority"] = priority
    since = " ".join(str(since or "").split())
    if since and since != "any time":
        if not _SINCE_RE.match(since):
            return {}, 'Write "since" like "1 hour ago", "today" or "2026-10-01 08:00".'
        payload["since"] = since
    grep = str(grep or "").strip()
    if grep:
        payload["grep"] = grep[:80]
    return payload, None


# ----------------------------------------------------------------------------- fonts & icons

class Fonts:
    """Cached CTkFonts plus pixel-sized tuples for plain Tk widgets and canvas text."""

    def __init__(self, root: tk.Misc, scale: float):
        families = set(tkfont.families(root))

        def pick(*names: str) -> str:
            return next((n for n in names if n in families), names[-1])

        self.scale = scale
        self.ui = pick("Segoe UI", "Arial")
        self.semi = pick("Segoe UI Semibold", self.ui)
        self.mono = pick("Cascadia Mono", "Consolas", "Courier New")
        self.icon = next((n for n in ("Segoe Fluent Icons", "Segoe MDL2 Assets") if n in families), "")
        self._cache: Dict[Tuple[str, int, str, str], ctk.CTkFont] = {}

    def __call__(self, size: int, weight: str = "normal", family: Optional[str] = None,
                 slant: str = "roman") -> ctk.CTkFont:
        key = (family or self.ui, size, weight, slant)
        font = self._cache.get(key)
        if font is None:
            font = self._cache[key] = ctk.CTkFont(family=key[0], size=size, weight=weight, slant=slant)
        return font

    def s(self, size: int) -> ctk.CTkFont:
        """Semibold."""
        return self(size, family=self.semi)

    def tk(self, size: float, family: Optional[str] = None, weight: str = "normal") -> tuple:
        return (family or self.ui, -max(1, round(size * self.scale)), weight)

    def icon_tk(self, size: float) -> tuple:
        return (self.icon or self.ui, -max(1, round(size * self.scale)))


class Icons:
    """Renders Segoe Fluent Icons glyphs into crisp CTkImages (cached)."""

    FONT_FILES = ("SegoeIcons.ttf", "segmdl2.ttf")

    def __init__(self, scale: float):
        self.scale = scale
        fonts_dir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        self.path = next((str(fonts_dir / f) for f in self.FONT_FILES if (fonts_dir / f).exists()), None)
        self._fonts: Dict[int, Any] = {}
        self._cache: Dict[Tuple[str, int, str], Optional[ctk.CTkImage]] = {}

    def pil(self, name: str, px: int, color: str) -> Optional[Image.Image]:
        code = ICONS.get(name)
        if not self.path or code is None:
            return None
        try:
            font = self._fonts.get(px)
            if font is None:
                font = self._fonts[px] = ImageFont.truetype(self.path, px)
            img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
            ch = chr(code)
            left, top, right, bottom = font.getbbox(ch)
            x = (px - (right - left)) / 2 - left
            y = (px - (bottom - top)) / 2 - top
            ImageDraw.Draw(img).text((x, y), ch, font=font, fill=color)
            return img
        except Exception:
            return None

    def get(self, name: str, size: int = 16, color: str = SOFT) -> Optional[ctk.CTkImage]:
        key = (name, size, color)
        if key not in self._cache:
            img = self.pil(name, max(8, round(size * self.scale)), color)
            self._cache[key] = ctk.CTkImage(light_image=img, dark_image=img, size=(size, size)) if img else None
        return self._cache[key]


# ----------------------------------------------------------------------------- scroll area

class ThinScrollbar(tk.Canvas):
    """Slim rounded scrollbar speaking Tk's scroll protocol (set / yview command).
    CTkScrollbar runs a full update_idletasks() inside every set(), so each scroll or
    content change forced the whole app to re-layout synchronously (100+ ms)."""

    def __init__(self, master: tk.Misc, command: Callable[..., Any], bg: str, scale: float = 1.0):
        self._px = lambda v: max(1, round(v * scale))
        super().__init__(master, bg=bg, highlightthickness=0, bd=0, width=self._px(12))
        self.command = command
        self.first, self.last = 0.0, 1.0
        self._thick = self._px(6)
        self._thumb = self.create_line(0, 0, 0, 0, fill=BORDER_HI, width=self._thick, capstyle="round")
        self._drag: Optional[Tuple[float, float]] = None
        self.bind("<Configure>", lambda _e: self._redraw())
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._motion)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Enter>", lambda _e: self.itemconfigure(self._thumb, fill=DIM))
        self.bind("<Leave>", lambda _e: self._drag is None and self.itemconfigure(self._thumb, fill=BORDER_HI))

    def set(self, first: Any, last: Any) -> None:
        self.first, self.last = float(first), float(last)
        self._redraw()

    def _track(self) -> Tuple[float, float]:
        pad = self._thick / 2 + self._px(3)
        return pad, max(1.0, self.winfo_height() - 2 * pad)

    def _thumb_span(self) -> Tuple[float, float]:
        pad, span = self._track()
        top, bottom = pad + self.first * span, pad + self.last * span
        shortfall = self._px(22) - (bottom - top)
        if shortfall > 0:  # keep a grabbable thumb on long content
            top = max(pad, top - shortfall * self.first)
            bottom = min(pad + span, top + self._px(22))
        return top, bottom

    def _redraw(self) -> None:
        if self.first <= 0.0 and self.last >= 1.0:  # nothing to scroll
            self.itemconfigure(self._thumb, state="hidden")
            return
        self.itemconfigure(self._thumb, state="normal")
        top, bottom = self._thumb_span()
        x = self.winfo_width() / 2
        self.coords(self._thumb, x, top, x, bottom)

    def _press(self, event: Any) -> None:
        top, bottom = self._thumb_span()
        if top - 4 <= event.y <= bottom + 4:
            self._drag = (event.y, self.first)
        else:
            self.command("scroll", 1 if event.y > bottom else -1, "pages")

    def _motion(self, event: Any) -> None:
        if self._drag is not None:
            y0, first0 = self._drag
            self.command("moveto", first0 + (event.y - y0) / self._track()[1])

    def _release(self, _event: Any) -> None:
        self._drag = None
        self.itemconfigure(self._thumb, fill=BORDER_HI)


class ScrollArea(ctk.CTkFrame):
    """Vertical scroller. With fill_height the body is at least as tall as the viewport (so
    weighted rows grow to fill it) and scrolls once the content is taller. The body keeps its
    natural height, so content that grows later is never clipped. The scrollbar only shows
    when needed; mouse-wheel scrolling is handled once, app-wide."""

    is_scroll_area = True

    def __init__(self, master: tk.Misc, bg: str = BG, fill_height: bool = True):
        super().__init__(master, fg_color=bg, corner_radius=0, border_width=0)
        self.fill_height = fill_height
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0, yscrollincrement=1)
        self.scrollbar = ThinScrollbar(self, self.canvas.yview, bg, float(ctk.ScalingTracker.get_widget_scaling(self)))
        self._holder = tk.Frame(self.canvas, bg=bg, bd=0, highlightthickness=0)
        self._holder.grid_columnconfigure(0, weight=1)
        self._holder.grid_rowconfigure(0, weight=1)
        self.body = tk.Frame(self._holder, bg=bg, bd=0, highlightthickness=0)
        self.body.grid(row=0, column=0, sticky="nsew")
        # Zero-width strut as tall as the viewport: the holder is at least viewport-high while the
        # body's own requested height still propagates (a forced item height would hide growth).
        self._strut = tk.Frame(self._holder, bg=bg, width=0, height=1, bd=0, highlightthickness=0)
        self._strut.grid(row=0, column=1, sticky="ns")
        self._win = self.canvas.create_window(0, 0, window=self._holder, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self._bar = False
        self._region: Optional[Tuple[int, int]] = None
        self.canvas.bind("<Configure>", self._on_canvas, add="+")
        self._holder.bind("<Configure>", self._sync, add="+")

    def _on_canvas(self, event: Any) -> None:
        self.canvas.itemconfigure(self._win, width=event.width)
        if self.fill_height:
            self._strut.configure(height=event.height)
        self._sync()

    def _sync(self, _event: Any = None) -> None:
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        if cw <= 1 or ch <= 1:
            return
        req = self._holder.winfo_reqheight()
        region = (cw, max(req, ch))
        if region != self._region:
            self._region = region
            self.canvas.configure(scrollregion=(0, 0, region[0], region[1]))
        need = req > ch + 1
        if need != self._bar:
            self._bar = need
            if need:
                self.scrollbar.grid(row=0, column=1, sticky="ns", padx=(2, 0), pady=4)
            else:
                self.scrollbar.grid_remove()
                self.canvas.yview_moveto(0)

    def scroll_pixels(self, pixels: int) -> None:
        if self._bar and pixels:
            self.canvas.yview_scroll(pixels, "units")


# ----------------------------------------------------------------------------- gauges & charts

class RingGauge(tk.Canvas):
    """270° ring gauge, antialiased by rendering the ring at 3x with PIL; value changes
    ease in over a few frames. Canvas items are created once and only reconfigured."""

    SS = 3

    def __init__(self, master: tk.Misc, app: "WillyDesktopApp", title: str, icon: str):
        super().__init__(master, bg=CARD, highlightthickness=0, bd=0, height=app.px(158))
        self.app = app
        f = app.fonts
        self._shown = 0.0
        self._target = 0.0
        self._has_value = False
        self._color = CYAN
        self._size = 0
        self._t = 0.0
        self._rc = 0.0
        self._c = 0.0
        self._bbox: Tuple[float, float, float, float] = (0, 0, 0, 0)
        self._base: Optional[Image.Image] = None
        self._photo: Optional[ImageTk.PhotoImage] = None
        self._anim: Optional[str] = None
        self._texts: Dict[int, str] = {}
        self._icon_id = self.create_text(0, 0, anchor="w", text=app.glyph(icon), font=f.icon_tk(13), fill=DIM)
        self._title_id = self.create_text(0, 0, anchor="w", text=title.upper(), font=f.tk(11, f.semi), fill=MUTED)
        self._img_id = self.create_image(0, 0)
        self._value_id = self.create_text(0, 0, text="—", font=f.tk(24, f.semi), fill=TEXT)
        self._sub_id = self.create_text(0, 0, text="", font=f.tk(11), fill=MUTED)
        self._foot_id = self.create_text(0, 0, text="", font=f.tk(11), fill=DIM)
        self.bind("<Configure>", self._layout)

    def _layout(self, _event: Any = None) -> None:
        w, h = self.winfo_width(), self.winfo_height()
        if w < 30 or h < 30:
            return
        px = self.app.px
        pad, head, foot = px(6), px(24), px(20)
        size = int(max(px(56), min(w - 2 * pad, h - head - foot - px(2))))
        cx = w / 2
        cy = head + size / 2
        self.coords(self._icon_id, pad, px(12))
        self.coords(self._title_id, pad + px(20), px(12))
        self.coords(self._img_id, cx, cy)
        self.coords(self._value_id, cx, cy - px(2))
        self.coords(self._sub_id, cx, cy + size * 0.27)
        self.coords(self._foot_id, cx, min(h - px(9), cy + size / 2 + px(6)))
        if size != self._size:
            self._size = size
            self._make_base()
        self._render()

    def _make_base(self) -> None:
        S, size = self.SS, self._size
        big = size * S
        self._t = max(6, round(size * 0.085)) * S
        self._c = big / 2
        self._rc = big / 2 - self._t / 2 - S
        outer = self._rc + self._t / 2
        self._bbox = (self._c - outer, self._c - outer, self._c + outer, self._c + outer)
        img = Image.new("RGB", (big, big), _rgb(CARD))
        self._arc(ImageDraw.Draw(img), 135, 405, _rgb(BORDER))
        self._base = img
        self._photo = ImageTk.PhotoImage(img.reduce(S))
        self.itemconfigure(self._img_id, image=self._photo)

    def _arc(self, draw: ImageDraw.ImageDraw, start: float, end: float, fill: Tuple[int, int, int]) -> None:
        # PIL draws the stroke inward from the bbox edge; caps sit on the stroke's centre line.
        draw.arc(self._bbox, start=start, end=end, fill=fill, width=int(self._t))
        r = self._t / 2
        for ang in (start, end):
            a = math.radians(ang)
            x, y = self._c + self._rc * math.cos(a), self._c + self._rc * math.sin(a)
            draw.ellipse((x - r, y - r, x + r, y + r), fill=fill)

    def _render(self) -> None:
        if not self._size or self._base is None or self._photo is None:
            return
        img = self._base
        if self._has_value and self._shown >= 0.5:
            img = img.copy()
            self._arc(ImageDraw.Draw(img), 135, 135 + 270 * min(100.0, self._shown) / 100.0, _rgb(self._color))
        self._photo.paste(img.reduce(self.SS))

    def _text(self, item: int, value: str) -> None:
        if self._texts.get(item) != value:
            self._texts[item] = value
            self.itemconfigure(item, text=value)

    def set(self, pct: Optional[float], color: str, value: str, sub: str = "", foot: str = "",
            animate: bool = False) -> None:
        self._text(self._value_id, value)
        self._text(self._sub_id, sub)
        self._text(self._foot_id, foot)
        has = pct is not None
        target = max(0.0, min(100.0, float(pct))) if has else 0.0
        changed = color != self._color or has != self._has_value
        self._color, self._has_value, self._target = color, has, target
        if animate and self._size and abs(target - self._shown) >= 0.8:
            if self._anim is None:
                self._step()
            return
        if self._anim is not None:
            self.after_cancel(self._anim)
            self._anim = None
        if changed or abs(target - self._shown) > 0.01:
            self._shown = target
            self._render()

    def _step(self) -> None:
        self._anim = None
        diff = self._target - self._shown
        self._shown = self._target if abs(diff) < 0.8 else self._shown + diff * 0.42  # ease-out, ~5-7 frames
        self._render()
        if self._shown != self._target:
            self._anim = self.after(33, self._step)


class LineChart(tk.Canvas):
    """History line chart rendered with PIL at 2x (antialiased lines, gradient fills).
    Axis labels stay Tk text for crisp ClearType rendering."""

    SS = 2

    def __init__(self, master: tk.Misc, app: "WillyDesktopApp", series: Sequence[Tuple[str, str]], *,
                 height: int = 150, y_max: Optional[float] = 100.0, axes: bool = True,
                 y_fmt: Callable[[float], str] = lambda v: f"{v:.0f}%", floor: float = 8.0,
                 fill_alpha: float = 0.30, bg: str = CARD):
        super().__init__(master, bg=bg, highlightthickness=0, bd=0, height=app.px(height))
        self.app = app
        self.series = list(series)
        self.data: Dict[str, deque] = {key: deque(maxlen=HISTORY_POINTS) for key, _ in self.series}
        self.y_max = y_max
        self.axes = axes
        self.y_fmt = y_fmt
        self.floor = floor
        self.fill_alpha = fill_alpha
        self.bg = bg
        self._photo: Optional[ImageTk.PhotoImage] = None
        self._photo_size: Optional[Tuple[int, int]] = None
        self._grad: Optional[Image.Image] = None
        self._img_id = self.create_image(0, 0, anchor="nw")
        f = app.fonts
        self._ylabels = [self.create_text(0, 0, anchor="e", text="", font=f.tk(10), fill=DIM)
                         for _ in range(3)] if axes else []
        span = (HISTORY_POINTS - 1) * HEARTBEAT_INTERVAL_SEC
        span_txt = f"{span / 60:.0f} min ago" if span >= 90 else f"{span:.0f}s ago"
        self._xlabels = [self.create_text(0, 0, anchor=a, text=t, font=f.tk(10), fill=DIM)
                         for a, t in (("w", span_txt), ("e", "now"))] if axes else []
        self.bind("<Configure>", lambda _e: self.render())

    def push(self, values: Dict[str, Any]) -> None:
        for key, _ in self.series:
            self.data[key].append(_num(values.get(key)))

    def seed(self, key: str, values: Sequence[Any]) -> None:
        vals = [_num(v) for v in values][-HISTORY_POINTS:]
        dq = self.data.get(key)
        if dq is not None and len(vals) > len(dq):
            dq.clear()
            dq.extend(vals)

    def _scale_max(self) -> float:
        if self.y_max:
            return self.y_max
        peak = max((v for dq in self.data.values() for v in dq if v is not None), default=0.0)
        return max(self.floor, peak * 1.25)

    def render(self) -> None:
        w, h = self.winfo_width(), self.winfo_height()
        px = self.app.px
        left = px(38) if self.axes else 0
        bottom = px(18) if self.axes else 0
        top, right = px(8), px(4)
        pw, ph = w - left - right, h - top - bottom
        if pw < 24 or ph < 16:
            return
        S = self.SS
        W, H = pw * S, ph * S
        ymax = self._scale_max()
        img = Image.new("RGB", (W, H), _rgb(self.bg))
        draw = ImageDraw.Draw(img)
        for i in range(0, 4):
            y = round((H - S) * i / 4)
            draw.line([(0, y), (W, y)], fill=_rgb(GRID), width=S)
        draw.line([(0, H - S), (W, H - S)], fill=_rgb(BORDER), width=S)
        if self._grad is None or self._grad.size != (W, H):
            ramp = ImageOps.invert(Image.linear_gradient("L").resize((W, H)))
            self._grad = ramp.point(lambda v: int(v * self.fill_alpha))
        lw = max(S, round(2 * S * self.app.scale))
        dot = lw * 1.5
        usable_w = W - dot - S
        step = usable_w / max(1, HISTORY_POINTS - 1)
        pad_y = lw
        for key, color in reversed(self.series):
            vals = list(self.data[key])
            n = len(vals)
            segments: List[List[Tuple[float, float]]] = []
            pts: List[Tuple[float, float]] = []
            for i, v in enumerate(vals):
                if v is None:
                    if pts:
                        segments.append(pts)
                        pts = []
                    continue
                x = usable_w - (n - 1 - i) * step
                y = H - pad_y - (min(max(v, 0.0), ymax) / ymax) * (H - 2 * pad_y)
                pts.append((x, y))
            if pts:
                segments.append(pts)
            rgb = _rgb(color)
            for seg in segments:
                if len(seg) < 2:
                    continue
                mask = Image.new("L", (W, H), 0)
                ImageDraw.Draw(mask).polygon(seg + [(seg[-1][0], H), (seg[0][0], H)], fill=255)
                img.paste(rgb, (0, 0, W, H), ImageChops.multiply(mask, self._grad))
                draw.line(seg, fill=rgb, width=lw, joint="curve")
            if segments and vals and vals[-1] is not None:
                x, y = segments[-1][-1]
                draw.ellipse((x - dot, y - dot, x + dot, y + dot), fill=rgb)
        small = img.reduce(S)
        if self._photo is None or self._photo_size != small.size:
            self._photo = ImageTk.PhotoImage(small)
            self._photo_size = small.size
            self.itemconfigure(self._img_id, image=self._photo)
        else:
            self._photo.paste(small)
        self.coords(self._img_id, left, top)
        if self.axes:
            for item, frac in zip(self._ylabels, (0.0, 0.5, 1.0)):
                self.itemconfigure(item, text=self.y_fmt(ymax * (1 - frac)))
                self.coords(item, left - px(6), top + frac * (ph - 1))
            self.coords(self._xlabels[0], left, h - px(8))
            self.coords(self._xlabels[1], w - right, h - px(8))


# ----------------------------------------------------------------------------- toasts

class _ToastWindow(ctk.CTkToplevel):
    """Borderless pop-up. Skips CTk's title-bar recolouring, which would withdraw and
    update() the window (re-entrant event processing) every time one is created."""

    _deactivate_windows_window_header_manipulation = True


class Toast:
    KINDS = {
        "info": (CYAN, "bolt"),
        "success": (GREEN, "check"),
        "error": (RED, "error"),
        "reminder": (AMBER, "bell"),
        "phone": (SOFT_PURPLE, "phone"),
    }
    WIDTH = 344

    def __init__(self, manager: "ToastManager"):
        self.manager = manager
        app = self.app = manager.app
        F = app.fonts
        self.win = _ToastWindow(app, fg_color=TOAST_KEY)
        self.win.withdraw()
        self.win.overrideredirect(True)
        for attr, value in (("-transparentcolor", TOAST_KEY), ("-topmost", True), ("-alpha", 0.0)):
            try:
                self.win.attributes(attr, value)
            except tk.TclError:
                pass
        self.card = ctk.CTkFrame(self.win, fg_color=SURFACE, corner_radius=14, border_width=1,
                                 border_color=BORDER_HI, bg_color=TOAST_KEY)
        self.card.pack(fill="both", expand=True)
        self.card.grid_columnconfigure(1, weight=1)
        self.icon = ctk.CTkLabel(self.card, text="", width=34, height=34, corner_radius=10)
        self.icon.grid(row=0, column=0, rowspan=3, sticky="n", padx=(14, 10), pady=(14, 14))
        self.title = ctk.CTkLabel(self.card, text="", font=F.s(13), text_color=TEXT, anchor="w",
                                  justify="left", height=20, wraplength=self.WIDTH - 100)
        self.title.grid(row=0, column=1, sticky="ew", pady=(13, 0))
        self.close = ctk.CTkLabel(self.card, text="", image=app.icons.get("close", 10, DIM), width=22,
                                  height=22, cursor="hand2")
        self.close.grid(row=0, column=2, sticky="ne", padx=(2, 10), pady=(10, 0))
        self.msg = ctk.CTkLabel(self.card, text="", font=F(12), text_color=SOFT, anchor="w", justify="left",
                                wraplength=self.WIDTH - 76, height=18)
        self.msg.grid(row=1, column=1, columnspan=2, sticky="ew", padx=(0, 14), pady=(1, 0))
        self.query = ctk.CTkLabel(self.card, text="", font=F(11, slant="italic"), text_color=DIM, anchor="w",
                                  justify="left", height=16, wraplength=self.WIDTH - 76)
        self.query.grid(row=2, column=1, columnspan=2, sticky="ew", padx=(0, 14), pady=(2, 0))
        ctk.CTkFrame(self.card, fg_color="transparent", height=12, width=10).grid(row=3, column=0, columnspan=3)
        self.visible = False
        self.height = 0
        self.alpha = 0.0
        self._dismiss_job: Optional[str] = None
        self._fade_job: Optional[str] = None
        self._styled = False
        self.duration = 4.5
        for widget in (self.card, self.icon, self.title, self.close, self.msg, self.query):
            widget.bind("<Button-1>", lambda _e: self.dismiss(), add="+")
            widget.bind("<Enter>", lambda _e: self._hover(True), add="+")
            widget.bind("<Leave>", lambda _e: self._hover(False), add="+")

    def configure(self, title: str, message: str, query: Optional[str], kind: str) -> None:
        accent, icon = self.KINDS.get(kind, self.KINDS["info"])
        self.icon.configure(image=self.app.icons.get(icon, 17, accent), fg_color=tint(accent, 0.16, SURFACE))
        self.card.configure(border_color=mix(BORDER_HI, accent, 0.35))
        self.title.configure(text=ellipsize(title, 60))
        self.msg.configure(text=ellipsize(message, 220))
        if query:
            self.query.configure(text=f"“{ellipsize(query, 80)}”")
            self.query.grid()
        else:
            self.query.grid_remove()
        self.win.update_idletasks()
        self.height = self.card.winfo_reqheight()

    def show(self, x: int, y: int, width: int, duration: float) -> None:
        self.duration = duration
        self.move(x, y, width)
        if not self.visible:
            self.visible = True
            self.alpha = 0.0
            self.win.deiconify()
            self._no_activate()
            self._fade(0.97, 0.19)
        self._schedule_dismiss(duration)

    def move(self, x: int, y: int, width: int) -> None:
        self.win.wm_geometry(f"{width}x{self.height}+{x}+{y}")

    def _no_activate(self) -> None:
        """Clicking or showing a toast must never steal keyboard focus."""
        if self._styled or sys.platform != "win32":
            return
        self._styled = True
        try:
            self.win.update_idletasks()
            user32 = ctypes.windll.user32
            hwnd = user32.GetParent(self.win.winfo_id())
            name = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(hwnd, name, 64)
            if name.value == "TkTopLevel":  # only ever touch our own wrapper window
                style = user32.GetWindowLongW(hwnd, -20)
                user32.SetWindowLongW(hwnd, -20, style | 0x08000000 | 0x00000080)  # NOACTIVATE | TOOLWINDOW
        except Exception:
            pass

    def _schedule_dismiss(self, seconds: float) -> None:
        if self._dismiss_job:
            self.win.after_cancel(self._dismiss_job)
        self._dismiss_job = self.win.after(int(seconds * 1000), self.dismiss)

    def _hover(self, inside: bool) -> None:
        if not self.visible:
            return
        if inside:
            if self._dismiss_job:
                self.win.after_cancel(self._dismiss_job)
                self._dismiss_job = None
        else:
            self.win.after(80, self._check_leave)

    def _check_leave(self) -> None:
        if not self.visible:
            return
        try:
            x, y = self.win.winfo_pointerxy()
            under = self.win.winfo_containing(x, y)
        except (tk.TclError, KeyError):
            under = None
        if under is None or under.winfo_toplevel() is not self.win:
            self._schedule_dismiss(2.0)

    def _fade(self, target: float, step: float, then: Optional[Callable[[], None]] = None) -> None:
        if self._fade_job:
            self.win.after_cancel(self._fade_job)
            self._fade_job = None
        if abs(target - self.alpha) <= step:
            self.alpha = target
        else:
            self.alpha += step if target > self.alpha else -step
        try:
            self.win.attributes("-alpha", self.alpha)
        except tk.TclError:
            return
        if self.alpha == target:
            if then:
                then()
            return
        self._fade_job = self.win.after(16, lambda: self._fade(target, step, then))

    def dismiss(self, animate: bool = True) -> None:
        if not self.visible:
            return
        self.visible = False
        if self._dismiss_job:
            self.win.after_cancel(self._dismiss_job)
            self._dismiss_job = None
        if animate:
            self._fade(0.0, 0.2, self._hidden)
        else:
            self._hidden()

    def _hidden(self) -> None:
        if self._fade_job:
            self.win.after_cancel(self._fade_job)
            self._fade_job = None
        try:
            self.win.attributes("-alpha", 0.0)
            self.win.withdraw()
        except tk.TclError:
            pass
        self.manager.released(self)


class ToastManager:
    """Pool of reusable pop-ups stacked bottom-right on the app's monitor.
    Windows are reused because every new CTkToplevel adds a permanent bind_all()."""

    MAX_VISIBLE = 4

    def __init__(self, app: "WillyDesktopApp"):
        self.app = app
        self.free: List[Toast] = []
        self.shown: List[Toast] = []

    def show(self, title: str, message: str, query: Optional[str] = None, kind: str = "info",
             duration: float = 4.5) -> None:
        if self.app.closing or not (title or message):
            return
        while len(self.shown) >= self.MAX_VISIBLE:
            oldest = self.shown.pop(0)
            oldest.dismiss(animate=False)
        toast = self.free.pop() if self.free else Toast(self)
        toast.configure(title, message, query, kind)
        self.shown.append(toast)
        self.restack(duration_for=toast, duration=duration)

    def released(self, toast: Toast) -> None:
        if toast in self.shown:
            self.shown.remove(toast)
        if toast not in self.free:
            self.free.append(toast)
        self.restack()

    def _work_area(self) -> Tuple[int, int, int, int]:
        try:
            import win32api
            hwnd = ctypes.windll.user32.GetParent(self.app.winfo_id())
            monitor = win32api.MonitorFromWindow(hwnd, 2)
            return tuple(win32api.GetMonitorInfo(monitor)["Work"])  # type: ignore[return-value]
        except Exception:
            return 0, 0, self.app.winfo_screenwidth(), self.app.winfo_screenheight() - 48

    def restack(self, duration_for: Optional[Toast] = None, duration: float = 4.5) -> None:
        if not self.shown:
            return
        left, top, right, bottom = self._work_area()
        width = self.app.px(Toast.WIDTH)
        margin, gap = self.app.px(16), self.app.px(10)
        x = right - width - margin
        y = bottom - margin
        for toast in reversed(self.shown):  # newest at the bottom
            y -= toast.height
            if toast is duration_for:
                toast.show(x, y, width, duration)
            elif toast.visible:
                toast.move(x, y, width)
            y -= gap

    def close_all(self) -> None:
        for toast in self.shown + self.free:
            try:
                toast.win.destroy()
            except Exception:
                pass
        self.shown, self.free = [], []


# ----------------------------------------------------------------------------- dialogs

class _Dialog(ctk.CTkToplevel):
    """Reusable modal dialog (created once, then shown/hidden)."""

    def __init__(self, app: "WillyDesktopApp", title: str):
        super().__init__(app, fg_color=CARD)
        self.withdraw()
        self._iconbitmap_method_called = True  # keep the Willy icon instead of CTk's default
        self.app = app
        self.title(title)
        self.transient(app)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda _e: self.close())

    def open(self) -> None:
        self.update_idletasks()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        x = self.app.winfo_rootx() + (self.app.winfo_width() - w) // 2
        y = self.app.winfo_rooty() + max(40, (self.app.winfo_height() - h) // 3)
        self.wm_geometry(f"+{max(0, x)}+{max(0, y)}")
        self.deiconify()
        style_titlebar(self)
        self.lift()
        self.after(60, self._grab)

    def _grab(self) -> None:
        try:
            self.grab_set()
            self.focus_force()
            self.focus_first()
        except tk.TclError:
            pass

    def focus_first(self) -> None:
        pass

    def close(self) -> None:
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.withdraw()
        try:
            self.app.focus_force()
        except tk.TclError:
            pass


class ConfirmDialog(_Dialog):
    def __init__(self, app: "WillyDesktopApp"):
        super().__init__(app, "Confirm")
        F = app.fonts
        self._callback: Optional[Callable[[], None]] = None
        body = ctk.CTkFrame(self, fg_color=CARD, corner_radius=0)
        body.pack(fill="both", expand=True, padx=22, pady=20)
        body.grid_columnconfigure(1, weight=1)
        self.icon = ctk.CTkLabel(body, text="", width=40, height=40, corner_radius=12)
        self.icon.grid(row=0, column=0, rowspan=2, sticky="n", padx=(0, 14))
        self.heading = ctk.CTkLabel(body, text="", font=F.s(16), text_color=TEXT, anchor="w")
        self.heading.grid(row=0, column=1, sticky="w")
        self.message = ctk.CTkLabel(body, text="", font=F(12), text_color=MUTED, anchor="w", justify="left",
                                    wraplength=340)
        self.message.grid(row=1, column=1, sticky="w", pady=(2, 0))
        buttons = ctk.CTkFrame(body, fg_color="transparent")
        buttons.grid(row=2, column=0, columnspan=2, sticky="e", pady=(20, 0))
        app.button(buttons, "Cancel", self.close, kind="secondary", width=92).pack(side="left", padx=(0, 8))
        self.ok = app.button(buttons, "OK", self._confirm, kind="danger", width=110)
        self.ok.pack(side="left")
        self.bind("<Return>", lambda _e: self._confirm())

    def ask(self, heading: str, message: str, confirm_text: str, on_confirm: Callable[[], None],
            danger: bool = True) -> None:
        accent = RED if danger else CYAN
        self.icon.configure(image=self.app.icons.get("error" if danger else "info", 20, accent),
                            fg_color=tint(accent, 0.16))
        self.heading.configure(text=heading)
        self.message.configure(text=message)
        self.ok.configure(text=confirm_text, **self.app.button_style("danger" if danger else "primary"))
        self._callback = on_confirm
        self.open()

    def focus_first(self) -> None:
        self.ok.focus_set()

    def _confirm(self) -> None:
        callback, self._callback = self._callback, None
        self.close()
        if callback:
            callback()


class QuickDropDialog(_Dialog):
    """Send a link or a note to a phone (quickdrop)."""

    def __init__(self, app: "WillyDesktopApp"):
        super().__init__(app, "Send to phone")
        F = app.fonts
        self._callback: Optional[Callable[[Dict[str, Any]], None]] = None
        body = ctk.CTkFrame(self, fg_color=CARD, corner_radius=0)
        body.pack(fill="both", expand=True, padx=22, pady=20)
        self.heading = ctk.CTkLabel(body, text="Send to phone", font=F.s(16), text_color=TEXT, anchor="w")
        self.heading.pack(anchor="w")
        ctk.CTkLabel(body, text="Paste a link to open it on the phone, or type a note to show there.",
                     font=F(12), text_color=MUTED, anchor="w").pack(anchor="w", pady=(2, 10))
        # Field labels instead of CTk placeholders (their state desyncs in a reused dialog).
        ctk.CTkLabel(body, text="Link or note", font=F(11), text_color=DIM, anchor="w", height=16).pack(anchor="w")
        self.content = ctk.CTkEntry(body, width=380, height=38, fg_color=SURFACE, border_color=BORDER, font=F(13))
        self.content.pack(fill="x", pady=(2, 0))
        ctk.CTkLabel(body, text="Title (optional)", font=F(11), text_color=DIM, anchor="w", height=16).pack(
            anchor="w", pady=(8, 0))
        self.label = ctk.CTkEntry(body, width=380, height=34, fg_color=SURFACE, border_color=BORDER, font=F(12))
        self.label.pack(fill="x", pady=(2, 0))
        self.error = ctk.CTkLabel(body, text="", font=F(11), text_color=SOFT_RED, anchor="w", height=16)
        self.error.pack(anchor="w", pady=(6, 0))
        buttons = ctk.CTkFrame(body, fg_color="transparent")
        buttons.pack(anchor="e", pady=(8, 0))
        app.button(buttons, "Cancel", self.close, kind="secondary", width=92).pack(side="left", padx=(0, 8))
        app.button(buttons, "Send", self._send, kind="primary", icon="send", width=100).pack(side="left")
        self.content.bind("<Return>", lambda _e: self._send())
        self.label.bind("<Return>", lambda _e: self._send())

    def ask(self, device_name: str, on_send: Callable[[Dict[str, Any]], None]) -> None:
        self.heading.configure(text=f"Send to {device_name}")
        self.content.delete(0, "end")
        self.label.delete(0, "end")
        self.error.configure(text="")
        self._callback = on_send
        self.open()

    def focus_first(self) -> None:
        self.content.focus_set()

    def _send(self) -> None:
        raw = self.content.get().strip()
        if not raw:
            self.error.configure(text="Type a link or a note first.")
            return
        title = self.label.get().strip() or "From Willy PC"
        if looks_like_url(raw):
            payload = {"url": as_url(raw), "title": title}
        else:
            payload = {"text": raw, "title": title}
        callback, self._callback = self._callback, None
        self.close()
        if callback:
            callback(payload)


class ServerTextDialog(_Dialog):
    """Text from the server (an app's log, a file) in a scrollable monospace view with Refresh
    and Copy. Not modal (the app stays usable while it's open); created once and reused.
    `load(done)` fetches the text off the Tk thread and calls done(text, error) on it."""

    def __init__(self, app: "WillyDesktopApp"):
        super().__init__(app, "Server")
        F = app.fonts
        self.resizable(True, True)
        self.name = ""
        self._load: Optional[Callable[[Callable[..., None]], None]] = None
        self._seq = 0
        self._value = ""
        body = ctk.CTkFrame(self, fg_color=CARD, corner_radius=0)
        body.pack(fill="both", expand=True, padx=18, pady=16)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(1, weight=1)
        head = tk.Frame(body, bg=CARD)
        head.grid(row=0, column=0, sticky="ew", pady=(0, app.px(10)))
        self.heading = ctk.CTkLabel(head, text="", font=F.s(16), text_color=TEXT, anchor="w")
        self.heading.pack(side="left")
        self.status = ctk.CTkLabel(head, text="", font=F(11), text_color=DIM, anchor="w")
        self.status.pack(side="left", padx=(12, 0))
        app.button(head, "Close", self.close, kind="ghost", height=30, width=70).pack(side="right")
        app.button(head, "Copy", self.copy, icon="copy", height=30).pack(side="right", padx=(0, 8))
        self.refresh_btn = app.button(head, "Refresh", self.refresh, icon="refresh", height=30)
        self.refresh_btn.pack(side="right", padx=(0, 8))
        box = ctk.CTkFrame(body, fg_color=SURFACE, corner_radius=12, border_width=1, border_color=BORDER)
        box.grid(row=1, column=0, sticky="nsew")
        box.grid_rowconfigure(0, weight=1)
        box.grid_columnconfigure(0, weight=1)
        self.text = tk.Text(box, bg=SURFACE, fg=SOFT, font=F.tk(11, F.mono), relief="flat", bd=0,
                            highlightthickness=0, wrap="word", width=104, height=28, padx=app.px(8),
                            pady=app.px(6), insertbackground=TEXT, selectbackground=tint(CYAN, 0.30, SURFACE),
                            selectforeground=TEXT, inactiveselectbackground=tint(CYAN, 0.22, SURFACE))
        self.text.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=8)
        bar = ThinScrollbar(box, self.text.yview, SURFACE, app.scale)
        bar.grid(row=0, column=1, sticky="ns", padx=(2, 6), pady=12)
        self.text.configure(yscrollcommand=bar.set)
        self.text.tag_configure("err", foreground=SOFT_RED)
        self.text.tag_configure("note", foreground=MUTED)
        self.text.configure(state="disabled")

    def _grab(self) -> None:  # not modal
        try:
            self.focus_force()
        except tk.TclError:
            pass

    def show(self, name: str, load: Callable[[Callable[..., None]], None]) -> None:
        self.name = name
        self._load = load
        self.title(name)
        self.heading.configure(text=name)
        self._set_text("", note="Loading…")
        if self.winfo_viewable():
            self.lift()
            self._grab()
        else:
            self.open()
        self.refresh()

    def refresh(self) -> None:
        if self._load is None:
            return
        self._seq += 1
        seq = self._seq
        self.refresh_btn.configure(state="disabled")
        self.status.configure(text="Loading…", text_color=DIM)
        self._load(lambda text, error=None: self._done(seq, text, error))

    def _done(self, seq: int, text: Optional[str], error: Optional[str]) -> None:
        if seq != self._seq:
            return  # something else was asked for meanwhile
        self.refresh_btn.configure(state="normal")
        if error:
            self.status.configure(text=ellipsize(error, 70), text_color=SOFT_RED)
            if not self._value:
                self._set_text("", note=error)
            return
        text = str(text or "").rstrip()
        self._set_text(text, note="" if text else "Nothing to show — it's empty.")
        self.status.configure(text=f"Updated {time.strftime('%H:%M:%S')}", text_color=DIM)

    def _set_text(self, text: str, note: str = "") -> None:
        self._value = text
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        if note and not text:
            self.text.insert("end", note, "note")
        for line in text.splitlines():
            lowered = line.lower()
            self.text.insert("end", line + "\n", "err" if ("error" in lowered or "exception" in lowered
                                                           or "traceback" in lowered) else ())
        self.text.configure(state="disabled")
        self.text.see("end")

    def copy(self) -> None:
        if self._value:
            self.app.copy_text(self._value)


class PromptDialog(_Dialog):
    """Asks for one line of text (a new name, a destination folder)."""

    def __init__(self, app: "WillyDesktopApp"):
        super().__init__(app, "Willy")
        F = app.fonts
        self._callback: Optional[Callable[[str], None]] = None
        self._validate: Optional[Callable[[str], Optional[str]]] = None
        body = ctk.CTkFrame(self, fg_color=CARD, corner_radius=0)
        body.pack(fill="both", expand=True, padx=22, pady=20)
        self.heading = ctk.CTkLabel(body, text="", font=F.s(16), text_color=TEXT, anchor="w")
        self.heading.pack(anchor="w")
        self.message = ctk.CTkLabel(body, text="", font=F(12), text_color=MUTED, anchor="w", justify="left",
                                    wraplength=380)
        self.message.pack(anchor="w", pady=(2, 10))
        self.entry = ctk.CTkEntry(body, width=400, height=36, fg_color=SURFACE, border_color=BORDER, font=F(13))
        self.entry.pack(fill="x")
        self.error = ctk.CTkLabel(body, text="", font=F(11), text_color=SOFT_RED, anchor="w", height=16)
        self.error.pack(anchor="w", pady=(6, 0))
        buttons = ctk.CTkFrame(body, fg_color="transparent")
        buttons.pack(anchor="e", pady=(8, 0))
        app.button(buttons, "Cancel", self.close, kind="secondary", width=92).pack(side="left", padx=(0, 8))
        self.ok = app.button(buttons, "OK", self._ok, kind="primary", width=100)
        self.ok.pack(side="left")
        self.entry.bind("<Return>", lambda _e: self._ok())

    def ask(self, heading: str, message: str, value: str, ok_text: str, on_ok: Callable[[str], None],
            validate: Optional[Callable[[str], Optional[str]]] = None) -> None:
        self.heading.configure(text=heading)
        self.message.configure(text=message)
        self.ok.configure(text=ok_text)
        self.entry.delete(0, "end")
        self.entry.insert(0, value)
        self.error.configure(text="")
        self._callback, self._validate = on_ok, validate
        self.open()

    def focus_first(self) -> None:
        self.entry.focus_set()
        self.entry.select_range(0, "end")

    def _ok(self) -> None:
        raw = self.entry.get().strip()
        problem = "Type something first." if not raw else self._validate(raw) if self._validate else None
        if problem:
            self.error.configure(text=problem)
            return
        callback, self._callback = self._callback, None
        self.close()
        if callback:
            callback(raw)


# ----------------------------------------------------------------------------- pages

class BasePage(tk.Frame):
    def __init__(self, master: tk.Misc, app: "WillyDesktopApp"):
        super().__init__(master, bg=BG, bd=0, highlightthickness=0)
        self.app = app
        self.visible = False
        self.built = False

    def ensure_built(self) -> None:
        if not self.built:
            self.built = True
            self.build()

    def build(self) -> None:
        pass

    def on_show(self) -> None:
        pass

    def on_hide(self) -> None:
        pass


class OverviewPage(BasePage):
    def build(self) -> None:
        app, F = self.app, self.app.fonts
        self._t: Dict[str, Any] = {}
        self._labels: Dict[str, str] = {}
        self._vol_job: Optional[str] = None
        self._vol_user_ts = 0.0
        self._vol_pending = False
        self._vol_level: Optional[int] = None
        self._muted: Optional[bool] = None
        self._bri_job: Optional[str] = None
        self._bri_level: Optional[int] = None
        self._bri_user_ts = 0.0

        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self.scroll = ScrollArea(self, bg=BG, fill_height=True)
        self.scroll.grid(row=0, column=0, sticky="nsew")
        body = tk.Frame(self.scroll.body, bg=BG)
        body.pack(fill="both", expand=True, padx=12, pady=(4, 10))
        for c in range(4):
            body.grid_columnconfigure(c, weight=1, uniform="overview")
        body.grid_rowconfigure(2, weight=1)

        head, right, _ = app.page_header(body, "Overview", "Live telemetry from this PC · updates every "
                                         f"{HEARTBEAT_INTERVAL_SEC:g} s")
        head.grid(row=0, column=0, columnspan=4, sticky="ew", padx=6, pady=(8, 6))
        self.updated = ctk.CTkLabel(right, text="Waiting for telemetry…", font=F(11), text_color=DIM)
        self.updated.pack(side="right")

        # --- gauges
        self.gauges: Dict[str, RingGauge] = {}
        for i, (key, title, icon) in enumerate((("cpu", "CPU", "chip"), ("ram", "Memory", "memory"),
                                                ("disk", "Disk", "disk"), ("battery", "Battery", "battery"))):
            tile = app.card(body)
            tile.grid(row=1, column=i, sticky="nsew", padx=6, pady=6)
            gauge = RingGauge(tile, app, title, icon)
            gauge.pack(fill="both", expand=True, padx=8, pady=8)
            self.gauges[key] = gauge

        # --- performance history
        perf = app.card(body)
        perf.grid(row=2, column=0, columnspan=3, sticky="nsew", padx=6, pady=6)
        slot = app.card_header(perf, "Performance", "pulse", "last 2 minutes")
        self.leg_ram = app.legend(slot, PURPLE, "RAM —")
        self.leg_ram.pack(side="right", padx=(12, 0))
        self.leg_cpu = app.legend(slot, CYAN, "CPU —")
        self.leg_cpu.pack(side="right")
        self.chart = LineChart(perf, app, (("cpu", CYAN), ("ram", PURPLE)), height=150)
        self.chart.pack(fill="both", expand=True, padx=(10, 14), pady=(2, 12))

        # --- network
        net = app.card(body)
        net.grid(row=2, column=3, sticky="nsew", padx=6, pady=6)
        app.card_header(net, "Network", "updown")
        rates = tk.Frame(net, bg=CARD)
        rates.pack(fill="x", padx=16, pady=(0, 2))
        rates.grid_columnconfigure((0, 1), weight=1)
        self.net_down = self._rate_block(rates, 0, "Download", CYAN)
        self.net_up = self._rate_block(rates, 1, "Upload", SOFT_PURPLE)
        self.net_chart = LineChart(net, app, (("down", CYAN), ("up", PURPLE)), height=70, y_max=None, axes=False,
                                   fill_alpha=0.26)
        self.net_chart.pack(fill="both", expand=True, padx=14, pady=(6, 4))
        self.net_foot = ctk.CTkLabel(net, text="—", font=F(11), text_color=DIM, anchor="w", height=18)
        self.net_foot.pack(fill="x", padx=16, pady=(0, 12))

        # --- system info
        info = app.card(body)
        info.grid(row=3, column=0, columnspan=2, sticky="nsew", padx=6, pady=6)
        app.card_header(info, "System", "info")
        grid = tk.Frame(info, bg=CARD)
        grid.pack(fill="both", expand=True, padx=16, pady=(0, 12))
        grid.grid_columnconfigure((0, 1), weight=1, uniform="info")
        self.info: Dict[str, ctk.CTkLabel] = {}
        rows = (("uptime", "Uptime", "clock"), ("idle", "Idle", "moon"),
                ("ip", "IP address", "globe"), ("wifi", "Wi-Fi", "wifi"),
                ("procs", "Processes", "tasks"), ("latency", "Hub latency", "speed"),
                ("actions", "Actions handled", "bolt"), ("session", "Connected", "power"))
        for i, (key, label, icon) in enumerate(rows):
            self.info[key] = self._info_cell(grid, i // 2, i % 2, label, icon)
        active = tk.Frame(grid, bg=CARD)
        active.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        active.grid_columnconfigure(1, weight=1)
        self._active_font = tkfont.Font(root=self, family=F.semi, size=-app.px(12))
        self._active_px = app.px(360)
        active.bind("<Configure>", self._active_resized, add="+")
        ctk.CTkLabel(active, text="", image=app.icons.get("window", 14, DIM), width=18).grid(row=0, column=0, rowspan=2,
                                                                                         sticky="n", pady=(3, 0))
        ctk.CTkLabel(active, text="Active window", font=F(12), text_color=MUTED, anchor="w", height=20).grid(
            row=0, column=1, sticky="w", padx=(8, 0))
        self.active_title = ctk.CTkLabel(active, text="—", font=F.s(12), text_color=TEXT, anchor="w", height=18)
        self.active_title.grid(row=1, column=1, columnspan=2, sticky="w", padx=(8, 0))
        self.active_proc = ctk.CTkLabel(active, text="", font=F(11), text_color=DIM, anchor="e", height=18)
        self.active_proc.grid(row=0, column=2, sticky="e")

        # --- controls
        ctrl = app.card(body)
        ctrl.grid(row=3, column=2, columnspan=2, sticky="nsew", padx=6, pady=6)
        app.card_header(ctrl, "Controls", "gear")
        cbody = tk.Frame(ctrl, bg=CARD)
        cbody.pack(fill="both", expand=True, padx=16, pady=(0, 14))
        cbody.grid_columnconfigure(1, weight=1)

        self.mute_btn = ctk.CTkButton(cbody, text="", image=app.icons.get("volume", 16, SOFT), width=36, height=34,
                                      command=self._toggle_mute, **app.button_style("secondary"))
        self.mute_btn.grid(row=0, column=0, padx=(0, 10), pady=4)
        self.vol_slider = ctk.CTkSlider(cbody, from_=0, to=100, number_of_steps=100, command=self._on_volume,
                                        scroll_step=0, height=18, **app.slider_style(CYAN))
        self.vol_slider.grid(row=0, column=1, sticky="ew", pady=4)
        self.vol_label = ctk.CTkLabel(cbody, text="—", width=46, font=F.s(12), text_color=TEXT, anchor="e")
        self.vol_label.grid(row=0, column=2, padx=(8, 0))

        self.bri_icon = ctk.CTkLabel(cbody, text="", image=app.icons.get("brightness", 16, AMBER), width=36, height=34)
        self.bri_slider = ctk.CTkSlider(cbody, from_=0, to=100, number_of_steps=100, command=self._on_brightness,
                                        scroll_step=0, height=18, **app.slider_style(AMBER))
        self.bri_label = ctk.CTkLabel(cbody, text="—", width=46, font=F.s(12), text_color=TEXT, anchor="e")

        media = tk.Frame(cbody, bg=CARD)
        media.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(8, 4))
        media.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(media, text="Media", font=F(12), text_color=MUTED, anchor="w").grid(row=0, column=0, sticky="w")
        mbtns = tk.Frame(media, bg=CARD)
        mbtns.grid(row=0, column=1, sticky="e")
        for name, action, text, width in (("prev", "prev", "", 40), ("play", "play_pause", "Play / Pause", 120),
                                          ("next", "next", "", 40)):
            app.button(mbtns, text, lambda a=action: app.run_action("media_control", {"action": a},
                                                                    f"Media {a.replace('_', '/')}"),
                       icon=name, width=width, height=32).pack(side="left", padx=(6, 0))

        quick = tk.Frame(cbody, bg=CARD)
        quick.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        quick.grid_columnconfigure((0, 1), weight=1, uniform="quick")
        actions = (("Lock PC", "lock", "power_action", {"action": "lock"}),
                   ("Screenshot", "camera", "take_screenshot", {}),
                   ("Show desktop", "desktop", "window_action", {"action": "minimize_all"}),
                   ("Open Downloads", "download", "open_folder", {"path": "downloads"}))
        for i, (label, icon, action, payload) in enumerate(actions):
            app.button(quick, label, lambda a=action, p=payload, l=label: app.run_action(a, p, l), icon=icon,
                       height=34).grid(row=i // 2, column=i % 2, sticky="ew", padx=(0 if i % 2 == 0 else 5,
                                                                                  5 if i % 2 == 0 else 0), pady=3)

    def _active_resized(self, event: Any) -> None:
        width = max(self.app.px(80), event.width - self.app.px(36))  # minus icon column and padding
        if abs(width - self._active_px) > 2:
            self._active_px = width
            self._labels.pop("active", None)
            if self._t and self.visible:
                self._set(self.active_title, "active", fit_text(self._active_font, self._t.get("active_window")
                                                                or "Desktop", width))

    def _rate_block(self, parent: tk.Misc, col: int, label: str, color: str) -> ctk.CTkLabel:
        F = self.app.fonts
        box = tk.Frame(parent, bg=CARD)
        box.grid(row=0, column=col, sticky="w")
        ctk.CTkLabel(box, text=label.upper(), font=F(10), text_color=DIM, anchor="w", height=14).pack(anchor="w")
        value = ctk.CTkLabel(box, text="—", font=F.s(17), text_color=color, anchor="w", height=24)
        value.pack(anchor="w")
        return value

    def _info_cell(self, parent: tk.Misc, row: int, col: int, label: str, icon: str) -> ctk.CTkLabel:
        app, F = self.app, self.app.fonts
        cell = tk.Frame(parent, bg=CARD)
        cell.grid(row=row, column=col, sticky="ew", padx=(0, 14) if col == 0 else (14, 0), pady=2)
        cell.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(cell, text="", image=app.icons.get(icon, 14, DIM), width=18, height=22).grid(row=0, column=0)
        ctk.CTkLabel(cell, text=label, font=F(12), text_color=MUTED, anchor="w", height=22).grid(
            row=0, column=1, sticky="w", padx=(8, 0))
        value = ctk.CTkLabel(cell, text="—", font=F.s(12), text_color=TEXT, anchor="e", height=22)
        value.grid(row=0, column=2, sticky="e")
        return value

    # ------------------------------------------------------------------ data

    def seed_history(self, history: Dict[str, Any], server_time: Optional[float]) -> None:
        """Pre-fills the charts from the hub's copy of this PC's recent samples (e.g. after an
        app restart) — only when that history is current, compared on the hub's own clock."""
        if not self.built or not isinstance(history, dict):
            return
        stamps = [_num(t) for t in history.get("t") or []]
        if not stamps or stamps[-1] is None:
            return
        now = _num(server_time) or stamps[-1]
        if now - stamps[-1] > 3 * HEARTBEAT_INTERVAL_SEC + 5:
            return  # the previous session ended a while ago
        cutoff = now - HISTORY_POINTS * HEARTBEAT_INTERVAL_SEC
        start = next((i for i, t in enumerate(stamps) if t is not None and t >= cutoff), len(stamps))

        def window(key: str, hold_zero: bool = False) -> List[Optional[float]]:
            out: List[Optional[float]] = []
            for value in (history.get(key) or [])[start:]:
                value = _num(value)
                if hold_zero and value == 0.0:  # see update_telemetry: 0.0 is a priming artefact
                    value = out[-1] if out else None
                out.append(value)
            return out

        self.chart.seed("cpu", window("cpu", hold_zero=True))
        self.chart.seed("ram", window("ram"))
        self.net_chart.seed("down", window("net_down"))
        self.net_chart.seed("up", window("net_up"))
        if self.visible:
            self.chart.render()
            self.net_chart.render()

    def update_telemetry(self, t: Dict[str, Any]) -> None:
        if not self.built:
            return
        if t.get("cpu_pct") == 0.0:
            # psutil's system CPU counter is process-global: the priming call, or any extra reading
            # between two heartbeats (e.g. a system_info action), yields a ~0 s window that reads
            # exactly 0.0. Hold the previous value instead of plotting a false dip.
            t = {**t, "cpu_pct": self._t.get("cpu_pct")}
        self._t = t
        self.chart.push({"cpu": t.get("cpu_pct"), "ram": t.get("ram_pct")})
        self.net_chart.push({"down": t.get("net_down_kbps"), "up": t.get("net_up_kbps")})
        if self.visible:
            self.refresh(animate=True)

    def on_show(self) -> None:
        self.refresh(animate=False)

    def _set(self, label: ctk.CTkLabel, key: str, text: str) -> None:
        if self._labels.get(key) != text:
            self._labels[key] = text
            label.configure(text=text)

    def refresh(self, animate: bool = False) -> None:
        t = self._t
        if not t:
            return
        app = self.app
        cpu, ram = _num(t.get("cpu_pct")), _num(t.get("ram_pct"))
        disk, bat = _num(t.get("disk_pct")), _num(t.get("battery_pct"))

        freq = _num(t.get("cpu_freq_mhz"))
        top = (t.get("top_processes") or [{}])[0] or {}
        top_txt = f"{ellipsize(top.get('name'), 16)} · {top.get('cpu', 0):.0f}%" if top.get("name") else ""
        self.gauges["cpu"].set(cpu, _level_color(cpu, CYAN, 70, 90), f"{cpu:.0f}%" if cpu is not None else "—",
                               f"{freq / 1000:.2f} GHz" if freq else "", top_txt, animate)

        used, total = _num(t.get("ram_used_gb")), _num(t.get("ram_total_gb"))
        self.gauges["ram"].set(ram, _level_color(ram, PURPLE, 80, 92), f"{ram:.0f}%" if ram is not None else "—",
                               f"{used:.1f} GB" if used is not None else "",
                               f"{total - used:.1f} GB free of {total:.0f} GB" if used is not None and total else "",
                               animate)

        free, dtotal = _num(t.get("disk_free_gb")), _num(t.get("disk_total_gb"))
        self.gauges["disk"].set(disk, _level_color(disk, SKY, 85, 95), f"{disk:.0f}%" if disk is not None else "—",
                                f"{free:.0f} GB free" if free is not None else "",
                                f"System drive · {dtotal:.0f} GB" if dtotal else "", animate)

        charging = bool(t.get("is_charging"))
        if bat is None:
            self.gauges["battery"].set(None, GREEN, "AC", "No battery", "Desktop power", animate)
        else:
            color = GREEN if charging or bat >= 30 else (AMBER if bat >= 15 else RED)
            secs = _num(t.get("battery_secs_left"))
            if charging:
                sub, foot = "Charging", ("Fully charged" if bat >= 99 else "Plugged in")
            else:
                sub, foot = "On battery", (f"{fmt_duration(secs)} left" if secs else "Estimating…")
            self.gauges["battery"].set(bat, color, f"{bat:.0f}%", sub, foot, animate)

        self._set(self.leg_cpu, "leg_cpu", f"CPU {cpu:.0f}%" if cpu is not None else "CPU —")
        self._set(self.leg_ram, "leg_ram", f"RAM {ram:.0f}%" if ram is not None else "RAM —")
        self.chart.render()
        self._set(self.net_down, "down", fmt_rate(t.get("net_down_kbps")))
        self._set(self.net_up, "up", fmt_rate(t.get("net_up_kbps")))
        self.net_chart.render()
        wifi, ip = t.get("wifi_ssid"), t.get("ip_address")
        self._set(self.net_foot, "net_foot", " · ".join(x for x in (f"Wi-Fi {wifi}" if wifi else "Wired / no Wi-Fi",
                                                                   ip or "") if x))

        node = app.node
        idle = _num(t.get("idle_sec"))
        uptime = _num(t.get("uptime_hours"))
        latency = node.latency_ms
        values = {
            "uptime": fmt_duration(uptime * 3600) if uptime is not None else "—",
            "idle": ("Active now" if idle < 5 else fmt_duration(idle)) if idle is not None else "—",
            "ip": ip or "—",
            "wifi": ellipsize(wifi, 22) if wifi else "Not on Wi-Fi",
            "procs": str(t.get("process_count") or "—"),
            "latency": f"{latency} ms" if latency is not None else "—",
            "actions": str(node.actions_handled),
            "session": fmt_duration(time.time() - node.connected_since)
            if node.connected and node.connected_since else "Offline",
        }
        for key, text in values.items():
            self._set(self.info[key], key, text)
        self._set(self.active_title, "active", fit_text(self._active_font, t.get("active_window") or "Desktop",
                                                        self._active_px))
        self._set(self.active_proc, "active_proc", t.get("active_process") or "")
        self._set(self.updated, "updated", f"Updated {time.strftime('%H:%M:%S')}")
        self._sync_volume(t.get("volume_level"), t.get("is_muted"))
        self._sync_brightness(t.get("brightness"))

    # --------------------------------------------------------------- volume

    def _sync_volume(self, level: Any, muted: Any) -> None:
        if self._vol_job or self._vol_pending or time.monotonic() - self._vol_user_ts < 3.0:
            return
        lvl = _num(level)
        if lvl is not None:
            self._vol_level = int(round(lvl))
            if int(round(self.vol_slider.get())) != self._vol_level:
                self.vol_slider.set(self._vol_level)
        if isinstance(muted, bool):
            self._muted = muted
        self._show_volume()

    def _show_volume(self) -> None:
        level = self._vol_level
        text = "—" if level is None else ("Muted" if self._muted else f"{level}%")
        self._set(self.vol_label, "vol", text)
        icon = "mute" if self._muted else "volume"
        if self._labels.get("vol_icon") != icon:
            self._labels["vol_icon"] = icon
            self.mute_btn.configure(image=self.app.icons.get(icon, 16, SOFT_RED if self._muted else SOFT))

    def _on_volume(self, value: float) -> None:
        level = int(round(value))
        if level == self._vol_level and not self._vol_job:
            return  # e.g. wheel over the slider (scroll_step=0) or a scaling redraw
        self._vol_user_ts = time.monotonic()
        self._vol_level = level
        self._set(self.vol_label, "vol", f"{level}%")
        if self._vol_job:
            self.after_cancel(self._vol_job)
        self._vol_job = self.after(250, lambda: self._apply_volume(level))

    def _apply_volume(self, level: int) -> None:
        self._vol_job = None
        self._vol_pending = True
        self.app.run_bg(self.app.silent_executor.execute_action, "volume_control", {"action": "set", "level": level},
                        on_done=self._volume_done, on_error=lambda e: self._volume_done({"success": False,
                                                                                         "error": str(e)}))

    def _volume_done(self, res: Dict[str, Any]) -> None:
        self._vol_pending = False
        self._vol_user_ts = time.monotonic()
        if res.get("success") is False:
            self.app.toast("Volume", res.get("error") or "Couldn't change the volume.", kind="error")
            return
        if isinstance(res.get("muted"), bool):
            self._muted = res["muted"]
        if res.get("level") is not None and not self._vol_job:
            self._vol_level = int(res["level"])
        self._show_volume()

    def _toggle_mute(self) -> None:
        action = "unmute" if self._muted else "mute"
        self._vol_pending = True
        self.app.run_bg(self.app.silent_executor.execute_action, "volume_control", {"action": action},
                        on_done=self._volume_done,
                        on_error=lambda e: self._volume_done({"success": False, "error": str(e)}))

    # ----------------------------------------------------------- brightness

    def enable_brightness(self, level: Optional[int]) -> None:
        """Shown only on laptops whose panel accepts software brightness."""
        if not self.built or level is None:
            return
        self._bri_level = level
        self.bri_slider.set(level)
        self.bri_label.configure(text=f"{level}%")
        self.bri_icon.grid(row=1, column=0, padx=(0, 10), pady=4)
        self.bri_slider.grid(row=1, column=1, sticky="ew", pady=4)
        self.bri_label.grid(row=1, column=2, padx=(8, 0))

    def _sync_brightness(self, level: Any) -> None:
        """Follows changes made elsewhere (phone, dashboard, keyboard) via live telemetry."""
        lvl = _num(level)
        if lvl is None or self._bri_job or time.monotonic() - self._bri_user_ts < 20.0:
            return  # the PC re-reads brightness every ~15 s; don't fight a recent drag
        if self._bri_level is None:
            self.enable_brightness(int(round(lvl)))
            return
        if int(round(lvl)) != self._bri_level:
            self._bri_level = int(round(lvl))
            self.bri_slider.set(self._bri_level)
            self.bri_label.configure(text=f"{self._bri_level}%")

    def _on_brightness(self, value: float) -> None:
        level = int(round(value))
        if level == self._bri_level and not self._bri_job:
            return
        self._bri_user_ts = time.monotonic()
        self._bri_level = level
        self.bri_label.configure(text=f"{level}%")
        if self._bri_job:
            self.after_cancel(self._bri_job)
        self._bri_job = self.after(400, lambda: self._apply_brightness(level))

    def _apply_brightness(self, level: int) -> None:
        self._bri_job = None

        def done(res: Dict[str, Any]) -> None:
            if res.get("success") is False:
                self.app.toast("Brightness", res.get("error") or "Couldn't change the brightness.", kind="error")

        self.app.run_bg(self.app.silent_executor.execute_action, "set_brightness", {"level": level}, on_done=done)


class AssistantPage(BasePage):
    SUGGESTIONS = ("What's my battery?", "Volume 40", "Open YouTube", "Take a screenshot", "Pause music")

    def build(self) -> None:
        app, F = self.app, self.app.fonts
        self.history: List[str] = []
        self._hist_idx: Optional[int] = None
        self._draft = ""
        self._tools_open = False

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        head, right, _ = app.page_header(self, "Assistant", "Ask or command Willy — runs through your hub over the "
                                                             "live connection")
        head.grid(row=0, column=0, sticky="ew", padx=18, pady=(12, 8))
        self.tools_btn = app.button(right, "Quick tools", self._toggle_tools, icon="chevron_down", kind="secondary",
                                    height=32)
        self.tools_btn.pack(side="right")
        app.button(right, "Clear", self.clear, icon="delete", kind="ghost", height=32).pack(side="right", padx=(0, 8))

        conv_card = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=16, border_width=1, border_color=BORDER)
        conv_card.grid(row=1, column=0, sticky="nsew", padx=18, pady=(0, 10))
        conv_card.grid_rowconfigure(0, weight=1)
        conv_card.grid_columnconfigure(0, weight=1)
        self.chat = ChatView(conv_card, app)
        self.chat.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=8)
        self.chat.scrollbar.grid(row=0, column=1, sticky="ns", padx=(2, 6), pady=12)
        self.chat.scrollbar.grid_remove()

        self.tools = self._build_tools()

        chips = tk.Frame(self, bg=BG)
        chips.grid(row=3, column=0, sticky="ew", padx=18, pady=(0, 8))
        for text in self.SUGGESTIONS:
            ctk.CTkButton(chips, text=text, height=30, width=10, corner_radius=15, font=F(12), fg_color=CARD,
                          hover_color=CARD_ALT, border_width=1, border_color=BORDER, text_color=SOFT,
                          command=lambda q=text: self.send(q)).pack(side="left", padx=(0, 8))

        bar = ctk.CTkFrame(self, fg_color=CARD, corner_radius=16, border_width=1, border_color=BORDER)
        bar.grid(row=4, column=0, sticky="ew", padx=18, pady=(0, 8))
        bar.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(bar, text="", image=app.icons.get("bolt", 16, CYAN), width=20).grid(row=0, column=0,
                                                                                      padx=(16, 4), pady=8)
        self.entry = ctk.CTkEntry(bar, height=40, border_width=0, fg_color=CARD, font=F(14), text_color=TEXT,
                                  placeholder_text="Ask Willy anything or give a command…",
                                  placeholder_text_color=DIM)
        self.entry.grid(row=0, column=1, sticky="ew", padx=(4, 8), pady=6)
        self.send_btn = app.button(bar, "Send", self.submit, icon="send", kind="primary", width=96, height=36)
        self.send_btn.grid(row=0, column=2, padx=(0, 8), pady=8)
        self.entry.bind("<Return>", lambda _e: self.submit())
        self.entry.bind("<Up>", lambda _e: self._history_step(-1))
        self.entry.bind("<Down>", lambda _e: self._history_step(1))

        opts = tk.Frame(self, bg=BG)
        opts.grid(row=5, column=0, sticky="ew", padx=20, pady=(0, 12))
        self.speak = ctk.CTkSwitch(opts, text="Speak replies", font=F(12), text_color=MUTED, progress_color=CYAN,
                                   button_color=TEXT, button_hover_color=SOFT, fg_color=BORDER_HI,
                                   switch_width=34, switch_height=18, onvalue=True, offvalue=False)
        self.speak.pack(side="left")
        self.speak.select()
        ctk.CTkLabel(opts, text="Enter to send · ↑ / ↓ for history · right-click a reply to copy it",
                     font=F(11), text_color=DIM).pack(side="right")

    def _build_tools(self) -> tk.Frame:
        app, F = self.app, self.app.fonts
        frame = tk.Frame(self, bg=BG)
        frame.grid_columnconfigure((0, 1), weight=1, uniform="tools")

        meet = app.card(frame)
        meet.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        app.card_header(meet, "Meeting scheduler", "calendar")
        m = tk.Frame(meet, bg=CARD)
        m.pack(fill="x", padx=16, pady=(0, 14))
        m.grid_columnconfigure(1, weight=1)
        self.meet_title = self._field(m, 0, "Title", "Project discussion with Hari")
        ctk.CTkLabel(m, text="Platform", font=F(12), text_color=MUTED, anchor="w", width=70).grid(row=1, column=0,
                                                                                               sticky="w", pady=3)
        self.meet_platform = ctk.CTkOptionMenu(m, values=["Google Calendar", "Microsoft Teams", "Zoom"], height=30,
                                               font=F(12), fg_color=SURFACE, button_color=BORDER,
                                               button_hover_color=BORDER_HI, dropdown_fg_color=CARD_ALT,
                                               dropdown_hover_color=BORDER, dropdown_font=F(12))
        self.meet_platform.grid(row=1, column=1, sticky="ew", pady=3)
        self.meet_time = self._field(m, 2, "When", "tomorrow at 3 PM")
        app.button(m, "Schedule via Willy", self._submit_meeting, icon="calendar", kind="secondary",
                   height=32).grid(row=3, column=0, columnspan=2, sticky="ew", pady=(8, 0))

        msg = app.card(frame)
        msg.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        app.card_header(msg, "Message", "chat")
        g = tk.Frame(msg, bg=CARD)
        g.pack(fill="x", padx=16, pady=(0, 14))
        g.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(g, text="Channel", font=F(12), text_color=MUTED, anchor="w", width=70).grid(row=0, column=0,
                                                                                              sticky="w", pady=3)
        self.msg_channel = ctk.CTkOptionMenu(g, values=["WhatsApp", "Email"], height=30, font=F(12),
                                             fg_color=SURFACE, button_color=BORDER, button_hover_color=BORDER_HI,
                                             dropdown_fg_color=CARD_ALT, dropdown_hover_color=BORDER,
                                             dropdown_font=F(12))
        self.msg_channel.grid(row=0, column=1, sticky="ew", pady=3)
        self.msg_to = self._field(g, 1, "To", "+91 98765 43210 or a contact")
        self.msg_body = self._field(g, 2, "Message", "Hello! Willy here.")
        self.msg_error = ctk.CTkLabel(g, text="", font=F(11), text_color=SOFT_RED, anchor="w", height=14)
        app.button(g, "Send via PC", self._submit_message, icon="send", kind="secondary",
                   height=32).grid(row=4, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        return frame

    def _field(self, parent: tk.Misc, row: int, label: str, placeholder: str) -> ctk.CTkEntry:
        F = self.app.fonts
        ctk.CTkLabel(parent, text=label, font=F(12), text_color=MUTED, anchor="w", width=70).grid(row=row, column=0,
                                                                                               sticky="w", pady=3)
        entry = ctk.CTkEntry(parent, height=30, font=F(12), fg_color=SURFACE, border_color=BORDER,
                             placeholder_text=placeholder, placeholder_text_color=DIM)
        entry.grid(row=row, column=1, sticky="ew", pady=3)
        return entry

    def _toggle_tools(self) -> None:
        self._tools_open = not self._tools_open
        if self._tools_open:
            self.tools.grid(row=2, column=0, sticky="ew", padx=18, pady=(0, 10))
        else:
            self.tools.grid_remove()
        self.tools_btn.configure(image=self.app.icons.get("chevron_up" if self._tools_open else "chevron_down",
                                                          14, SOFT))

    def _submit_meeting(self) -> None:
        title = self.meet_title.get().strip() or "Discussion"
        platform = self.meet_platform.get()
        when = self.meet_time.get().strip() or "tomorrow at 3 PM"
        self.send(f"Schedule a {platform} meeting titled '{title}' {when}")

    def _submit_message(self) -> None:
        channel = "WhatsApp" if self.msg_channel.get() == "WhatsApp" else "email"
        to, body = self.msg_to.get().strip(), self.msg_body.get().strip()
        if not to or not body:
            self.msg_error.configure(text="Add a recipient and a message.")
            self.msg_error.grid(row=3, column=0, columnspan=2, sticky="w", pady=(4, 0))
            return
        self.msg_error.grid_remove()
        self.send(f"Send a {channel} message to '{to}' saying: '{body}'")

    # --------------------------------------------------------------- chat

    def on_show(self) -> None:
        self.after(30, lambda: self.entry.focus_set())

    def focus_input(self) -> None:
        self.entry.focus_set()

    def _history_step(self, direction: int) -> str:
        if not self.history:
            return "break"
        if self._hist_idx is None:
            if direction > 0:
                return "break"
            self._draft = self.entry.get()
            self._hist_idx = len(self.history)
        self._hist_idx = max(0, min(len(self.history), self._hist_idx + direction))
        text = self._draft if self._hist_idx == len(self.history) else self.history[self._hist_idx]
        if self._hist_idx == len(self.history):
            self._hist_idx = None
        self.entry.delete(0, "end")
        self.entry.insert(0, text)
        self.entry.icursor("end")
        return "break"

    def submit(self) -> None:
        text = self.entry.get().strip()
        if text:
            self.entry.delete(0, "end")
            self.send(text)

    def send(self, query: str) -> None:
        query = query.strip()
        if not query:
            return
        self.ensure_built()
        if not self.history or self.history[-1] != query:
            self.history.append(query)
            del self.history[:-50]
        self._hist_idx = None
        self.chat.add("user", query)
        pending = self.chat.add("willy", "Thinking", pending=True)
        speak = bool(self.speak.get())
        node = self.app.node
        t0 = time.perf_counter()
        fut = node.submit(node.send_command(query, return_audio=speak))
        if fut is None:
            self._resolve(pending, {"success": False, "reply": "The Willy engine isn't running yet — try again "
                                                                 "in a moment."}, t0, False)
            return
        fut.add_done_callback(lambda f: self.app.post(self._on_future, pending, f, t0, speak))

    def _on_future(self, pending: Dict[str, Any], fut: Any, t0: float, speak: bool) -> None:
        try:
            res = fut.result()
        except Exception as e:
            res = {"success": False, "reply": f"Something went wrong: {e}"}
        self._resolve(pending, res if isinstance(res, dict) else {"success": True, "reply": str(res)}, t0, speak)

    def _resolve(self, msg: Dict[str, Any], res: Dict[str, Any], t0: float, speak: bool) -> None:
        ok = res.get("success", True) is not False
        reply = str(res.get("reply") or res.get("error") or ("Done." if ok else "That didn't work."))
        rtt = round((time.perf_counter() - t0) * 1000)
        timings = res.get("timings") or {}
        parts = []
        if res.get("fast_path"):
            parts.append("⚡ fast path")
        elif timings.get("llm_ms") is not None:
            parts.append(f"LLM {timings['llm_ms']} ms")
        tools = [t.get("name") for t in res.get("tools") or [] if isinstance(t, dict) and t.get("name")]
        if tools:
            parts.append(", ".join(tools[:3]))
        parts.append(f"{rtt} ms")
        if not ok and res.get("error") in ("OFFLINE", "SEND_FAILED", "DISCONNECTED", "TIMEOUT"):
            parts.insert(0, str(res["error"]).lower())
        self.chat.update_message(msg, reply, "  ·  ".join(parts), error=not ok)
        audio = res.get("audio_base64")
        if speak and audio and ok:
            self.app.play_audio(audio)

    def clear(self) -> None:
        self.chat.clear()


class ChatView(tk.Canvas):
    """The conversation drawn on one canvas: bubbles are antialiased 9-slice shapes (cached
    corner images + rects) around wrapped text items. Adding or scrolling messages redraws a
    single surface instead of moving dozens of CTk child windows (which stalled every send)."""

    is_scroll_area = True

    def __init__(self, master: tk.Misc, app: "WillyDesktopApp"):
        super().__init__(master, bg=SURFACE, highlightthickness=0, bd=0, yscrollincrement=1)
        self.app = app
        F = app.fonts
        px = app.px
        self.msgs: List[Dict[str, Any]] = []
        self._seq = 0
        self._r = px(14)
        self._wrap = px(460)
        self._width = 0
        self._total = 0
        self._bar = False
        self._layout_job: Optional[str] = None
        self._corners: Dict[Tuple[str, Optional[str]], List[ImageTk.PhotoImage]] = {}
        self.font, self.meta_font = F.tk(13), F.tk(11)
        self.scrollbar = ThinScrollbar(master, self.yview, SURFACE, app.scale)
        self.configure(yscrollcommand=self.scrollbar.set)
        self._welcome = [
            self.create_image(0, 0, image=app.avatar_large_photo),
            self.create_text(0, 0, text="Hi, I'm Willy.", font=F.tk(18, F.semi), fill=TEXT),
            self.create_text(0, 0, text="Ask about this PC or tell it what to do. Simple commands take the fast "
                                        "path and answer in milliseconds.", font=F.tk(12), fill=MUTED,
                             width=px(420), justify="center"),
        ]
        self.bind("<Configure>", self._on_configure)

    # ------------------------------------------------------------ shapes

    def _corner_images(self, fill: str, border: Optional[str]) -> List[ImageTk.PhotoImage]:
        key = (fill, border)
        if key not in self._corners:
            r, ss = self._r, 4
            d = 2 * r * ss
            img = Image.new("RGBA", (d, d), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            if border:
                draw.ellipse((0, 0, d - 1, d - 1), fill=_rgb(border) + (255,))
                draw.ellipse((ss, ss, d - 1 - ss, d - 1 - ss), fill=_rgb(fill) + (255,))
            else:
                draw.ellipse((0, 0, d - 1, d - 1), fill=_rgb(fill) + (255,))
            img = img.resize((2 * r, 2 * r), Image.LANCZOS)
            quads = (img.crop((0, 0, r, r)), img.crop((r, 0, 2 * r, r)), img.crop((0, r, r, 2 * r)),
                     img.crop((r, r, 2 * r, 2 * r)))
            self._corners[key] = [ImageTk.PhotoImage(q) for q in quads]
        return self._corners[key]

    def _new_shape(self, tag: str) -> Dict[str, int]:
        shape = {key: self.create_rectangle(0, 0, 0, 0, outline="", tags=tag) for key in ("a", "b")}
        for key, anchor in (("tl", "nw"), ("tr", "ne"), ("bl", "sw"), ("br", "se")):
            shape[key] = self.create_image(0, 0, anchor=anchor, tags=tag)
        for key in ("top", "bottom", "left", "right"):
            shape[key] = self.create_rectangle(0, 0, 0, 0, outline="", tags=tag)
        return shape

    def _style_shape(self, shape: Dict[str, int], fill: str, border: Optional[str]) -> None:
        corners = self._corner_images(fill, border)
        for key in ("a", "b"):
            self.itemconfigure(shape[key], fill=fill)
        for key, image in zip(("tl", "tr", "bl", "br"), corners):
            self.itemconfigure(shape[key], image=image)
        for key in ("top", "bottom", "left", "right"):
            self.itemconfigure(shape[key], fill=border or fill, state="normal" if border else "hidden")

    def _place_shape(self, shape: Dict[str, int], x0: float, y0: float, x1: float, y1: float) -> None:
        r = self._r
        x0, y0, x1, y1 = round(x0), round(y0), round(x1), round(y1)
        self.coords(shape["a"], x0 + r, y0, x1 - r, y1)
        self.coords(shape["b"], x0, y0 + r, x1, y1 - r)
        self.coords(shape["tl"], x0, y0)
        self.coords(shape["tr"], x1, y0)
        self.coords(shape["bl"], x0, y1)
        self.coords(shape["br"], x1, y1)
        self.coords(shape["top"], x0 + r, y0, x1 - r, y0 + 1)
        self.coords(shape["bottom"], x0 + r, y1 - 1, x1 - r, y1)
        self.coords(shape["left"], x0, y0 + r, x0 + 1, y1 - r)
        self.coords(shape["right"], x1 - 1, y0 + r, x1, y1 - r)

    # ---------------------------------------------------------- messages

    def add(self, who: str, text: str, pending: bool = False) -> Dict[str, Any]:
        stick = self.at_bottom()
        self._seq += 1
        tag = f"m{self._seq}"
        m: Dict[str, Any] = {"who": who, "text": text, "pending": pending, "error": False, "tag": tag,
                             "job": None, "t0": time.monotonic(), "top": 0, "bottom": 0}
        m["shape"] = self._new_shape(tag)
        if who == "user":
            self._style_shape(m["shape"], PURPLE, None)
            m["text_id"] = self.create_text(0, 0, text=text, anchor="ne", justify="left", font=self.font,
                                            fill="#FFFFFF", width=self._wrap, tags=tag)
        else:
            self._style_shape(m["shape"], CARD, BORDER)
            m["avatar"] = self.create_image(0, 0, anchor="nw", image=self.app.avatar_photo, tags=tag)
            m["text_id"] = self.create_text(0, 0, text=text, anchor="nw", justify="left", font=self.font,
                                            fill=MUTED if pending else TEXT, width=self._wrap, tags=tag)
            m["meta_id"] = self.create_text(0, 0, text="", anchor="nw", font=self.meta_font, fill=DIM, tags=tag)
        self.tag_bind(tag, "<Button-3>", lambda _e, msg=m: self.app.copy_text(msg["text"]))
        self.msgs.append(m)
        for item in self._welcome:
            self.itemconfigure(item, state="hidden")
        while len(self.msgs) > MAX_CHAT_ROWS:
            old = self.msgs.pop(0)
            self._stop(old)
            self.delete(old["tag"])
            self._relayout(0)
        self._relayout(len(self.msgs) - 1)
        if stick or who == "user":
            self.yview_moveto(1.0)
        if pending:
            self._animate(m, 0)
        return m

    def update_message(self, m: Dict[str, Any], text: str, meta: str = "", error: bool = False) -> None:
        if m not in self.msgs:
            return
        stick = self.at_bottom()
        self._stop(m)
        m.update(text=text, error=error, pending=False)
        self.itemconfigure(m["text_id"], text=text, fill=SOFT_RED if error else TEXT)
        if "meta_id" in m:
            self.itemconfigure(m["meta_id"], text=meta)
        self._style_shape(m["shape"], CARD, tint(RED, 0.45, SURFACE) if error else BORDER)
        self._relayout(self.msgs.index(m))
        if stick:
            self.yview_moveto(1.0)

    def _animate(self, m: Dict[str, Any], n: int) -> None:
        if not m["pending"] or m not in self.msgs:
            return
        elapsed = time.monotonic() - m["t0"]
        self.itemconfigure(m["text_id"], text="Thinking" + "." * (n % 4) + (f"  {elapsed:.0f}s" if elapsed >= 3
                                                                             else ""))
        self._relayout(self.msgs.index(m))  # one bubble: a handful of coords calls
        m["job"] = self.after(350, lambda: self._animate(m, n + 1))

    def _stop(self, m: Dict[str, Any]) -> None:
        if m.get("job"):
            self.after_cancel(m["job"])
            m["job"] = None

    def clear(self) -> None:
        for m in self.msgs:
            self._stop(m)
            self.delete(m["tag"])
        self.msgs = []
        for item in self._welcome:
            self.itemconfigure(item, state="normal")
        self._relayout(0)
        self.yview_moveto(0)

    # ------------------------------------------------------------- layout

    def _on_configure(self, _event: Any) -> None:
        if self._layout_job is None:
            self._layout_job = self.after(40, self._resized)

    def _resized(self) -> None:
        self._layout_job = None
        width = self.winfo_width()
        px = self.app.px
        wrap = int(max(px(220), min(px(720), width * 0.66 - px(40))))
        if width != self._width or wrap != self._wrap:
            stick = self.at_bottom()
            self._width, self._wrap = width, wrap
            for m in self.msgs:
                self.itemconfigure(m["text_id"], width=wrap)
            self._relayout(0)
            if stick:
                self.yview_moveto(1.0)
        cx, cy = width / 2, max(px(120), self.winfo_height() * 0.36)
        self.coords(self._welcome[0], cx, cy)
        self.coords(self._welcome[1], cx, cy + px(48))
        self.coords(self._welcome[2], cx, cy + px(80))
        self._sync_scroll()

    def _layout_one(self, m: Dict[str, Any], y: float) -> float:
        px = self.app.px
        width = self._width or self.winfo_width()
        margin, padx, pady = px(16), px(14), px(8)
        if m["who"] == "user":
            right = width - margin
            self.coords(m["text_id"], right - padx, y + pady)
            x0, _y0, _x1, y1 = self.bbox(m["text_id"])
            self._place_shape(m["shape"], min(x0 - padx, right - 2 * self._r - 2), y, right, y1 + pady)
            return y1 + pady
        self.coords(m["avatar"], margin, y)
        tx = margin + px(30) + px(10) + padx
        self.coords(m["text_id"], tx, y + pady)
        _x0, _y0, x1, y1 = self.bbox(m["text_id"])
        bottom = y1 + pady
        self._place_shape(m["shape"], tx - padx, y, max(x1 + padx, tx - padx + 2 * self._r + 2), bottom)
        self.coords(m["meta_id"], tx - padx + px(6), bottom + px(4))
        box = self.bbox(m["meta_id"])
        return max(bottom, box[3]) if box and self.itemcget(m["meta_id"], "text") else bottom

    def _relayout(self, start: int) -> None:
        px = self.app.px
        y = self.msgs[start - 1]["bottom"] + px(14) if 0 < start <= len(self.msgs) else px(14)
        for m in self.msgs[start:]:
            m["top"] = y
            m["bottom"] = self._layout_one(m, y)
            y = m["bottom"] + px(14)
        self._total = int(y)
        self._sync_scroll()

    def _sync_scroll(self) -> None:
        width, height = self.winfo_width(), self.winfo_height()
        total = self._total if self.msgs else 0
        self.configure(scrollregion=(0, 0, width, max(total, height)))
        need = total > height + 1
        if need != self._bar:
            self._bar = need
            if need:
                self.scrollbar.grid()
            else:
                self.scrollbar.grid_remove()
                self.yview_moveto(0)

    def at_bottom(self) -> bool:
        return not self._bar or self.yview()[1] >= 0.995

    def scroll_pixels(self, pixels: int) -> None:
        if self._bar and pixels:
            self.yview_scroll(pixels, "units")


class ProcessTable(tk.Canvas):
    """The process list drawn on one canvas. A refresh reconfigures existing text items in
    place (a single redraw) instead of re-laying out dozens of label/button widgets, and the
    rounded "End task" pills are shared antialiased images with crisp Tk text on top."""

    is_scroll_area = True
    ROW_H, BTN_W, BTN_H = 36, 84, 26

    def __init__(self, master: tk.Misc, app: "WillyDesktopApp", on_end_task: Callable[[Dict[str, Any]], None]):
        super().__init__(master, bg=CARD, highlightthickness=0, bd=0, yscrollincrement=1)
        self.app = app
        self.on_end_task = on_end_task
        F = app.fonts
        self.font = tkfont.Font(root=self, family=F.ui, size=-app.px(12))
        self.row_h = app.px(self.ROW_H)
        self.rows: List[Dict[str, Any]] = []
        self.items: List[Dict[str, int]] = []
        self.texts: List[List[Any]] = []
        self.shown: List[bool] = []
        self._fit_cache: Dict[Tuple[str, int], str] = {}
        self._cols: Dict[str, float] = {}
        self._hover = (-1, False)
        self._pills = (app.pill_image(self.BTN_W, self.BTN_H, None, tint(RED, 0.45)),
                       app.pill_image(self.BTN_W, self.BTN_H, tint(RED, 0.22, CARD_ALT), tint(RED, 0.65)))
        self.header = tk.Canvas(master, bg=CARD, highlightthickness=0, bd=0, height=app.px(30))
        self._head = {key: self.header.create_text(0, 0, text=title, fill=DIM, font=F.tk(11, F.semi),
                                                   anchor="w" if key == "name" else "e")
                      for key, title in (("name", "NAME"), ("pid", "PID"), ("cpu", "CPU"), ("mem", "MEMORY"))}
        self._head_line = self.header.create_rectangle(0, 0, 0, 0, fill=BORDER, outline="")
        self.scrollbar = ThinScrollbar(master, self.yview, CARD, app.scale)
        self.configure(yscrollcommand=self.scrollbar.set)
        self._bar = False
        self.empty_id = self.create_text(0, 0, text="Loading processes…", fill=MUTED, font=F.tk(12), anchor="n")
        self.bind("<Configure>", lambda _e: self._layout())
        self.header.bind("<Configure>", lambda _e: self._layout_header())
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", lambda _e: self._set_hover(-1, False))
        self.bind("<Button-1>", self._on_click)

    def _columns(self, width: int) -> Dict[str, float]:
        px = self.app.px
        btn_right = width - px(10)
        mem = btn_right - px(self.BTN_W) - px(22)
        cpu = mem - px(96)
        pid = cpu - px(80)
        return {"name": px(14), "pid": pid, "cpu": cpu, "mem": mem, "btn": btn_right - px(self.BTN_W) / 2,
                "name_w": max(px(60), pid - px(64) - px(14))}

    def _layout_header(self) -> None:
        width = self.header.winfo_width()
        cols = self._columns(width)
        cy = self.header.winfo_height() / 2
        for key, item in self._head.items():
            self.header.coords(item, cols[key], cy)
        h = self.header.winfo_height()
        self.header.coords(self._head_line, self.app.px(12), h - 1, width - self.app.px(12), h)

    def _layout(self) -> None:
        width = self.winfo_width()
        if width < 50:
            return
        cols = self._columns(width)
        if cols != self._cols:
            self._cols = cols
            self._fit_cache.clear()
            for i in range(len(self.items)):
                self._place_row(i)
                self.texts[i][0] = None  # re-fit names to the new width
            self.coords(self.empty_id, width / 2, self.app.px(28))
            if self.rows:
                self.set_rows(self.rows)
        self._sync_scroll()

    def _place_row(self, i: int) -> None:
        it, c, rh, px = self.items[i], self._cols, self.row_h, self.app.px
        y0 = i * rh
        cy = y0 + rh / 2
        width = self.winfo_width()
        self.coords(it["bg"], 0, y0, width, y0 + rh)
        self.coords(it["sep"], px(12), y0 + rh - 1, width - px(12), y0 + rh)
        self.coords(it["name"], c["name"], cy)
        self.coords(it["pid"], c["pid"], cy)
        self.coords(it["cpu"], c["cpu"], cy)
        self.coords(it["mem"], c["mem"], cy)
        self.coords(it["pill"], c["btn"], cy)
        self.coords(it["pill_text"], c["btn"], cy)

    def _create_row(self) -> None:
        i = len(self.items)
        tag = f"r{i}"
        F = self.app.fonts
        font = F.tk(12)
        it = {
            "bg": self.create_rectangle(0, 0, 0, 0, fill=CARD, outline="", tags=tag),
            "sep": self.create_rectangle(0, 0, 0, 0, fill=GRID, outline="", tags=tag),
            "name": self.create_text(0, 0, anchor="w", fill=TEXT, font=font, tags=tag),
            "pid": self.create_text(0, 0, anchor="e", fill=MUTED, font=font, tags=tag),
            "cpu": self.create_text(0, 0, anchor="e", fill=TEXT, font=font, tags=tag),
            "mem": self.create_text(0, 0, anchor="e", fill=TEXT, font=font, tags=tag),
            "pill": self.create_image(0, 0, image=self._pills[0], tags=tag),
            "pill_text": self.create_text(0, 0, text="End task", fill=SOFT_RED, font=F.tk(11), tags=tag),
        }
        self.items.append(it)
        self.texts.append([None, None, None, None, None])
        self.shown.append(True)
        if self._cols:
            self._place_row(i)

    def _fit(self, name: str, width: int) -> str:
        key = (name, width)
        fitted = self._fit_cache.get(key)
        if fitted is None:
            if len(self._fit_cache) > 2000:
                self._fit_cache.clear()
            fitted = self._fit_cache[key] = fit_text(self.font, name, width)
        return fitted

    def set_rows(self, rows: List[Dict[str, Any]], empty_text: str = "No processes.") -> None:
        self.rows = rows
        if not self._cols:
            return
        while len(self.items) < len(rows):
            self._create_row()
        name_w = int(self._cols["name_w"])
        for i, proc in enumerate(rows):
            it, cache = self.items[i], self.texts[i]
            if not self.shown[i]:
                self.itemconfigure(f"r{i}", state="normal")
                self.shown[i] = True
            cpu = float(proc.get("cpu") or 0.0)
            values = (self._fit(str(proc.get("name") or "?"), name_w), str(proc.get("pid", "")), f"{cpu:.1f}%",
                      fmt_mem(proc.get("mem_mb")), RED if cpu >= 50 else AMBER if cpu >= 20 else TEXT)
            for j, key in enumerate(("name", "pid", "cpu", "mem")):
                if cache[j] != values[j]:
                    cache[j] = values[j]
                    self.itemconfigure(it[key], text=values[j])
            if cache[4] != values[4]:
                cache[4] = values[4]
                self.itemconfigure(it["cpu"], fill=values[4])
        for i in range(len(rows), len(self.items)):
            if self.shown[i]:
                self.itemconfigure(f"r{i}", state="hidden")
                self.shown[i] = False
        self.itemconfigure(self.empty_id, text=empty_text, state="hidden" if rows else "normal")
        if self._hover[0] >= len(rows):
            self._set_hover(-1, False)
        self._sync_scroll()

    def _sync_scroll(self) -> None:
        width, height = self.winfo_width(), self.winfo_height()
        total = len(self.rows) * self.row_h
        self.configure(scrollregion=(0, 0, width, max(total, height)))
        need = total > height + 1
        if need != self._bar:
            self._bar = need
            if need:
                self.scrollbar.grid()
            else:
                self.scrollbar.grid_remove()
                self.yview_moveto(0)

    def scroll_pixels(self, pixels: int) -> None:
        if self._bar and pixels:
            self.yview_scroll(pixels, "units")

    def _hit(self, event: Any) -> Tuple[int, bool]:
        y = self.canvasy(event.y)
        i = int(y // self.row_h)
        if i < 0 or i >= len(self.rows):
            return -1, False
        px = self.app.px
        dy = y - (i * self.row_h + self.row_h / 2)
        on_btn = abs(event.x - self._cols.get("btn", -999)) <= px(self.BTN_W) / 2 and abs(dy) <= px(self.BTN_H) / 2
        return i, on_btn

    def _on_motion(self, event: Any) -> None:
        self._set_hover(*self._hit(event))

    def _set_hover(self, index: int, on_btn: bool) -> None:
        old, old_btn = self._hover
        if (index, on_btn) == (old, old_btn):
            return
        if 0 <= old < len(self.items):
            self.itemconfigure(self.items[old]["bg"], fill=CARD)
            self.itemconfigure(self.items[old]["pill"], image=self._pills[0])
        if 0 <= index < len(self.items):
            self.itemconfigure(self.items[index]["bg"], fill=CARD_ALT)
            self.itemconfigure(self.items[index]["pill"], image=self._pills[1 if on_btn else 0])
        self.configure(cursor="hand2" if on_btn else "")
        self._hover = (index, on_btn)

    def _on_click(self, event: Any) -> None:
        index, on_btn = self._hit(event)
        if on_btn and 0 <= index < len(self.rows):
            self.on_end_task(self.rows[index])


class ProcessesPage(BasePage):
    def build(self) -> None:
        app, F = self.app, self.app.fonts
        self._rows_data: List[Dict[str, Any]] = []
        self._sort = "cpu"
        self._job: Optional[str] = None
        self._filter_job: Optional[str] = None
        self._inflight = False
        self._status_job: Optional[str] = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        head, right, self.subtitle = app.page_header(self, "Processes", "Busiest apps on this PC · refreshes every "
                                                                        "3 s while open")
        head.grid(row=0, column=0, sticky="ew", padx=18, pady=(12, 8))
        app.button(right, "", lambda: self.refresh(fresh=True), icon="refresh", kind="secondary", width=36,
                   height=32).pack(side="right")
        self.filter = ctk.CTkEntry(right, width=210, height=32, font=F(12), fg_color=CARD, border_color=BORDER,
                                   placeholder_text="Filter by name or PID", placeholder_text_color=DIM)
        self.filter.pack(side="right", padx=(0, 8))
        self.filter.bind("<KeyRelease>", lambda _e: self._filter_changed(), add="+")
        self.sort_btn = ctk.CTkSegmentedButton(right, values=["CPU", "Memory"], command=self._sort_changed, height=32,
                                               font=F(12), fg_color=CARD, selected_color=tint(CYAN, 0.28, CARD),
                                               selected_hover_color=tint(CYAN, 0.36, CARD), unselected_color=CARD,
                                               unselected_hover_color=CARD_ALT, text_color=TEXT)
        self.sort_btn.set("CPU")
        self.sort_btn.pack(side="right", padx=(0, 8))

        table = app.card(self)
        table.grid(row=1, column=0, sticky="nsew", padx=18, pady=(0, 8))
        table.grid_rowconfigure(1, weight=1)
        table.grid_columnconfigure(0, weight=1)
        self.table = ProcessTable(table, app, self._confirm_kill)
        self.table.header.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(10, 0))
        self.table.grid(row=1, column=0, sticky="nsew", padx=(10, 2), pady=(0, 10))
        self.table.scrollbar.grid(row=1, column=1, sticky="ns", padx=(0, 6), pady=(2, 12))
        self.table.scrollbar.grid_remove()

        self.status = ctk.CTkLabel(self, text="", font=F(12), text_color=MUTED, anchor="w", height=20)
        self.status.grid(row=2, column=0, sticky="ew", padx=22, pady=(0, 12))

    # ------------------------------------------------------------ refresh

    def on_show(self) -> None:
        self.refresh(fresh=not self._rows_data)

    def on_hide(self) -> None:
        if self._job:
            self.after_cancel(self._job)
            self._job = None

    def refresh(self, fresh: bool = False) -> None:
        if self._job:
            self.after_cancel(self._job)
            self._job = None
        if self._inflight:
            return
        self._inflight = True
        # Cached rows come from the telemetry sampler (a steady ~4 s CPU window); a fresh
        # sample is only taken on demand so the hub's CPU numbers aren't skewed.
        self.app.run_bg(self.app.node.collector.top_processes, 1000, self._sort, fresh,
                        on_done=self._got_rows, on_error=self._got_error)

    def _got_rows(self, rows: List[Dict[str, Any]]) -> None:
        self._inflight = False
        self._rows_data = list(rows or [])
        self.render()
        self._schedule()

    def _got_error(self, err: Exception) -> None:
        self._inflight = False
        self._show_status(f"Couldn't read processes: {err}", RED)
        self._schedule()

    def _schedule(self) -> None:
        if self.visible and not self.app.closing:
            self._job = self.after(PROCESS_REFRESH_MS, self.refresh)

    def _sort_changed(self, value: str) -> None:
        self._sort = "memory" if value == "Memory" else "cpu"
        self.render()

    def _filter_changed(self) -> None:
        if self._filter_job:
            self.after_cancel(self._filter_job)
        self._filter_job = self.after(120, self.render)

    def render(self) -> None:
        self._filter_job = None
        needle = self.filter.get().strip().lower()
        key = (lambda r: (r.get("mem_mb", 0), r.get("cpu", 0))) if self._sort == "memory" else \
            (lambda r: (r.get("cpu", 0), r.get("mem_mb", 0)))
        rows = sorted(self._rows_data, key=key, reverse=True)
        if needle:
            rows = [r for r in rows if needle in str(r.get("name", "")).lower() or needle == str(r.get("pid"))]
        shown = rows[:PROCESS_ROWS]
        empty = f"No processes match “{needle}”." if needle else \
            ("Loading processes…" if not self._rows_data else "No processes.")
        self.table.set_rows(shown, empty)
        sort_txt = "memory" if self._sort == "memory" else "CPU"
        self.subtitle.configure(text=f"{len(self._rows_data)} processes · top {len(shown)} by {sort_txt}"
                                     f"{' matching filter' if needle else ''} · refreshes every 3 s while open")

    # --------------------------------------------------------------- kill

    def _confirm_kill(self, proc: Dict[str, Any]) -> None:
        if not proc:
            return
        name, pid = proc.get("name") or "this process", proc.get("pid")
        self.app.confirm.ask(
            f"End {name}?",
            f"PID {pid} will be closed right away. Unsaved work in this app will be lost.",
            "End task", lambda: self._kill(pid, name))

    def _kill(self, pid: Any, name: str) -> None:
        self._show_status(f"Ending {name} (PID {pid})…", MUTED, keep=True)

        def done(res: Dict[str, Any]) -> None:
            if res.get("success"):
                self._rows_data = [r for r in self._rows_data if r.get("pid") != pid]
                self.render()
                self._show_status(f"Ended {res.get('name') or name} (PID {pid}).", GREEN)
                self.after(1200, lambda: self.visible and self.refresh(fresh=True))
            else:
                self._show_status(res.get("error") or f"Couldn't end {name}.", RED)

        self.app.run_bg(self.app.node.executor.execute_action, "kill_process", {"pid": pid}, on_done=done,
                        on_error=lambda e: done({"success": False, "error": str(e)}))

    def _show_status(self, text: str, color: str, keep: bool = False) -> None:
        self.status.configure(text=text, text_color=color)
        if self._status_job:
            self.after_cancel(self._status_job)
            self._status_job = None
        if not keep:
            self._status_job = self.after(7000, lambda: self.status.configure(text=""))


class DeviceCard(ctk.CTkFrame):
    def __init__(self, master: tk.Misc, page: "DevicesPage", dev: Dict[str, Any]):
        app = page.app
        F = app.fonts
        super().__init__(master, fg_color=CARD, corner_radius=16, border_width=1, border_color=BORDER)
        self.page, self.app = page, app
        self.device_id = dev.get("device_id")
        self.is_self = self.device_id == app.node.device_id
        self.is_phone = dev.get("device_type") == "mobile"
        self.is_server = dev.get("device_type") == "server"
        self.dev = dev
        self._texts: Dict[str, str] = {}
        self._online: Optional[bool] = None
        self.grid_columnconfigure(1, weight=1)

        accent = SOFT_PURPLE if self.is_phone else AMBER if self.is_server else CYAN
        icon = "phone" if self.is_phone else "server" if self.is_server else "laptop"
        self.badge = ctk.CTkLabel(self, text="", width=44, height=44, corner_radius=12, fg_color=tint(accent, 0.15),
                                  image=app.icons.get(icon, 22, accent))
        self.badge.grid(row=0, column=0, rowspan=2, padx=(16, 12), pady=(16, 0), sticky="n")
        title_row = tk.Frame(self, bg=CARD)
        title_row.grid(row=0, column=1, sticky="ew", pady=(16, 0))
        self.name = ctk.CTkLabel(title_row, text="", font=F.s(15), text_color=TEXT, anchor="w", height=22)
        self.name.pack(side="left")
        if self.is_self:
            ctk.CTkLabel(title_row, text="THIS PC", font=F(9, "bold"), text_color=CYAN, fg_color=tint(CYAN, 0.14),
                         corner_radius=6, height=18, width=10, padx=6).pack(side="left", padx=(8, 0))
        self.status = ctk.CTkLabel(self, text="", font=F(11, "bold"), corner_radius=11, height=22, padx=10)
        self.status.grid(row=0, column=2, padx=16, pady=(16, 0), sticky="ne")
        self.sub = ctk.CTkLabel(self, text="", font=F(12), text_color=MUTED, anchor="w", height=18)
        self.sub.grid(row=1, column=1, columnspan=2, sticky="ew")

        self.stats_row = tk.Frame(self, bg=CARD)
        self.stats_row.grid(row=2, column=0, columnspan=3, sticky="ew", padx=16, pady=(14, 0))
        if self.is_phone:
            keys = (("battery", "battery"), ("notif", "bell"), ("whatsapp", "chat"), ("calls", "call"))
        elif self.is_server:
            keys = (("cpu", "chip"), ("ram", "memory"), ("disk", "disk"), ("uptime", "clock"))
        else:
            keys = (("battery", "battery"), ("cpu", "chip"), ("ram", "memory"), ("disk", "disk"))
        self.stats: Dict[str, ctk.CTkLabel] = {}
        for key, icon in keys:
            chip = ctk.CTkLabel(self.stats_row, text="—", image=app.icons.get(icon, 13, MUTED), compound="left",
                                font=F(12), text_color=SOFT, fg_color=CARD_ALT, corner_radius=8, height=26, padx=8)
            chip.pack(side="left", padx=(0, 6))
            self.stats[key] = chip

        foot = tk.Frame(self, bg=CARD)
        foot.grid(row=3, column=0, columnspan=3, sticky="ew", padx=16, pady=(12, 16))
        self.seen = ctk.CTkLabel(foot, text="", font=F(11), text_color=DIM, anchor="w", height=18)
        self.seen.pack(side="left")
        self.buttons: List[ctk.CTkButton] = []
        if self.is_phone:
            send = app.button(foot, "Send link / note", self._quickdrop, icon="link", height=30)
            send.pack(side="right")
            ring = app.button(foot, "Ring", self._ring, icon="bell", height=30, width=80)
            ring.pack(side="right", padx=(0, 6))
            self.buttons = [ring, send]
        elif self.is_server:
            open_page = app.button(foot, "Open Server page", lambda: app.show_page("server"), icon="server", height=30)
            open_page.pack(side="right")
        self.update_device(dev)

    def _set(self, widget: ctk.CTkLabel, key: str, text: str, **extra: Any) -> None:
        if self._texts.get(key) != text:
            self._texts[key] = text
            widget.configure(text=text, **extra)

    def update_device(self, dev: Dict[str, Any]) -> None:
        self.dev = dev
        t = dev.get("telemetry") or {}
        online = bool(dev.get("online", dev.get("status") == "online"))
        self._set(self.name, "name", ellipsize(dev.get("name") or dev.get("hostname") or self.device_id, 28))
        kind = "Phone" if self.is_phone else "Server" if self.is_server else "PC"
        model = t.get("model") if self.is_phone else None
        self._set(self.sub, "sub", " · ".join(str(x) for x in (dev.get("platform") or kind, model,
                                                                dev.get("device_id")) if x))
        if online != self._online:
            self._online = online
            color = GREEN if online else DIM
            self.status.configure(text=f"●  {'Online' if online else 'Offline'}", text_color=color,
                                  fg_color=tint(color, 0.16))
            self.configure(border_color=mix(BORDER, GREEN, 0.25) if online else BORDER)
            for btn in self.buttons:
                btn.configure(state="normal" if online else "disabled")
        if "battery" in self.stats:
            bat = _num(t.get("battery_pct"))
            bat_txt = "AC" if bat is None else f"{bat:.0f}%{' ⚡' if t.get('is_charging') else ''}"
            self._set(self.stats["battery"], "battery", bat_txt)
        if self.is_server:
            cpu, ram, free = _num(t.get("cpu_pct")), _num(t.get("ram_pct")), _num(t.get("disk_free_gb"))
            hours = _num(t.get("uptime_hours"))
            self._set(self.stats["cpu"], "cpu", f"CPU {cpu:.0f}%" if cpu is not None else "CPU —")
            self._set(self.stats["ram"], "ram", f"RAM {ram:.0f}%" if ram is not None else "RAM —")
            self._set(self.stats["disk"], "disk", f"{free:.0f} GB free" if free is not None else "Disk —")
            self._set(self.stats["uptime"], "uptime",
                      (f"Up {hours / 24:.0f} days" if hours >= 48 else f"Up {hours:.0f} h") if hours is not None else "Up —")
        elif self.is_phone:
            self._set(self.stats["notif"], "notif", f"{int(_num(t.get('unread_notifications')) or 0)} alerts")
            self._set(self.stats["whatsapp"], "whatsapp", f"{int(_num(t.get('unread_whatsapp')) or 0)} WhatsApp")
            self._set(self.stats["calls"], "calls", f"{int(_num(t.get('missed_calls')) or 0)} missed")
        else:
            cpu, ram, free = _num(t.get("cpu_pct")), _num(t.get("ram_pct")), _num(t.get("disk_free_gb"))
            self._set(self.stats["cpu"], "cpu", f"CPU {cpu:.0f}%" if cpu is not None else "CPU —")
            self._set(self.stats["ram"], "ram", f"RAM {ram:.0f}%" if ram is not None else "RAM —")
            self._set(self.stats["disk"], "disk", f"{free:.0f} GB free" if free is not None else "Disk —")
        self.tick()

    def tick(self, now: Optional[float] = None) -> None:
        dev = self.dev
        cmds = dev.get("commands_handled")
        seen = "Live now" if self._online and (now or time.time()) - (_num(dev.get("last_seen")) or 0) < 5 \
            else f"Last seen {fmt_ago(dev.get('last_seen'), now)}"
        self._set(self.seen, "seen", seen + (f" · {cmds} commands" if cmds else ""))

    def _ring(self) -> None:
        name = self.dev.get("name") or "phone"
        self.app.device_action(self.device_id, "ring_device", {"message": "Find My Phone", "duration_sec": 15},
                               f"Ringing {name}…", f"Ring {name}")

    def _quickdrop(self) -> None:
        name = self.dev.get("name") or "phone"
        self.app.quickdrop.ask(name, lambda payload: self.app.device_action(
            self.device_id, "quickdrop", payload, f"Sent to {name}.", f"Send to {name}"))


class DevicesPage(BasePage):
    def build(self) -> None:
        app, F = self.app, self.app.fonts
        self.cards: Dict[str, DeviceCard] = {}
        self._order: List[str] = []
        self._cols = 0
        self._layout_job: Optional[str] = None
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        head, right, self.subtitle = app.page_header(self, "Devices", "Everything connected to your Willy hub")
        head.grid(row=0, column=0, sticky="ew", padx=18, pady=(12, 8))
        self.counts = ctk.CTkLabel(right, text="", font=F(12), text_color=MUTED)
        self.counts.pack(side="right")
        self.scroll = ScrollArea(self, bg=BG, fill_height=False)
        self.scroll.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 10))
        self.grid_body = tk.Frame(self.scroll.body, bg=BG)
        self.grid_body.pack(fill="both", expand=True)
        self.empty = ctk.CTkLabel(self.scroll.body, text="Waiting for the hub…", font=F(13), text_color=MUTED)
        self.scroll.bind("<Configure>", lambda _e: self._queue_layout(), add="+")
        self.sync()

    def on_show(self) -> None:
        self.sync()

    def sync(self) -> None:
        """Mirrors node.devices (copied first: the node thread mutates it in place)."""
        if not self.built:
            return
        devices = dict(self.app.node.devices)
        for dev_id in [d for d in self.cards if d not in devices]:
            self.cards.pop(dev_id).destroy()
        for dev in devices.values():
            self.upsert(dev, relayout=False)
        self._queue_layout()

    def upsert(self, dev: Dict[str, Any], relayout: bool = True) -> None:
        if not self.built or not dev.get("device_id"):
            return
        card = self.cards.get(dev["device_id"])
        was_online = card._online if card else None
        if card is None:
            card = self.cards[dev["device_id"]] = DeviceCard(self.grid_body, self, dev)
            relayout = True
        elif self.visible:
            card.update_device(dev)
        else:
            card.dev = dev
            card._dirty = True  # type: ignore[attr-defined]
        if relayout or card._online != was_online:
            self._queue_layout()

    def tick(self) -> None:
        if not self.built or not self.visible:
            return
        now = time.time()
        for card in self.cards.values():
            if getattr(card, "_dirty", False):
                card._dirty = False  # type: ignore[attr-defined]
                card.update_device(card.dev)
            else:
                card.tick(now)

    def _queue_layout(self) -> None:
        if self._layout_job is None:
            self._layout_job = self.after(60, self._layout)

    def _layout(self) -> None:
        self._layout_job = None
        for card in self.cards.values():
            if getattr(card, "_dirty", False) and self.visible:
                card._dirty = False  # type: ignore[attr-defined]
                card.update_device(card.dev)
        own = self.app.node.device_id
        order = sorted(self.cards, key=lambda d: (d != own, not self.cards[d]._online,
                                                  self.cards[d].is_phone,
                                                  str(self.cards[d].dev.get("name") or d).lower()))
        width = self.scroll.winfo_width() / max(0.5, self.app.scale)
        cols = 1 if width < 860 else 2 if width < 1320 else 3  # a card needs ~420 px for its stat chips
        if order != self._order or cols != self._cols:
            self._order, self._cols = order, cols
            for c in range(3):
                self.grid_body.grid_columnconfigure(c, weight=1 if c < cols else 0, uniform="dev" if c < cols else "")
            for i, dev_id in enumerate(order):
                self.cards[dev_id].grid(row=i // cols, column=i % cols, sticky="nsew", padx=6, pady=6)
        online = sum(1 for c in self.cards.values() if c._online)
        self.counts.configure(text=f"{online} online · {len(self.cards) - online} offline" if self.cards else "")
        if self.cards:
            self.empty.pack_forget()
        else:
            self.empty.configure(text="No devices yet — waiting for the hub…" if not self.app.node.connected
                                 else "No devices are connected to the hub.")
            self.empty.pack(pady=40)


class NotificationList(tk.Canvas):
    """The phone's recent notifications on one canvas: app and title on the first line, the
    text on the second, each fitted to the width. Rows are reused and only the text items
    that changed are rewritten; the canvas is as tall as its rows (the page scrolls)."""

    ROW_H = 54
    TIME_W = 66  # right-hand "12m ago" column

    def __init__(self, master: tk.Misc, app: "WillyDesktopApp"):
        super().__init__(master, bg=CARD, highlightthickness=0, bd=0, height=app.px(72))
        self.app = app
        F = app.fonts
        self.rows: List[Dict[str, Any]] = []
        self.items: List[Dict[str, int]] = []
        self._cache: List[Dict[str, str]] = []
        self._shown: List[bool] = []
        self._width = 0
        self._height = 0
        self._job: Optional[str] = None
        self.row_h = app.px(self.ROW_H)
        self.f_app = tkfont.Font(root=self, family=F.semi, size=-app.px(11))
        self.f_title = tkfont.Font(root=self, family=F.semi, size=-app.px(12))
        self.f_body = tkfont.Font(root=self, family=F.ui, size=-app.px(12))
        self.f_time = tkfont.Font(root=self, family=F.ui, size=-app.px(11))
        self.empty_id = self.create_text(0, 0, anchor="n", text="", fill=MUTED, font=F.tk(12), justify="center")
        self.bind("<Configure>", self._on_configure)

    def _on_configure(self, _event: Any) -> None:
        if self._job is None:
            self._job = self.after(50, self._resized)

    def _resized(self) -> None:
        self._job = None
        if self.winfo_width() != self._width:
            self._width = self.winfo_width()
            self._layout()

    def set_rows(self, rows: List[Dict[str, Any]], empty_text: str = "") -> None:
        self.rows = list(rows)
        while len(self.items) < len(self.rows):
            self._new_row()
        for i, shown in enumerate(self._shown):
            show = i < len(self.rows)
            if shown != show:
                self._shown[i] = show
                self.itemconfigure(f"n{i}", state="normal" if show else "hidden")
        self.itemconfigure(self.empty_id, text=empty_text, state="hidden" if self.rows else "normal")
        self._layout()

    def _new_row(self) -> None:
        tag = f"n{len(self.items)}"
        self.items.append({
            "app": self.create_text(0, 0, anchor="w", fill=SOFT_PURPLE, font=self.f_app, tags=tag),
            "title": self.create_text(0, 0, anchor="w", fill=TEXT, font=self.f_title, tags=tag),
            "time": self.create_text(0, 0, anchor="e", fill=DIM, font=self.f_time, tags=tag),
            "body": self.create_text(0, 0, anchor="w", fill=MUTED, font=self.f_body, tags=tag),
            "sep": self.create_rectangle(0, 0, 0, 0, fill=GRID, outline="", tags=tag),
        })
        self._cache.append({})
        self._shown.append(True)

    def _put(self, i: int, key: str, text: str) -> None:
        if self._cache[i].get(key) != text:
            self._cache[i][key] = text
            self.itemconfigure(self.items[i][key], text=text)

    def _layout(self) -> None:
        px = self.app.px
        width = self._width or self.winfo_width()
        height = max(px(72), len(self.rows) * self.row_h)
        if height != self._height:
            self._height = height
            self.configure(height=height)
        self.coords(self.empty_id, width / 2, px(24))
        self.itemconfigure(self.empty_id, width=max(px(160), width - px(40)))
        if width < px(120):
            return
        pad, now, last = px(10), time.time(), len(self.rows) - 1  # + the canvas inset = the card's 16 px
        for i, row in enumerate(self.rows):
            it, top = self.items[i], i * self.row_h
            app_text = fit_text(self.f_app, row["app"], px(140))
            title_x = pad + self.f_app.measure(app_text) + px(8)
            title_w = width - pad - px(self.TIME_W) - title_x
            self._put(i, "app", app_text)
            self._put(i, "title", fit_text(self.f_title, row["title"], title_w) if title_w > px(24) else "")
            self._put(i, "body", fit_text(self.f_body, row["text"], width - 2 * pad))
            self._put(i, "time", fmt_ago(row["ts"], now) if row["ts"] else "")
            self.coords(it["app"], pad, top + px(18))
            self.coords(it["title"], title_x, top + px(18))
            self.coords(it["time"], width - pad, top + px(18))
            self.coords(it["body"], pad, top + px(37))
            if i < last:
                self.coords(it["sep"], pad, top + self.row_h - 1, width - pad, top + self.row_h)
            else:
                self.coords(it["sep"], 0, 0, 0, 0)

    def tick(self, now: Optional[float] = None) -> None:
        now = now or time.time()
        for i, row in enumerate(self.rows):
            self._put(i, "time", fmt_ago(row["ts"], now) if row["ts"] else "")


class PhonePage(BasePage):
    """Your Android phone from this PC: live status from its heartbeats, its recent
    notifications, and one-click phone actions sent through the hub (send_to_device).

    The page follows one phone (pick_phone: the most recently seen, preferring online ones).
    Hub events only mark it dirty while it's hidden; it re-reads node.devices and renders when
    shown, on phone events while visible, and every second for the relative times."""

    TILES = (("battery", "Battery", "battery", GREEN), ("network", "Network", "wifi", CYAN),
             ("storage", "Storage", "disk", SKY), ("ram", "Memory", "memory", SOFT_PURPLE),
             ("screen", "Screen", "brightness", AMBER), ("notif", "Notifications", "bell", CYAN),
             ("whatsapp", "WhatsApp", "chat", GREEN), ("calls", "Missed calls", "call", SOFT_RED))
    NETWORKS = {"wifi": ("Wi-Fi", "wifi"), "cellular": ("Mobile data", "cellular"),
                "ethernet": ("Ethernet", "ethernet"), "vpn": ("VPN", "globe"), "other": ("Connected", "globe"),
                "none": ("No network", "wifi")}

    def build(self) -> None:
        app, F = self.app, self.app.fonts
        self._phone: Optional[Dict[str, Any]] = None
        self._phone_id: Optional[str] = None
        self._dirty = True
        self._labels: Dict[str, Any] = {}
        self._ready: Optional[bool] = None  # phone actions enabled
        self._controls: List[Any] = []
        self._cols = self._tile_cols = 0
        self._banner_on = False
        self._channel = "sms"  # what Enter in the message box sends (the last channel used)
        self._notif_rows: List[Dict[str, Any]] = []
        self._notif_for: Optional[str] = None  # the phone the list came from
        self._notif_at = -1e9  # monotonic time of the last request
        self._notif_ok_at: Optional[float] = None
        self._notif_unread: Any = None  # the phone's unread count at the last request
        self._notif_busy = False
        self._notif_hint = ""  # e.g. notification access is off on the phone
        self._notif_error = ""
        self._notif_shown: Optional[Tuple[int, str]] = None
        self._notif_version = 0
        self._vol_job: Optional[str] = None
        self._vol_level: Optional[int] = None
        self._vol_user_ts = 0.0
        self._torch: Optional[bool] = None
        self._torch_user_ts = 0.0
        self._badges: Dict[Tuple[str, str, int], ImageTk.PhotoImage] = {}
        self._photos: List[ImageTk.PhotoImage] = []  # icons shown by plain Tk labels

        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self.scroll = ScrollArea(self, bg=BG, fill_height=False)
        self.scroll.grid(row=0, column=0, sticky="nsew")
        self.body = body = tk.Frame(self.scroll.body, bg=BG)
        body.pack(fill="both", expand=True, padx=12, pady=(4, 12))
        body.grid_columnconfigure((0, 1), weight=1, uniform="phone")

        head, right, _ = app.page_header(body, "Phone", "Your Android phone from this PC — live status, "
                                                        "notifications and quick actions")
        head.grid(row=0, column=0, columnspan=2, sticky="ew", padx=6, pady=(8, 6))
        self.seen = ctk.CTkLabel(right, text="", font=F(11), text_color=DIM)
        self.seen.pack(side="right")

        # --- who and where. Plain-text labels on this page are Tk labels: a CTkLabel costs three
        # native windows, and creating ~100 extra of them made the first visit stall.
        px = app.px
        hero = self.hero = app.card(body)
        hero.grid(row=1, column=0, columnspan=2, sticky="ew", padx=6, pady=6)
        hero.grid_columnconfigure(1, weight=1)
        tk.Label(hero, image=self._badge("phone", SOFT_PURPLE, 52, 14, 26), bg=CARD, bd=0).grid(
            row=0, column=0, rowspan=3, padx=(px(18), px(14)), pady=px(16), sticky="n")
        self.name = self._label(hero, "Looking for your phone…", F.tk(18, F.semi), TEXT)
        self.name.grid(row=0, column=1, sticky="sw", pady=(px(16), 0))
        self.pill = ctk.CTkLabel(hero, text="", font=F(11, "bold"), corner_radius=11, height=22, padx=10)
        self.pill.grid(row=0, column=2, sticky="ne", padx=18, pady=(18, 0))
        self.model = self._label(hero, "", F.tk(12), MUTED)
        self.model.grid(row=1, column=1, columnspan=2, sticky="w", pady=(px(2), 0))
        self.presence = self._label(hero, "", F.tk(12, F.semi), MUTED, justify="left", wraplength=px(560))
        self.presence.grid(row=2, column=1, columnspan=2, sticky="nw", padx=(0, px(18)), pady=(px(4), px(16)))

        # --- live tiles
        self.tiles_frame = tk.Frame(body, bg=BG)
        self.tiles_frame.grid(row=2, column=0, columnspan=2, sticky="ew")
        self.tiles = {key: self._tile(title, icon, accent) for key, title, icon, accent in self.TILES}

        self.left = tk.Frame(body, bg=BG)
        self.left.grid_columnconfigure(0, weight=1)
        self._build_actions(self.left)
        self.notif_card = self._build_notifications(body)
        self._layout(1180)  # sensible first layout; <Configure> refines it
        self.scroll.bind("<Configure>", lambda _e: self._layout(), add="+")
        self.sync()
        self.after_idle(self._premap)

    def _premap(self) -> None:
        """After a background build (_prebuild_pages), lays the page out once, unseen under the
        current page: creating its ~230 native windows takes ~0.5 s, better spent right after
        start-up than on the first click. No-op when the page is already being shown."""
        if self.visible or self.app.closing or self.winfo_manager():
            return
        self.lower()
        self.grid(row=0, column=0, sticky="nsew")
        try:
            self._update()  # the first render (e.g. greying out every control) happens now too
            self.update_idletasks()
        finally:
            if not self.visible:
                self.grid_remove()

    # ------------------------------------------------------------ widgets

    def _ctl(self, widget: Any) -> Any:
        """Registers a control that only works while the phone is reachable."""
        self._controls.append(widget)
        return widget

    def _label(self, parent: tk.Misc, text: str, font: Any, color: str, bg: str = CARD, **kw: Any) -> tk.Label:
        """A plain Tk text label (one native window; see build)."""
        return tk.Label(parent, text=text, font=font, fg=color, bg=bg, anchor="w", bd=0, padx=0, pady=0,
                        highlightthickness=0, **kw)

    def _badge(self, icon: str, accent: str, size: int, radius: int, glyph: int) -> Optional[ImageTk.PhotoImage]:
        """Rounded tinted square with an icon (what a CTkLabel badge draws), as one image."""
        key = (icon, accent, size)
        if key not in self._badges:
            px, ss = self.app.px, 3
            side = px(size)
            img = Image.new("RGBA", (side * ss, side * ss), (0, 0, 0, 0))
            ImageDraw.Draw(img).rounded_rectangle((0, 0, side * ss - 1, side * ss - 1), radius=px(radius) * ss,
                                                  fill=_rgb(tint(accent, 0.15)) + (255,))
            img = img.resize((side, side), Image.LANCZOS)
            mark = self.app.icons.pil(icon, px(glyph), accent)
            if mark is not None:
                img.alpha_composite(mark, ((side - mark.width) // 2, (side - mark.height) // 2))
            self._badges[key] = ImageTk.PhotoImage(img)
        return self._badges[key]

    def _icon(self, parent: tk.Misc, icon: str, size: int, color: str, bg: str = CARD) -> tk.Label:
        mark = self.app.icons.pil(icon, self.app.px(size), color)
        photo = ImageTk.PhotoImage(mark) if mark is not None else None
        if photo is not None:
            self._photos.append(photo)
        return tk.Label(parent, image=photo or "", bg=bg, bd=0, highlightthickness=0)

    def _tile(self, title: str, icon: str, accent: str) -> Dict[str, Any]:
        F, px = self.app.fonts, self.app.px
        tile = self.app.card(self.tiles_frame)
        tile.grid_columnconfigure(1, weight=1)
        badge = tk.Label(tile, image=self._badge(icon, accent, 36, 10, 17), bg=CARD, bd=0)
        badge.grid(row=0, column=0, rowspan=3, padx=(px(14), px(10)), pady=px(12))
        self._label(tile, title.upper(), F.tk(10, F.semi), DIM).grid(row=0, column=1, sticky="sw",
                                                                   padx=(0, px(10)), pady=(px(12), 0))
        value = self._label(tile, "—", F.tk(15, F.semi), TEXT)
        value.grid(row=1, column=1, sticky="w", padx=(0, px(10)), pady=(px(1), px(1)))
        sub = self._label(tile, "", F.tk(11), MUTED, height=1)
        sub.grid(row=2, column=1, sticky="nw", padx=(0, px(10)), pady=(0, px(12)))
        return {"frame": tile, "badge": badge, "value": value, "sub": sub, "accent": accent}

    def _entry(self, parent: tk.Misc, row: int, label: str, placeholder: str,
               submit: Callable[[], None]) -> ctk.CTkEntry:
        """A labelled one-line field; Enter submits it."""
        F = self.app.fonts
        if label:
            self._label(parent, label, F.tk(12), MUTED).grid(row=row, column=0, sticky="w",
                                                            padx=(0, self.app.px(8)), pady=self.app.px(3))
        entry = self._ctl(ctk.CTkEntry(parent, height=30, font=F(12), fg_color=SURFACE, border_color=BORDER,
                                       placeholder_text=placeholder, placeholder_text_color=DIM))
        entry.grid(row=row, column=1 if label else 0, columnspan=1 if label else 2, sticky="ew", pady=3)
        entry.bind("<Return>", lambda _e: submit())
        return entry

    def _entry_button(self, parent: tk.Misc, row: int, text: str, icon: str,
                      command: Callable[[], None]) -> ctk.CTkButton:
        btn = self._ctl(self.app.button(parent, text, command, icon=icon, width=92, height=30))
        btn.grid(row=row, column=2, sticky="ew", padx=(8, 0), pady=3)
        return btn

    def _card(self, parent: tk.Misc, row: int, title: str, icon: str) -> tk.Frame:
        card = self.app.card(parent)
        card.grid(row=row, column=0, sticky="ew", padx=6, pady=6)
        self.app.card_header(card, title, icon)
        inner = tk.Frame(card, bg=CARD)
        inner.pack(fill="x", padx=16, pady=(0, 14))
        inner.grid_columnconfigure(0, minsize=self.app.px(64))  # field labels line up across cards
        inner.grid_columnconfigure(1, weight=1)
        return inner

    def _build_actions(self, parent: tk.Misc) -> None:
        app, F = self.app, self.app.fonts
        # Shown instead of silently greyed-out buttons when the phone can't be reached.
        px, banner_bg = app.px, tint(AMBER, 0.10, BG)
        self.banner = ctk.CTkFrame(parent, fg_color=banner_bg, corner_radius=12, border_width=1,
                                   border_color=tint(AMBER, 0.40, BG))
        self.banner.grid_columnconfigure(1, weight=1)
        self._icon(self.banner, "error", 16, AMBER, bg=banner_bg).grid(row=0, column=0, padx=(px(14), px(10)),
                                                                        pady=px(10))
        self.banner_text = self._label(self.banner, "", F.tk(12, F.semi), SOFT, bg=banner_bg, justify="left",
                                       wraplength=px(300))
        self.banner_text.grid(row=0, column=1, sticky="w", padx=(0, px(14)), pady=px(10))

        # --- find & control
        c = self._card(parent, 1, "Find & control", "phone")
        c.grid_columnconfigure((0, 1), weight=1, uniform="find")
        c.grid_columnconfigure(2, weight=0)
        for i, (text, icon, command) in enumerate((("Ring", "bell", self.ring),
                                                    ("Stop ringing", "stop", self.stop_ring),
                                                    ("Flashlight", "flashlight", self.toggle_torch),
                                                    ("Vibrate", "vibrate", self.vibrate))):
            btn = self._ctl(app.button(c, text, command, icon=icon, height=34))
            btn.grid(row=i // 2, column=i % 2, sticky="ew", padx=(0, 5) if i % 2 == 0 else (5, 0), pady=3)
            if icon == "flashlight":
                self.torch_btn = btn
        media = tk.Frame(c, bg=CARD)
        media.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 2))
        media.grid_columnconfigure(1, weight=1)
        self._label(media, "Media", F.tk(12), MUTED).grid(row=0, column=0, sticky="w")
        buttons = tk.Frame(media, bg=CARD)
        buttons.grid(row=0, column=1, sticky="e")
        for icon, action, text, width in (("prev", "prev", "", 40), ("play", "play_pause", "Play / Pause", 120),
                                          ("next", "next", "", 40)):
            self._ctl(app.button(buttons, text, lambda a=action: self.media(a), icon=icon, width=width,
                                 height=32)).pack(side="left", padx=(6, 0))
        vol = tk.Frame(c, bg=CARD)
        vol.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        vol.grid_columnconfigure(0, minsize=px(36))
        vol.grid_columnconfigure(1, weight=1)
        self._icon(vol, "volume", 16, SOFT).grid(row=0, column=0, padx=(0, px(10)), pady=px(6))
        self.vol_slider = self._ctl(ctk.CTkSlider(vol, from_=0, to=100, number_of_steps=100, command=self._on_volume,
                                                  scroll_step=0, height=18, **app.slider_style(CYAN)))
        self.vol_slider.grid(row=0, column=1, sticky="ew")
        self.vol_label = ctk.CTkLabel(vol, text="—", width=46, font=F.s(12), text_color=TEXT, anchor="e")
        self.vol_label.grid(row=0, column=2, padx=(8, 0))

        # --- send to the phone
        s = self._card(parent, 2, "Send to phone", "send")
        self.send_entry = self._entry(s, 0, "", "Paste a link or type a note", self.send_to_phone)
        self._entry_button(s, 0, "Send", "send", self.send_to_phone)
        self._ctl(app.button(s, "Send PC clipboard", self.send_clipboard, icon="paste", height=30)).grid(
            row=1, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        self._ctl(app.button(s, "Send a file…", self.send_file, icon="folder", height=30)).grid(
            row=2, column=0, columnspan=3, sticky="ew", pady=(6, 0))

        # --- call & message
        m = self._card(parent, 3, "Call & message", "call")
        self.call_entry = self._entry(m, 0, "Call", "Name or number", self.call)
        self._entry_button(m, 0, "Call", "call", self.call)
        # Enter in "To" sends when the message is already typed, else moves on to the message.
        self.to_entry = self._entry(m, 1, "To", "Name or number", lambda: self.send_message(self._channel)
                                    if self.msg_entry.get().strip() else self.msg_entry.focus_set())
        self.msg_entry = self._entry(m, 2, "Message", "Type a message", lambda: self.send_message(self._channel))
        pair = tk.Frame(m, bg=CARD)
        pair.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        pair.grid_columnconfigure((0, 1), weight=1, uniform="msg")
        self.sms_btn = self._ctl(app.button(pair, "SMS", lambda: self.send_message("sms"), icon="message", height=32))
        self.sms_btn.grid(row=0, column=0, sticky="ew", padx=(0, 5))
        self.wa_btn = self._ctl(app.button(pair, "WhatsApp", lambda: self.send_message("whatsapp"), icon="chat",
                                           height=32))
        self.wa_btn.grid(row=0, column=1, sticky="ew", padx=(5, 0))  # styled by _show_channel on render

        # --- apps, maps, alarms
        a = self._card(parent, 4, "Apps, maps & alarms", "apps")
        self.app_entry = self._entry(a, 0, "Open app", "e.g. Spotify", self.open_app)
        self._entry_button(a, 0, "Open", "apps", self.open_app)
        self.nav_entry = self._entry(a, 1, "Navigate", "Place or address", self.navigate)
        self._entry_button(a, 1, "Go", "navigate", self.navigate)
        self.alarm_entry = self._entry(a, 2, "Alarm", "7:30 am, 19:05 or 7 pm", self.set_alarm)
        self._entry_button(a, 2, "Set", "clock", self.set_alarm)
        self.timer_entry = self._entry(a, 3, "Timer", "Minutes, e.g. 5", self.start_timer)
        self._entry_button(a, 3, "Start", "timer", self.start_timer)

    def _build_notifications(self, body: tk.Misc) -> ctk.CTkFrame:
        app, F = self.app, self.app.fonts
        card = app.card(body)
        slot = app.card_header(card, "Recent notifications", "bell")
        self.notif_refresh = self._ctl(app.button(slot, "", self.refresh_notifications, icon="refresh",
                                                  kind="secondary", width=30, height=26))
        self.notif_refresh.pack(side="right")
        self.notif_status = self._label(slot, "", F.tk(11), DIM)
        self.notif_status.pack(side="right", padx=(0, app.px(8)))
        self.notif_hint = ctk.CTkLabel(card, text="", font=F(12), text_color=AMBER, fg_color=tint(AMBER, 0.10),
                                       corner_radius=10, anchor="w", justify="left", wraplength=300, padx=10, pady=8)
        self.notif_list = NotificationList(card, app)
        # A plain Tk canvas: pad in real pixels, clear of the card's rounded corners at any scale.
        self.notif_list.pack(fill="x", padx=app.px(6), pady=(0, app.px(12)))
        return card

    def _layout(self, width: Optional[float] = None) -> None:
        """Two columns (actions | notifications) and 4 tiles a row; one column and 2 tiles
        a row when narrow. Wrap lengths follow the width."""
        width = width or self.scroll.winfo_width() / max(0.5, self.app.scale)
        if width < 100:
            return
        cols = 2 if width >= 720 else 1
        tile_cols = 4 if width >= 640 else 2
        if cols != self._cols:
            self._cols = cols
            if cols == 2:
                self.left.grid(row=3, column=0, columnspan=1, sticky="new")
                self.notif_card.grid(row=3, column=1, columnspan=1, sticky="nsew", padx=6, pady=6)
            else:
                self.left.grid(row=3, column=0, columnspan=2, sticky="new")
                self.notif_card.grid(row=4, column=0, columnspan=2, sticky="nsew", padx=6, pady=6)
        if tile_cols != self._tile_cols:
            self._tile_cols = tile_cols
            for c in range(4):
                self.tiles_frame.grid_columnconfigure(c, weight=1 if c < tile_cols else 0,
                                                      uniform="tile" if c < tile_cols else "")
            for i, (key, *_rest) in enumerate(self.TILES):
                self.tiles[key]["frame"].grid(row=i // tile_cols, column=i % tile_cols, sticky="nsew", padx=6, pady=6)
        cell = (width - 24) / cols  # one grid column (the body's 12 px margins aside)
        wraps = (int(max(200, width - 150)),  # hero presence: badge column and paddings
                 int(max(160, cell - 80)),     # offline banner: icon and paddings
                 int(max(160, cell - 96)))     # notification hint: card, label and text paddings
        if wraps != self._labels.get("wraps"):
            self._labels["wraps"] = wraps
            self.presence.configure(wraplength=self.app.px(wraps[0]))  # Tk labels wrap in real pixels
            self.banner_text.configure(wraplength=self.app.px(wraps[1]))
            self.notif_hint.configure(wraplength=wraps[2])  # a CTkLabel scales it itself

    # -------------------------------------------------------------- state

    def _set(self, widget: Any, key: str, text: str, **extra: Any) -> None:
        """Reconfigures a label only when its text or style changed (text_color works for
        both CTk and plain Tk labels)."""
        sig = (text, tuple(sorted(extra.items())))
        if self._labels.get(key) != sig:
            self._labels[key] = sig
            if isinstance(widget, tk.Label) and "text_color" in extra:
                extra["fg"] = extra.pop("text_color")
            widget.configure(text=text, **extra)

    @property
    def phone_name(self) -> str:
        return str((self._phone or {}).get("name") or "your phone")

    def on_show(self) -> None:
        self.sync()
        self._auto_fetch(on_show=True)

    def on_device(self, dev: Dict[str, Any]) -> None:
        """A hub device event: phones re-render the page (only marks it dirty while hidden)."""
        if self.built and isinstance(dev, dict) and dev.get("device_type") == "mobile":
            self.sync()

    def sync(self) -> None:
        """Re-reads the phone from node.devices (copied: the node thread replaces it) and
        renders; while hidden it only marks the page dirty."""
        if not self.built:
            return
        if not self.visible:
            self._dirty = True
            return
        self._dirty = False
        self._update()
        self._auto_fetch()

    def _update(self) -> None:
        """Picks the phone to follow and renders it (a switch to another phone resets its lists)."""
        phone = pick_phone(dict(self.app.node.devices), self._phone_id)
        phone_id = phone.get("device_id") if phone else None
        if phone_id != self._phone_id:
            self._phone_id = phone_id
            self._notif_rows, self._notif_for, self._notif_ok_at = [], None, None
            self._notif_hint = self._notif_error = ""
            self._notif_at, self._notif_version = -1e9, self._notif_version + 1
            self._torch, self._vol_level = None, None
        self._phone = phone
        self.render()

    def tick(self) -> None:
        if not self.built or not self.visible:
            return
        if self._dirty:
            self.sync()
            return
        now = time.time()
        if self._phone:
            self._set(self.seen, "seen", fmt_last_seen(self._phone.get("last_seen"), now))
            self._render_presence(now)
        self.notif_list.tick(now)
        self._render_notif_status()

    def _reachable(self) -> bool:
        return bool(self._phone) and device_online(self._phone) and self.app.node.connected

    # ------------------------------------------------------------- render

    def render(self) -> None:
        phone, now = self._phone, time.time()
        ready = self._reachable()
        if ready != self._ready:
            self._ready = ready
            for widget in self._controls:
                try:
                    widget.configure(state="normal" if ready else "disabled")
                except (tk.TclError, ValueError):
                    pass
            # A disabled CTkSlider looks just like an enabled one: grey it out.
            self.vol_slider.configure(progress_color=CYAN if ready else BORDER_HI,
                                      button_color=TEXT if ready else DIM)
            self._show_channel()
        if phone is None:
            self._set(self.name, "name", "No phone connected")
            self._set(self.model, "model", "Open Willy on your Android phone and connect it to this hub — "
                                           "it shows up here.")
            self._set(self.seen, "seen", "")
            banner = "No phone connected yet — open Willy on your Android phone to use these actions."
        else:
            t = phone.get("telemetry") or {}
            version = t.get("android_version")
            android = f"Android {version}" if version else (phone.get("platform") or "Android")
            self._set(self.name, "name", ellipsize(phone.get("name") or phone.get("hostname")
                                                   or phone.get("device_id"), 40))
            self._set(self.model, "model", " · ".join(str(x) for x in (t.get("model"), android) if x))
            self._set(self.seen, "seen", fmt_last_seen(phone.get("last_seen"), now))
            banner = "" if ready else ("This PC isn't connected to the hub — reconnecting…"
                                       if not self.app.node.connected else
                                       "Phone is offline — actions work again as soon as it reconnects.")
        self._render_presence(now)
        self._render_tiles()
        self._render_controls()
        if banner:
            self._set(self.banner_text, "banner", banner)
            if not self._banner_on:
                self._banner_on = True
                self.banner.grid(row=0, column=0, sticky="ew", padx=6, pady=(6, 0))
        elif self._banner_on:
            self._banner_on = False
            self.banner.grid_remove()
        self._render_notifications()

    def _render_presence(self, now: float) -> None:
        phone = self._phone
        if phone is None:
            pill, color, line, line_color = "●  Not connected", DIM, "", MUTED
        elif not self.app.node.connected:
            pill, color = "●  Hub offline", AMBER
            line, line_color = f"Last known: {presence_line(phone, now)}", MUTED
        elif device_online(phone):
            pill, color, line, line_color = "●  Online", GREEN, presence_line(phone, now), GREEN
        else:
            pill, color, line, line_color = "●  Offline", RED, presence_line(phone, now), SOFT
        self._set(self.pill, "pill", pill, text_color=color, fg_color=tint(color, 0.16))
        self._set(self.presence, "presence", line, text_color=line_color)
        border = mix(BORDER, GREEN, 0.25) if color == GREEN else BORDER
        if self._labels.get("hero_border") != border:
            self._labels["hero_border"] = border
            self.hero.configure(border_color=border)

    def _tile_set(self, key: str, value: str, sub: str = "", color: str = TEXT, icon: Optional[str] = None) -> None:
        tile = self.tiles[key]
        self._set(tile["value"], f"{key}.value", value, text_color=color)
        self._set(tile["sub"], f"{key}.sub", sub)
        if icon:
            self._set(tile["badge"], f"{key}.icon", "", image=self._badge(icon, tile["accent"], 36, 10, 17))

    def _render_tiles(self) -> None:
        t = (self._phone or {}).get("telemetry") or {}
        bat = _num(t.get("battery_pct"))
        if bat is None:
            self._tile_set("battery", "—")
        else:
            charging = bool(t.get("is_charging"))
            color = TEXT if charging or bat >= 30 else AMBER if bat >= 15 else RED
            self._tile_set("battery", f"{bat:.0f}%{' ⚡' if charging else ''}",
                           "Charging" if charging else "On battery", color)

        kind = str(t.get("network_type") or "").lower()
        label, icon = self.NETWORKS.get(kind, ("—" if not kind else kind.title(), "globe" if kind else "wifi"))
        ping = _num(t.get("last_ping_ms"))
        self._tile_set("network", label, f"{ping:.0f} ms to the hub" if ping is not None and
                       device_online(self._phone) else "", AMBER if kind == "none" else TEXT, icon)

        free, total = _num(t.get("storage_free_gb")), _num(t.get("storage_total_gb"))
        if free is None:
            self._tile_set("storage", "—")
        else:
            share = free / total if total else None
            color = TEXT if share is None or share >= 0.10 else AMBER if share >= 0.05 else RED
            self._tile_set("storage", f"{free:.1f} GB free" if free < 100 else f"{free:.0f} GB free",
                           f"of {total:.0f} GB" if total else "", color)

        ram = _num(t.get("ram_pct"))
        self._tile_set("ram", f"{ram:.0f}%" if ram is not None else "—", "in use" if ram is not None else "",
                       _level_color(ram, TEXT, 85, 95))

        screen = t.get("screen_on")
        self._tile_set("screen", "On" if screen is True else "Off" if screen is False else "—",
                       "Awake" if screen is True else "Asleep" if screen is False else "")

        for key, field, some, none in (("notif", "unread_notifications", "unread", "all caught up"),
                                       ("whatsapp", "unread_whatsapp", "unread messages", "no new messages"),
                                       ("calls", "missed_calls", "to call back", "none missed")):
            count = _num(t.get(field)) if self._phone else None
            if count is None:
                self._tile_set(key, "—")
            else:
                n = int(count)
                self._tile_set(key, str(n), some if n else none, SOFT_RED if key == "calls" and n else TEXT)

    def _render_controls(self) -> None:
        t = (self._phone or {}).get("telemetry") or {}
        torch = t.get("torch_on")
        if isinstance(torch, bool) and time.monotonic() - self._torch_user_ts > 8:
            self._torch = torch  # the phone's own state wins unless the user just flipped it
        self._show_torch()
        level = _num(t.get("volume_level"))
        if level is not None and not self._vol_job and time.monotonic() - self._vol_user_ts > 3:
            self._vol_level = int(round(level))
            if int(round(self.vol_slider.get())) != self._vol_level:
                self.vol_slider.set(self._vol_level)
        self._set(self.vol_label, "vol", f"{self._vol_level}%" if self._vol_level is not None else "—")

    def _show_torch(self) -> None:
        on = bool(self._torch) and bool(self._ready)  # a greyed-out button never looks lit
        if self._labels.get("torch") == on:
            return
        self._labels["torch"] = on
        if on:  # lit like a quick-settings tile while the flashlight is on
            self.torch_btn.configure(fg_color=tint(AMBER, 0.22, CARD), hover_color=tint(AMBER, 0.30, CARD),
                                     border_color=tint(AMBER, 0.60, CARD), text_color=TEXT,
                                     image=self.app.icons.get("flashlight", 14, AMBER))
        else:
            self.torch_btn.configure(image=self.app.icons.get("flashlight", 14, SOFT),
                                     **self.app.button_style("secondary"))

    def _show_channel(self) -> None:
        """The message button Enter uses is drawn as the primary one (while actions work)."""
        for channel, btn, icon in (("sms", self.sms_btn, "message"), ("whatsapp", self.wa_btn, "chat")):
            kind = "primary" if channel == self._channel and self._ready else "secondary"
            btn.configure(image=self.app.icons.get(icon, 14, BG if kind == "primary" else SOFT),
                          font=self.app.fonts.s(12) if kind == "primary" else self.app.fonts(12),
                          **self.app.button_style(kind))

    # ------------------------------------------------------ notifications

    def _render_notifications(self) -> None:
        phone = self._phone
        rows = self._notif_rows if self._notif_for == self._phone_id else []
        if phone is None:
            empty = "Connect your phone to see its notifications here."
        elif self._notif_hint:
            empty = ""
        elif rows:
            empty = ""
        elif self._notif_busy:
            empty = "Loading notifications…"
        elif self._notif_error:
            empty = self._notif_error
        elif not self._ready:
            empty = "Your phone is offline." if self.app.node.connected else "Waiting for the hub…"
        elif self._notif_for == self._phone_id:
            empty = "No recent notifications."
        else:
            empty = "Loading notifications…"
        shown = (self._notif_version, empty)
        if shown != self._notif_shown:
            self._notif_shown = shown
            self.notif_list.set_rows(rows, empty)
        hint = self._notif_hint if phone is not None else ""
        if self._labels.get("hint") != hint:
            self._labels["hint"] = hint
            if hint:
                self.notif_hint.configure(text=hint)
                self.notif_hint.pack(fill="x", padx=16, pady=(0, 10), before=self.notif_list)
            else:
                self.notif_hint.pack_forget()
        self._render_notif_status()

    def _render_notif_status(self) -> None:
        if self._notif_busy:
            text, color = "Loading…", DIM
        elif self._notif_hint:
            text, color = "", DIM  # the hint says it all
        elif self._notif_error and self._notif_rows:
            text, color = ellipsize(self._notif_error, 48), SOFT_RED
        elif self._notif_ok_at and self._notif_for == self._phone_id and self._phone:
            text, color = f"Updated {fmt_ago(self._notif_ok_at)}", DIM
        else:
            text, color = "", DIM
        self._set(self.notif_status, "notif_status", text, text_color=color)

    def _auto_fetch(self, on_show: bool = False) -> None:
        """Refreshes the list when the page opens or the phone's unread count changes, at
        most every PHONE_NOTIF_MIN_GAP_SEC (the Refresh button always asks right away)."""
        if not self.visible or not self._reachable() or self._notif_busy:
            return
        if time.monotonic() - self._notif_at < PHONE_NOTIF_MIN_GAP_SEC:
            return
        unread = ((self._phone or {}).get("telemetry") or {}).get("unread_notifications")
        if on_show or self._notif_for != self._phone_id or unread != self._notif_unread:
            self.refresh_notifications()

    def refresh_notifications(self) -> None:
        if self._notif_busy or not self._phone_id:
            return
        if not self._reachable():
            self._render_notifications()
            return
        node, device_id = self.app.node, self._phone_id
        self._notif_at = time.monotonic()
        self._notif_unread = ((self._phone or {}).get("telemetry") or {}).get("unread_notifications")
        fut = node.submit(node.send_action(device_id, "phone_notifications", {"limit": PHONE_NOTIF_LIMIT}))
        if fut is None:
            self._notif_error = "The Willy engine isn't running."
            self._render_notifications()
            return
        self._notif_busy = True
        self._render_notifications()
        fut.add_done_callback(lambda f: self.app.post(self._got_notifications, f, device_id))

    def _got_notifications(self, fut: Any, device_id: str) -> None:
        self._notif_busy = False
        try:
            res = fut.result()
        except Exception as e:  # noqa: BLE001 - shown on the page
            res = {"success": False, "error": str(e)}
        if not isinstance(res, dict):
            res = {"success": False, "error": "The phone sent an unexpected reply."}
        if device_id != self._phone_id:
            return  # the page moved on to another phone meanwhile
        if res.get("access") is False:
            self._notif_hint = str(res.get("error") or "Notification access is off on the phone.")
            self._notif_rows, self._notif_error = [], ""
            self._notif_version += 1
        elif res.get("success") is False:
            self._notif_error = str(res.get("reply") or res.get("error") or "The phone didn't answer.")
        else:
            self._notif_rows = normalize_notifications(res.get("notifications"))
            self._notif_hint = self._notif_error = ""
            self._notif_ok_at = time.time()
            self._notif_version += 1
        self._notif_for = device_id
        self._render_notifications()

    # ------------------------------------------------------------ actions

    def _target(self, label: str) -> Optional[str]:
        """The phone's device id when an action can go out now; otherwise says why not."""
        if not self._phone_id:
            self.app.toast("No phone connected", "Open Willy on your Android phone so this PC can reach it.",
                           kind="error")
            return None
        if not self._reachable():
            self.app.toast(label, "This PC isn't connected to the hub right now." if not self.app.node.connected
                           else f"{self.phone_name} is offline.", kind="error")
            return None
        return self._phone_id

    def _act(self, action: str, payload: Dict[str, Any], success_text: str, label: str,
             on_result: Optional[Callable[[Dict[str, Any]], None]] = None) -> bool:
        device_id = self._target(label)
        if device_id is None:
            return False
        self.app.device_action(device_id, action, payload, success_text, label, on_result=on_result)
        return True

    @staticmethod
    def _ok(res: Dict[str, Any]) -> bool:
        return isinstance(res, dict) and res.get("success", True) is not False

    def _clear_after(self, entry: ctk.CTkEntry, sent: str) -> Callable[[Dict[str, Any]], None]:
        """Result callback: empties the field after a success, unless it was edited meanwhile."""
        def done(res: Dict[str, Any]) -> None:
            if self._ok(res) and entry.get().strip() == sent:
                entry.delete(0, "end")
        return done

    def _need(self, entry: ctk.CTkEntry, label: str, message: str) -> Optional[str]:
        value = entry.get().strip()
        if not value:
            self.app.toast(label, message, kind="error", duration=3.5)
            if self._ready:
                entry.focus_set()
            return None
        return value

    def ring(self) -> None:
        name = self.phone_name
        self._act("ring_device", dict(PHONE_RING), f"Ringing {name}…", f"Ring {name}")

    def stop_ring(self) -> None:
        self._act("stop_ring", {}, "Stopped ringing.", f"Stop ringing {self.phone_name}")

    def vibrate(self) -> None:
        self._act("vibrate", {"ms": 600}, f"{self.phone_name} vibrated.", "Vibrate")

    def toggle_torch(self) -> None:
        on = not self._torch
        previous = self._torch

        def done(res: Dict[str, Any]) -> None:
            if not self._ok(res):  # didn't happen: show the phone's state again
                self._torch, self._torch_user_ts = previous, 0.0
                self._show_torch()

        if self._act("flashlight", {"on": on}, f"Flashlight {'on' if on else 'off'}.", "Flashlight", done):
            self._torch, self._torch_user_ts = on, time.monotonic()
            self._show_torch()

    def media(self, action: str) -> None:
        text = {"prev": "Previous track.", "play_pause": "Play / pause.", "next": "Next track."}.get(action, "Done.")
        self._act("phone_media", {"action": action}, text, "Phone media")

    def _on_volume(self, value: float) -> None:
        level = int(round(value))
        if level == self._vol_level and not self._vol_job:
            return  # e.g. a scaling redraw
        self._vol_user_ts = time.monotonic()
        self._vol_level = level
        self._set(self.vol_label, "vol", f"{level}%")
        if self._vol_job:
            self.after_cancel(self._vol_job)
        self._vol_job = self.after(PHONE_VOLUME_DEBOUNCE_MS, lambda: self._apply_volume(level))

    def _apply_volume(self, level: int) -> None:
        """Sent quietly (a pop-up per drag would be noise); only failures pop up."""
        self._vol_job = None
        device_id = self._target("Phone volume")
        node = self.app.node
        fut = node.submit(node.send_action(device_id, "phone_volume", {"level": level})) if device_id else None
        if device_id and fut is None:
            self.app.toast("Phone volume", "The Willy engine isn't running.", kind="error")
        if fut is not None:
            self.app.local_log(f"[UI] Phone volume {level}%")
            fut.add_done_callback(lambda f: self.app.post(self._volume_done, f))

    def _volume_done(self, fut: Any) -> None:
        try:
            res = fut.result()
        except Exception as e:  # noqa: BLE001 - shown as a pop-up
            res = {"success": False, "error": str(e)}
        res = res if isinstance(res, dict) else {}
        self._vol_user_ts = time.monotonic()
        if res.get("success") is False:
            self.app.toast("Phone volume", str(res.get("reply") or res.get("error") or "The volume didn't change."),
                           kind="error")
            return
        level = _num(res.get("level"))
        if level is not None and not self._vol_job:
            self._vol_level = int(round(level))
            if int(round(self.vol_slider.get())) != self._vol_level:
                self.vol_slider.set(self._vol_level)
            self._set(self.vol_label, "vol", f"{self._vol_level}%")

    def send_to_phone(self) -> None:
        raw = self._need(self.send_entry, "Send to phone", "Paste a link or type a note first.")
        if raw is None:
            return
        payload = quickdrop_payload(raw)
        what = "Link" if "url" in payload else "Note"
        self._act("quickdrop", payload, f"{what} sent to {self.phone_name}.", f"Send to {self.phone_name}",
                  self._clear_after(self.send_entry, raw))

    def send_clipboard(self) -> None:
        try:
            text = self.clipboard_get()
        except tk.TclError:  # empty, or not text (e.g. an image)
            text = ""
        if not str(text).strip():
            self.app.toast("Send clipboard", "The PC clipboard has no text to send.", kind="error")
            return
        if len(text) > CLIPBOARD_SEND_LIMIT:
            self.app.toast("Send clipboard", f"That's {len(text):,} characters — too much for the phone's "
                                             f"clipboard (up to {CLIPBOARD_SEND_LIMIT:,}).", kind="error")
            return
        self._act("set_clipboard", {"text": text}, "Copied to the phone's clipboard.",
                  f"Clipboard to {self.phone_name}")

    def send_file(self) -> None:
        """Picks a file and relays it to the phone's Downloads/Willy through the hub."""
        from tkinter import filedialog

        from pc_client.tools import file_relay

        chosen = filedialog.askopenfilename(parent=self, title=f"Send a file to {self.phone_name}")
        if not chosen:
            return
        path = Path(chosen)
        self.app.toast("Send file", f"Sending {path.name}…")

        def done(res: Dict[str, Any]) -> None:
            if res.get("success"):
                self.app.toast("Send file", str(res.get("message") or f"Sent {path.name}."), kind="success")
            else:
                self.app.toast("Send file", str(res.get("error") or "The file wasn't sent."), kind="error")

        self.app.run_bg(file_relay.send_file, path, "phone", on_done=done,
                        on_error=lambda e: self.app.toast("Send file", f"The file wasn't sent: {e}", kind="error"))

    def call(self) -> None:
        to = self._need(self.call_entry, "Call", "Type a name or number to call.")
        if to is not None:
            self._act("phone_call", {"to": to}, f"Calling {to}…", f"Call {to}")

    def send_message(self, channel: str) -> None:
        if channel != self._channel:
            self._channel = channel
            self._show_channel()
        label = "WhatsApp" if channel == "whatsapp" else "SMS"
        to = self.to_entry.get().strip()
        if channel == "sms" and not to:
            self._need(self.to_entry, label, "Who should get the text? Type a name or number.")
            return
        text = self._need(self.msg_entry, label, "Type the message first.")
        if text is None:
            return
        if channel == "whatsapp":
            self._act("phone_whatsapp", {"to": to, "message": text}, "WhatsApp is open on your phone — tap send.",
                      f"WhatsApp {to}".strip(), self._clear_after(self.msg_entry, text))
        else:
            self._act("phone_sms", {"to": to, "message": text}, f"Text sent to {to}.", f"SMS to {to}",
                      self._clear_after(self.msg_entry, text))

    def open_app(self) -> None:
        name = self._need(self.app_entry, "Open app", "Type the app's name, like Spotify.")
        if name is not None:
            self._act("phone_open_app", {"name": name}, f"Opening {name}…", f"Open {name}")

    def navigate(self) -> None:
        place = self._need(self.nav_entry, "Navigate", "Type a place or an address.")
        if place is not None:
            self._act("phone_navigate", {"destination": place, "mode": "driving"},
                      f"Directions to {place} are open on your phone.", "Navigate")

    def set_alarm(self) -> None:
        raw = self._need(self.alarm_entry, "Alarm", "Type a time like 7:30 am, 19:05 or 7 pm.")
        if raw is None:
            return
        parsed = parse_alarm_time(raw)
        if parsed is None:
            self.app.toast("Alarm", f"“{ellipsize(raw, 30)}” isn't a time — try 7:30 am, 19:05 or 7 pm.",
                           kind="error")
            return
        hour, minute = parsed
        self._act("phone_alarm", {"hour": hour, "minute": minute, "label": "Willy alarm"},
                  f"Alarm set for {fmt_clock(hour, minute)}.", "Alarm")

    def start_timer(self) -> None:
        raw = self._need(self.timer_entry, "Timer", "Type the minutes, like 5 or 2.5.")
        if raw is None:
            return
        seconds = parse_timer_seconds(raw)
        if seconds is None:
            self.app.toast("Timer", f"“{ellipsize(raw, 30)}” isn't a timer length — type minutes "
                                    "(up to 24 hours), like 5 or 2.5.", kind="error")
            return
        self._act("phone_timer", {"seconds": seconds, "label": "Willy timer"},
                  f"Timer set for {fmt_duration(seconds)}.", "Timer")


class ActivityFeed(tk.Canvas):
    """Live command feed on one canvas. A new entry slides in at the top by moving the existing
    rows (one move() per row) instead of re-packing and repainting dozens of child windows."""

    is_scroll_area = True
    SOURCES = {"pc": ("PC", CYAN), "mobile": ("PHONE", SOFT_PURPLE), "voice": ("VOICE", GREEN),
               "web": ("WEB", SKY), "dashboard": ("WEB", SKY), "api": ("API", AMBER)}

    def __init__(self, master: tk.Misc, app: "WillyDesktopApp"):
        super().__init__(master, bg=CARD, highlightthickness=0, bd=0, yscrollincrement=1)
        self.app = app
        F = app.fonts
        self.rows: Dict[str, Dict[str, Any]] = {}
        self.order: List[str] = []  # newest first
        self._pills: Dict[Tuple[str, str], Tuple[ImageTk.PhotoImage, int]] = {}
        self._width = 0
        self._bar = False
        self._layout_job: Optional[str] = None
        self.f_query, self.f_reply, self.f_meta = F.tk(13, F.semi), F.tk(12), F.tk(11)
        self._query_font = tkfont.Font(root=self, family=F.semi, size=-app.px(13))
        self._pill_font = tkfont.Font(root=self, family=F.ui, size=-app.px(9), weight="bold")
        self.scrollbar = ThinScrollbar(master, self.yview, CARD, app.scale)
        self.configure(yscrollcommand=self.scrollbar.set)
        self.empty_id = self.create_text(0, 0, anchor="n", fill=MUTED, font=F.tk(12), justify="center",
                                         text="No activity yet. Commands from the phone, dashboard and this app "
                                              "appear here.")
        self.bind("<Configure>", self._on_configure)

    def _pill(self, label: str, color: str) -> Tuple[ImageTk.PhotoImage, int]:
        key = (label, color)
        if key not in self._pills:
            width = self._pill_font.measure(label) / self.app.scale + 14
            fill = tint(color, 0.16)
            image = self.app.pill_image(int(width), 18, fill, fill, radius=6)
            self._pills[key] = (image, image.width())
        return self._pills[key]

    # ---------------------------------------------------------------- rows

    def upsert(self, entry: Dict[str, Any], restack: bool = True) -> None:
        entry_id = entry.get("id")
        if not entry_id:
            return
        row = self.rows.get(entry_id)
        if row is None:
            row = self._new_row(entry_id)
            self.rows[entry_id] = row
            self.order.insert(0, entry_id)
            while len(self.order) > MAX_ACTIVITY_ROWS:
                self.delete(self.rows.pop(self.order.pop())["tag"])
            self._fill(row, entry)
            if restack:
                self.restack()
        else:
            height = row["height"]
            self._fill(row, entry)
            if restack and row["height"] != height:
                self.restack()

    def remove_missing(self, keep: set) -> None:
        for entry_id in [i for i in self.order if i not in keep]:
            self.order.remove(entry_id)
            self.delete(self.rows.pop(entry_id)["tag"])
        self.restack()

    def _new_row(self, entry_id: str) -> Dict[str, Any]:
        tag = f"a_{len(self.rows)}_{abs(hash(entry_id)) % 10 ** 8}"
        app = self.app
        row: Dict[str, Any] = {"tag": tag, "top": 0, "height": 0, "entry": {}, "texts": {}}
        row["icon"] = self.create_text(0, 0, font=app.fonts.icon_tk(16), tags=tag)
        row["pill"] = self.create_image(0, 0, anchor="w", tags=tag)
        row["pill_text"] = self.create_text(0, 0, font=self._pill_font, tags=tag)
        row["query"] = self.create_text(0, 0, anchor="w", font=self.f_query, fill=TEXT, tags=tag)
        row["when"] = self.create_text(0, 0, anchor="e", font=self.f_meta, fill=DIM, tags=tag)
        row["reply"] = self.create_text(0, 0, anchor="nw", font=self.f_reply, fill=MUTED, tags=tag)
        row["meta"] = self.create_text(0, 0, anchor="nw", font=self.f_meta, fill=DIM, tags=tag)
        row["sep"] = self.create_rectangle(0, 0, 0, 0, fill=GRID, outline="", tags=tag)
        return row

    def _set(self, row: Dict[str, Any], key: str, item: Optional[str] = None, **opts: Any) -> None:
        sig = tuple(sorted((k, str(v)) for k, v in opts.items()))
        if row["texts"].get(key) != sig:
            row["texts"][key] = sig
            self.itemconfigure(row[item or key], **opts)

    def _fill(self, row: Dict[str, Any], entry: Dict[str, Any]) -> None:
        row["entry"] = entry
        glyph = self.app.glyph
        status = entry.get("status")
        if status == "running":
            icon, color = glyph("running"), AMBER
        elif status == "error" or entry.get("success") is False:
            icon, color = glyph("error"), RED
        else:
            icon, color = glyph("check"), GREEN
        self._set(row, "icon", text=icon, fill=color)
        src = str(entry.get("source") or "api").lower()
        label, scolor = self.SOURCES.get(src, (src.upper()[:8], MUTED))
        image, row["pill_w"] = self._pill(label, scolor)
        self._set(row, "pill", image=image)
        self._set(row, "pill_text", text=label, fill=scolor)
        reply = entry.get("reply")
        self._set(row, "reply", text=ellipsize(reply, 300) if reply else
                  ("Working on it…" if status == "running" else "—"), fill=SOFT_RED if status == "error" else MUTED)
        parts = []
        if entry.get("fast_path"):
            parts.append("⚡ fast path")
        tools = [t for t in entry.get("tools") or [] if t]
        if tools:
            parts.append("tools: " + ", ".join(str(t) for t in tools[:4]))
        if entry.get("latency_ms") is not None:
            parts.append(f"{entry['latency_ms']} ms")
        self._set(row, "meta", text="  ·  ".join(parts))
        self._tick_row(row, time.time())
        self._layout_row(row)

    def _layout_row(self, row: Dict[str, Any]) -> None:
        """Positions a row's items relative to its top; updates its height."""
        px = self.app.px
        width = self._width or self.winfo_width()
        y, x = row["top"], px(48)
        line = y + px(22)
        pill_w = row.get("pill_w", px(40))
        self.coords(row["icon"], px(26), line)
        self.coords(row["pill"], x, line)
        self.coords(row["pill_text"], x + pill_w / 2, line)
        query_x = x + pill_w + px(8)
        self.coords(row["query"], query_x, line)
        self.coords(row["when"], width - px(16), line)
        room = int(width - px(16) - px(78) - query_x)
        self._set(row, "query", text=fit_text(self._query_font, row["entry"].get("query") or "—", max(40, room)))
        self._set(row, "reply_width", item="reply", width=max(px(120), width - x - px(20)))
        self.coords(row["reply"], x, y + px(36))
        bottom = (self.bbox(row["reply"]) or (0, 0, 0, y + px(52)))[3]
        if self.itemcget(row["meta"], "text"):
            self.coords(row["meta"], x, bottom + px(3))
            bottom = (self.bbox(row["meta"]) or (0, 0, 0, bottom))[3]
        bottom += px(12)
        self.coords(row["sep"], px(14), bottom - 1, width - px(14), bottom)
        row["height"] = bottom - y

    def restack(self) -> None:
        y = self.app.px(6)
        for entry_id in self.order:
            row = self.rows[entry_id]
            if row["top"] != y:
                self.move(row["tag"], 0, y - row["top"])
                row["top"] = y
            y += row["height"]
        self.itemconfigure(self.empty_id, state="hidden" if self.order else "normal")
        self._sync_scroll(y + self.app.px(6))

    def _sync_scroll(self, total: int) -> None:
        width, height = self.winfo_width(), self.winfo_height()
        total = total if self.order else 0
        self.configure(scrollregion=(0, 0, width, max(total, height)))
        need = total > height + 1
        if need != self._bar:
            self._bar = need
            if need:
                self.scrollbar.grid()
            else:
                self.scrollbar.grid_remove()
                self.yview_moveto(0)

    def _on_configure(self, _event: Any) -> None:
        if self._layout_job is None:
            self._layout_job = self.after(60, self._relayout_all)

    def _relayout_all(self) -> None:
        self._layout_job = None
        width = self.winfo_width()
        self.coords(self.empty_id, width / 2, self.app.px(40))
        self.itemconfigure(self.empty_id, width=max(self.app.px(200), width - self.app.px(80)))
        if width != self._width:
            self._width = width
            for entry_id in self.order:
                self._layout_row(self.rows[entry_id])
        self.restack()

    def _tick_row(self, row: Dict[str, Any], now: float) -> None:
        self._set(row, "when", text=fmt_ago(row["entry"].get("ts"), now))

    def tick(self) -> None:
        now = time.time()
        for row in self.rows.values():
            self._tick_row(row, now)

    def scroll_pixels(self, pixels: int) -> None:
        if self._bar and pixels:
            self.yview_scroll(pixels, "units")


class ActivityPage(BasePage):
    def build(self) -> None:
        app, F = self.app, self.app.fonts
        self._dirty = False
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        head, right, self.subtitle = app.page_header(self, "Activity", "Every command the hub handles, live")
        head.grid(row=0, column=0, sticky="ew", padx=18, pady=(12, 8))
        self.stats = ctk.CTkLabel(right, text="", font=F(12), text_color=MUTED)
        self.stats.pack(side="right")
        card = app.card(self)
        card.grid(row=1, column=0, sticky="nsew", padx=18, pady=(0, 14))
        card.grid_rowconfigure(0, weight=1)
        card.grid_columnconfigure(0, weight=1)
        self.feed = ActivityFeed(card, app)
        self.feed.grid(row=0, column=0, sticky="nsew", padx=(4, 0), pady=8)
        self.feed.scrollbar.grid(row=0, column=1, sticky="ns", padx=(2, 6), pady=12)
        self.feed.scrollbar.grid_remove()
        self._dirty = True

    def on_show(self) -> None:
        if self._dirty:
            self.sync()
        self.tick()

    def sync(self) -> None:
        """Mirrors node.activity (copied first: the node thread mutates it in place)."""
        if not self.built:
            return
        self._dirty = False
        entries = list(self.app.node.activity)[:MAX_ACTIVITY_ROWS]
        self.feed.remove_missing({e.get("id") for e in entries})
        for entry in reversed(entries):  # oldest first, so each new row lands on top
            self.feed.upsert(entry, restack=False)
        self.feed.restack()
        self._update_stats()

    def upsert(self, entry: Dict[str, Any], stats: bool = True) -> None:
        if not self.built:
            return
        if not self.visible:
            self._dirty = True
            return
        self.feed.upsert(entry)
        if stats:
            self._update_stats()

    def _update_stats(self) -> None:
        entries = list(self.app.node.activity)
        done = [e for e in entries if e.get("status") in ("done", "error")]
        if not done:
            self.stats.configure(text="")
            return
        ok = sum(1 for e in done if e.get("success") is not False)
        fast = sum(1 for e in done if e.get("fast_path"))
        lat = sorted(e["latency_ms"] for e in done if isinstance(e.get("latency_ms"), (int, float)))
        p50 = f" · p50 {lat[len(lat) // 2]} ms" if lat else ""
        self.stats.configure(text=f"{len(done)} commands · {round(100 * ok / len(done))}% ok · "
                                  f"⚡ {round(100 * fast / len(done))}% fast{p50}")

    def tick(self) -> None:
        if self.built and self.visible:
            self.feed.tick()


class ToolsPage(BasePage):
    def build(self) -> None:
        app, F = self.app, self.app.fonts
        self._cols = 0
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self.scroll = ScrollArea(self, bg=BG, fill_height=False)
        self.scroll.grid(row=0, column=0, sticky="nsew")
        body = tk.Frame(self.scroll.body, bg=BG)
        body.pack(fill="both", expand=True, padx=12, pady=(4, 12))
        body.grid_columnconfigure(0, weight=1)
        head, _right, _ = app.page_header(body, "Tools", "Admin toolbox and hardware details for this PC")
        head.grid(row=0, column=0, sticky="ew", padx=6, pady=(8, 6))
        self.tiles_frame = tk.Frame(body, bg=BG)
        self.tiles_frame.grid(row=1, column=0, sticky="ew")
        tools = (
            ("Admin PowerShell", "Run as administrator", "shell", PURPLE, app.tool_admin_powershell),
            ("Task Manager", "Apps and performance", "pulse", CYAN, app.tool_task_manager),
            ("Flush DNS", "Clear the DNS cache", "globe", SKY, app.tool_flush_dns),
            ("System32 Explorer", "Open the System32 folder", "folder", AMBER, app.tool_system32),
            ("Screenshot", "Save a screen capture", "camera", GREEN, app.tool_screenshot),
            ("Lock PC", "Lock this workstation", "lock", RED, app.tool_lock),
            ("Mute / Unmute", "Toggle system audio", "mute", SOFT_PURPLE, app.tool_toggle_mute),
            ("Battery & Specs", "Quick system summary", "info", CYAN, app.tool_specs_summary),
        )
        self.tiles = [self._tile(self.tiles_frame, *tool) for tool in tools]

        specs = app.card(body)
        specs.grid(row=2, column=0, sticky="ew", padx=6, pady=(10, 6))
        app.card_header(specs, "Hardware & system", "chip")
        self.spec_grid = tk.Frame(specs, bg=CARD)
        self.spec_grid.pack(fill="x", padx=16, pady=(0, 14))
        self.spec_grid.grid_columnconfigure((1, 3), weight=1, uniform="spec")
        self.spec_labels: Dict[str, ctk.CTkLabel] = {}
        fields = (("device", "Device"), ("user", "User"), ("os", "Windows"), ("cpu", "Processor"),
                  ("cores", "Cores"), ("ram", "Memory"), ("gpu", "Graphics"), ("display", "Displays"),
                  ("battery", "Battery"), ("boot", "Booted"), ("id", "Device ID"), ("hub", "Hub"))
        for i, (key, label) in enumerate(fields):
            r, c = i // 2, (i % 2) * 2
            ctk.CTkLabel(self.spec_grid, text=label, font=F(12), text_color=MUTED, anchor="nw", width=84).grid(
                row=r, column=c, sticky="nw", pady=4, padx=(0 if c == 0 else 18, 8))
            value = ctk.CTkLabel(self.spec_grid, text="—", font=F.s(12), text_color=TEXT, anchor="w",
                                 justify="left", wraplength=280)
            value.grid(row=r, column=c + 1, sticky="w", pady=4)
            self.spec_labels[key] = value
        self.scroll.bind("<Configure>", lambda _e: self._layout_tiles(), add="+")
        self.update_specs()

    def _tile(self, parent: tk.Misc, title: str, desc: str, icon: str, accent: str,
              command: Callable[[], None]) -> ctk.CTkFrame:
        app, F = self.app, self.app.fonts
        tile = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=14, border_width=1, border_color=BORDER,
                            cursor="hand2")
        tile.grid_columnconfigure(1, weight=1)
        badge = ctk.CTkLabel(tile, text="", width=40, height=40, corner_radius=12, fg_color=tint(accent, 0.15),
                             image=app.icons.get(icon, 19, accent))
        badge.grid(row=0, column=0, rowspan=2, padx=(14, 12), pady=14, sticky="n")
        name = ctk.CTkLabel(tile, text=title, font=F.s(13), text_color=TEXT, anchor="w", height=20)
        name.grid(row=0, column=1, sticky="sw", pady=(14, 0), padx=(0, 12))
        sub = ctk.CTkLabel(tile, text=desc, font=F(11), text_color=MUTED, anchor="w", justify="left", height=16,
                           wraplength=150)
        sub.grid(row=1, column=1, sticky="nw", pady=(0, 14), padx=(0, 12))
        hover_border = mix(BORDER, accent, 0.55)
        for widget in (tile, badge, name, sub):
            widget.bind("<Button-1>", lambda _e: command(), add="+")
            widget.bind("<Enter>", lambda _e: tile.configure(border_color=hover_border, fg_color=CARD_ALT), add="+")
            widget.bind("<Leave>", lambda _e: tile.configure(border_color=BORDER, fg_color=CARD), add="+")
            try:
                widget.configure(cursor="hand2")
            except (tk.TclError, ValueError):
                pass
        tile.sub = sub  # type: ignore[attr-defined]
        return tile

    def _layout_tiles(self) -> None:
        width = self.scroll.winfo_width() / max(0.5, self.app.scale)
        if width < 100:
            return
        # Spec values wrap inside their half of the card (label column ~84 px + gaps).
        spec_wrap = int(max(140, (width - 24 - 32) / 2 - 84 - 30))
        if spec_wrap != getattr(self, "_spec_wrap", None):
            self._spec_wrap = spec_wrap
            for label in self.spec_labels.values():
                label.configure(wraplength=spec_wrap)
        cols = 4 if width >= 900 else 3 if width >= 660 else 2
        if cols == self._cols:
            return
        self._cols = cols
        for c in range(4):
            self.tiles_frame.grid_columnconfigure(c, weight=1 if c < cols else 0, uniform="tile" if c < cols else "")
        wrap = int((width - 24) / cols - 104)
        for i, tile in enumerate(self.tiles):
            tile.grid(row=i // cols, column=i % cols, sticky="nsew", padx=6, pady=6)
            tile.sub.configure(wraplength=max(100, wrap))  # type: ignore[attr-defined]

    def update_specs(self) -> None:
        if not self.built:
            return
        app = self.app
        s = app.specs or {}
        node = app.node
        gpus = s.get("gpu") or []
        build = str(s.get("os_version") or "").split(".")[-1]
        boot = _num(s.get("boot_time"))
        values = {
            "device": f"{node.device_name}" + (f" ({s['hostname']})" if s.get("hostname") and
                                               s.get("hostname") != node.device_name else ""),
            "user": s.get("user") or "—",
            "os": " · ".join(x for x in (s.get("os"), f"build {build}" if build else "", s.get("machine")) if x) or "—",
            "cpu": s.get("cpu_model") or "—",
            "cores": f"{s.get('cpu_cores') or '?'} cores · {s.get('cpu_threads') or '?'} threads"
            if s.get("cpu_threads") else "—",
            "ram": f"{s.get('ram_total_gb')} GB" if s.get("ram_total_gb") else "—",
            "gpu": "\n".join(gpus) if gpus else ("Detecting…" if s else "—"),
            "display": f"{s.get('monitors')} monitor{'s' if (s.get('monitors') or 0) != 1 else ''} · "
                       f"{s.get('primary_resolution')} primary" if s.get("monitors") else "—",
            "battery": ("Yes — laptop" if s.get("has_battery") else "No — desktop power") if s else "—",
            "boot": time.strftime("%a %d %b, %H:%M", time.localtime(boot)) if boot else "—",
            "id": node.device_id,
            "hub": ellipsize(node.server_url, 48),
        }
        for key, text in values.items():
            self.spec_labels[key].configure(text=text)


class LogPage(BasePage):
    TAGS = {"ok": GREEN, "err": SOFT_RED, "info": CYAN, "pc": SOFT_PURPLE, "ui": SKY, "rem": AMBER, "ts": DIM,
            "plain": SOFT}

    def build(self) -> None:
        app, F = self.app, self.app.fonts
        self._lines = 0
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        head, right, _ = app.page_header(self, "Log", "Connection and action log from the Willy engine "
                                                      f"(last {MAX_LOG_LINES} lines)")
        head.grid(row=0, column=0, sticky="ew", padx=18, pady=(12, 8))
        app.button(right, "Clear", self.clear, icon="delete", kind="ghost", height=32).pack(side="right")
        app.button(right, "Copy", self.copy, icon="copy", kind="secondary", height=32).pack(side="right", padx=(0, 8))
        self.follow = ctk.CTkSwitch(right, text="Auto-scroll", font=F(12), text_color=MUTED, progress_color=CYAN,
                                    button_color=TEXT, button_hover_color=SOFT, fg_color=BORDER_HI,
                                    switch_width=34, switch_height=18)
        self.follow.select()
        self.follow.pack(side="right", padx=(0, 14))
        box = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=14, border_width=1, border_color=BORDER)
        box.grid(row=1, column=0, sticky="nsew", padx=18, pady=(0, 14))
        box.grid_rowconfigure(0, weight=1)
        box.grid_columnconfigure(0, weight=1)
        self.text = tk.Text(box, bg=SURFACE, fg=SOFT, font=F.tk(12, F.mono), relief="flat", bd=0,
                            highlightthickness=0, wrap="word", padx=app.px(8), pady=app.px(6),
                            spacing1=app.px(2), insertbackground=TEXT, selectbackground=tint(CYAN, 0.30, SURFACE),
                            selectforeground=TEXT, inactiveselectbackground=tint(CYAN, 0.22, SURFACE))
        self.text.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=8)
        bar = ThinScrollbar(box, self.text.yview, SURFACE, app.scale)
        bar.grid(row=0, column=1, sticky="ns", padx=(2, 6), pady=12)
        self.text.configure(yscrollcommand=bar.set)
        for tag, color in self.TAGS.items():
            self.text.tag_configure(tag, foreground=color)
        for ts, line in list(app.log_lines):
            self._insert(ts, line)
        self.text.configure(state="disabled")
        self._scroll()

    @staticmethod
    def _tag_for(line: str) -> str:
        head = line[:12]
        if head.startswith("[+]"):
            return "ok"
        if head.startswith("[-]") or "error" in line.lower()[:40]:
            return "err"
        if head.startswith("[*]"):
            return "info"
        if head.startswith("[PC]"):
            return "pc"
        if head.startswith("[UI]"):
            return "ui"
        if head.startswith("[Reminder]"):
            return "rem"
        return "plain"

    def _insert(self, ts: str, line: str) -> None:
        self.text.insert("end", f"{ts}  ", "ts")
        self.text.insert("end", line + "\n", self._tag_for(line))
        self._lines += 1

    def append(self, ts: str, line: str) -> None:
        if not self.built:
            return
        self.text.configure(state="normal")
        self._insert(ts, line)
        if self._lines > MAX_LOG_LINES:
            extra = self._lines - MAX_LOG_LINES
            self.text.delete("1.0", f"{extra + 1}.0")
            self._lines = MAX_LOG_LINES
        self.text.configure(state="disabled")
        if self.visible:
            self._scroll()

    def _scroll(self) -> None:
        if self.follow.get():
            self.text.see("end")

    def on_show(self) -> None:
        self._scroll()

    def clear(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")
        self._lines = 0
        self.app.log_lines.clear()

    def copy(self) -> None:
        self.app.copy_text("\n".join(f"{ts}  {line}" for ts, line in self.app.log_lines))


class HistoryChart(tk.Canvas):
    """A resource-history line chart on a plain Tk canvas: time on x (the chosen range, ending at
    the newest sample), one or more series, gaps where the server sent nothing, and a hover
    line with the values under the pointer."""

    def __init__(self, master: tk.Misc, app: "WillyDesktopApp", series: Sequence[Tuple[str, str, str]], *,
                 fmt: Callable[[float], str], y_max: Optional[float] = 100.0, height: int = 120, bg: str = CARD):
        super().__init__(master, bg=bg, highlightthickness=0, bd=0, height=app.px(height))
        self.app = app
        self.series = list(series)   # (key, label, colour)
        self.fmt = fmt
        self.y_max = y_max
        self.bg = bg
        self.hours = 24.0
        self.points: List[Dict[str, Any]] = []
        self.empty_text = "Loading…"
        self.hover_text = ""
        self._geom: Optional[Tuple[float, float, float, float, float, float, float]] = None
        self._hover_x: Optional[int] = None
        self.bind("<Configure>", lambda _e: self.render())
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda _e: self._hide_hover())

    def set_data(self, points: Any, hours: float, empty_text: str = "") -> None:
        self.points = sorted((p for p in points or [] if isinstance(p, dict) and _num(p.get("t")) is not None),
                             key=lambda p: float(p["t"]))
        self.hours = float(hours)
        self.empty_text = empty_text
        self.render()

    def _ymax(self) -> float:
        if self.y_max:
            return self.y_max
        peak = max((v for key, _l, _c in self.series for _t, v in history_values(self.points, key) if v is not None),
                   default=0.0)
        return nice_ceiling(max(peak * 1.1, 10.0))

    def _time_label(self, t: float) -> str:
        return time.strftime("%a %d %b" if self.hours > 48 else "%a %H:%M" if self.hours > 24 else "%H:%M",
                             time.localtime(t))

    def render(self) -> None:
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        px, F = self.app.px, self.app.fonts
        left, right, top, bottom = px(58), px(10), px(8), px(20)
        pw, ph = w - left - right, h - top - bottom
        self._geom = None
        if pw < 30 or ph < 20:
            return
        ymax = self._ymax()
        for i in range(3):
            y = top + ph * i / 2
            self.create_line(left, y, left + pw, y, fill=GRID if i < 2 else BORDER)
            self.create_text(left - px(6), y, anchor="e", text=self.fmt(ymax * (1 - i / 2)), font=F.tk(10), fill=DIM)
        if not self.points:
            self.create_text(left + pw / 2, top + ph / 2, text=self.empty_text or "No data yet.", font=F.tk(11),
                             fill=MUTED)
            return
        t1 = float(self.points[-1]["t"])
        t0 = min(float(self.points[0]["t"]), t1 - self.hours * 3600)
        span = max(60.0, t1 - t0)
        self._geom = (left, top, pw, ph, t0, span, ymax)
        for i, anchor in enumerate(("w", "center", "center", "e")):
            frac = i / 3
            self.create_text(left + pw * frac, h - px(8), anchor=anchor, text=self._time_label(t0 + span * frac),
                             font=F.tk(10), fill=DIM)
        gap = history_gap([float(p["t"]) for p in self.points])
        width = max(1, px(2))
        for key, _label, color in reversed(self.series):
            for seg in history_segments(history_values(self.points, key), gap):
                xy = [(self._x(t), self._y(v)) for t, v in seg]
                if len(xy) == 1:
                    x, y = xy[0]
                    self.create_oval(x - width, y - width, x + width, y + width, fill=color, outline="")
                    continue
                if len(self.series) == 1:  # a soft area under a lone series
                    self.create_polygon(*[c for pt in xy for c in pt], xy[-1][0], top + ph, xy[0][0], top + ph,
                                        fill=tint(color, 0.14, self.bg), outline="")
                self.create_line(*[c for pt in xy for c in pt], fill=color, width=width)
        if self._hover_x is not None:
            self._draw_hover(self._hover_x)

    def _x(self, t: float) -> float:
        left, _top, pw, _ph, t0, span, _ymax = self._geom  # type: ignore[misc]
        return left + (t - t0) / span * pw

    def _y(self, v: float) -> float:
        _left, top, _pw, ph, _t0, _span, ymax = self._geom  # type: ignore[misc]
        return top + ph - min(max(v, 0.0), ymax) / ymax * ph

    def _motion(self, event: Any) -> None:
        self._hover_x = event.x
        self._draw_hover(event.x)

    def _hide_hover(self) -> None:
        self._hover_x = None
        self.hover_text = ""
        self.delete("hover")

    def _draw_hover(self, x: float) -> None:
        self.delete("hover")
        self.hover_text = ""
        g = self._geom
        if g is None or not self.points:
            return
        left, top, pw, ph, t0, span, _ymax = g
        if not left <= x <= left + pw:
            return
        t = t0 + (x - left) / pw * span
        p = min(self.points, key=lambda q: abs(float(q["t"]) - t))
        if abs(float(p["t"]) - t) > max(history_gap([float(q["t"]) for q in self.points]), span / pw * 6):
            return  # nothing was recorded around there
        px, F = self.app.px, self.app.fonts
        hx = self._x(float(p["t"]))
        self.create_line(hx, top, hx, top + ph, fill=BORDER_HI, dash=(3, 3), tags="hover")
        parts = [time.strftime("%a %H:%M" if self.hours > 24 else "%H:%M", time.localtime(float(p["t"])))]
        for key, label, color in self.series:
            v = _num(p.get(key))
            parts.append(f"{label} {self.fmt(v) if v is not None else '—'}")
            if v is not None:
                y = self._y(max(0.0, v))
                r = px(3.5)
                self.create_oval(hx - r, y - r, hx + r, y + r, fill=color, outline=self.bg, tags="hover")
        self.hover_text = "  ·  ".join(parts)
        right_side = hx < left + pw * 0.6
        tid = self.create_text(hx + (px(10) if right_side else -px(10)), top + px(10),
                               anchor="w" if right_side else "e", text=self.hover_text, font=F.tk(11), fill=TEXT,
                               tags="hover")
        x0, y0, x1, y1 = self.bbox(tid)
        rid = self.create_rectangle(x0 - px(6), y0 - px(3), x1 + px(6), y1 + px(3), fill=SURFACE, outline=BORDER_HI,
                                    tags="hover")
        self.tag_raise(tid, rid)


def _own_wheel(widget: Any) -> None:
    """The mouse wheel over a list or text box inside a ScrollArea scrolls only that box."""
    def wheel(event: Any) -> str:
        widget.yview_scroll(-3 if event.delta > 0 else 3, "units")
        return "break"

    widget.bind("<MouseWheel>", wheel)


class ServerPage(BasePage):
    """The Willy server (the Linux machine running the hub), in six tabs:

    - Overview: the hub's server monitor (GET /api/v1/server/status) — problems (mute / unmute),
      resources, pm2 apps (logs, restart), systemd services and Docker containers (logs,
      restart, stop / start) and the websites nginx serves. Re-read every SERVER_REFRESH_SEC
      while visible; websites are only re-checked on "Check sites now". The resource meters
      follow the server device's live heartbeats (device events) when its agent is connected.
    - Files: browse the server and open, download, rename, copy, move, zip, unzip, delete
      (to ~/.willy-trash); upload a PC file (it lands in ~/willy-inbox).
    - History: CPU / memory / swap / disk and network graphs over 1 h – 7 d from the hub's
      minute samples (GET /api/v1/server/history), re-read every minute while visible.
    - System, in sections: Updates (check, install security / all, reboot), Storage (disks,
      biggest folders, cleanups), Network & security (interfaces, listening ports, SSH
      logins), Services & jobs (every systemd service with controls and its boot setting,
      cron and timers) and Logs (the system journal). Anything that changes the server asks
      first.
    - Files: browse the server and open, download, rename, copy, move, zip, unzip, delete
      (to ~/.willy-trash); upload a PC file (it lands in ~/willy-inbox).
    - Terminal: run a bash command on the server (pressing Run is the confirmation).
    - Processes: the top 15 by memory or CPU.

    Hub REST calls run in the worker pool (hub_json); server actions go through the hub like
    phone actions (node.send_action, with a longer wait for the slow admin actions — see
    server_action_request) and come back on the Tk thread. Widgets are updated in place; tabs
    other than Overview (and System sections) are built the first time they're opened."""

    METERS = (("cpu", "CPU", CYAN, 75, 90), ("ram", "Memory", SOFT_PURPLE, 80, 92),
              ("disk", "Disk", SKY, 80, 90), ("swap", "Swap", GREEN, 50, 80))
    SUBTITLE = "Your Willy server's health — apps, services and websites"
    TABS = ("Overview", "History", "System", "Files", "Terminal", "Processes")
    SECTIONS = ("Updates", "Storage", "Network & security", "Services & jobs", "Logs")
    # what each System section reads: (state key, agent action)
    SECTION_LOADS = {"Updates": (("updates", "sys_updates"),), "Storage": (("storage", "storage"),),
                     "Network & security": (("network", "network"), ("security", "security")),
                     "Services & jobs": (("services", "services_list"), ("timers", "timers")),
                     "Logs": (("services", "services_list"),)}
    GRAPHS = (("cpu", "CPU", CYAN, "chip"), ("ram", "Memory", SOFT_PURPLE, "memory"),
              ("swap", "Swap", GREEN, "memory"), ("disk", "Disk", SKY, "disk"))

    def build(self) -> None:
        app, F, px = self.app, self.app.fonts, self.app.px
        self._status: Optional[Dict[str, Any]] = None  # the last good answer
        self._error = ""
        self._offline = False
        self._fetching = 0          # status reads in flight (without a website check)
        self._sites_busy = False    # a "Check sites now" in flight
        self._manual = 0            # Refresh clicks in flight (the button waits for them)
        self._seq = self._ok_seq = 0
        self._fetch_at = -1e9       # monotonic start of the last status read
        self._ok_at: Optional[float] = None
        self._muting: set = set()
        self._restarting: set = set()
        self._controlling: set = set()  # (kind, name) of service / container actions in flight
        self._shown: Dict[str, Any] = {}
        self._orders: Dict[str, List[str]] = {}
        self._app_rows: Dict[str, Dict[str, Any]] = {}
        self._site_rows: Dict[str, Dict[str, Any]] = {}
        self._problem_sig: Any = None
        self._svc_sig: Any = None
        self._cols = 0
        self._wrap = 0
        self._cards_on = False
        self._banner_on = False
        self._job: Optional[str] = None
        self._text_dialog: Optional[ServerTextDialog] = None
        self._prompt: Optional[PromptDialog] = None
        # the server device (live telemetry, actions)
        self._server: Optional[Dict[str, Any]] = None
        self._server_id: Optional[str] = None
        self._dev_dirty = True
        self._ready: Optional[bool] = None
        self._ready_sig: Any = None
        # tabs
        self._tab = "Overview"
        self._tabs: Dict[str, tk.Misc] = {}
        self._files_path: Optional[str] = None  # None until the first listing
        self._files_parent: Optional[str] = None
        self._files_entries: List[Dict[str, Any]] = []
        self._files_note = ""
        self._files_busy = False
        self._files_seq = 0
        self._files_width = 0
        self._files_job: Optional[str] = None
        self._history: List[str] = []
        self._hist_i = 0
        self._term_busy = False
        self._term_lines = 0
        self._procs: List[Dict[str, Any]] = []
        self._procs_sort = "memory"
        self._procs_busy = False
        self._procs_at = -1e9
        self._procs_ok_at: Optional[float] = None
        self._procs_error = ""
        self._proc_rows: List[Dict[str, tk.Label]] = []
        # history graphs
        self._graph_hours = 24
        self._graph_points: List[Dict[str, Any]] = []
        self._graph_busy = False
        self._graph_seq = 0
        self._graph_at = -1e9
        self._graph_ok_at: Optional[float] = None
        self._graph_error = ""
        self._graph_shown_hours = 24  # the range of the points on screen
        # system sections: one state per agent read
        self._section = "Updates"
        self._sections: Dict[str, tk.Misc] = {}
        self._sys: Dict[str, Dict[str, Any]] = {
            key: {"data": None, "busy": False, "error": "", "at": None, "asked": -1e9, "tried": False}
            for key in ("updates", "storage", "network", "security", "services", "timers", "journal")}
        self._applying: Optional[str] = None     # "security" / "all" while updates install
        self._apply_output = ""
        self._reboot_needed: Optional[bool] = None  # from the last install (newer than the last check)
        self._reboot_busy = False
        self._reboot_at: Optional[float] = None  # when a reboot we scheduled happens
        self._cleaning: set = set()
        self._booting: set = set()                # services whose boot setting is changing
        self._svc_rows: List[Dict[str, Any]] = []  # the services list as shown (filtered)
        self._svc_selected: Optional[str] = None
        self._svc_job: Optional[str] = None
        self._sys_sigs: Dict[str, Any] = {}

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        # --- header: name, state chip, last check, Refresh / Check sites now
        head, right, self.subtitle = app.page_header(self, "Server", self.SUBTITLE)
        head.grid(row=0, column=0, sticky="ew", padx=18, pady=(12, 6))
        self.sites_btn = app.button(right, "Check sites now", self.check_sites, icon="globe", kind="outline",
                                    height=32)
        self.sites_btn.pack(side="right")
        self.refresh_btn = app.button(right, "Refresh", self.refresh, icon="refresh", height=32)
        self.refresh_btn.pack(side="right", padx=(0, 8))
        state = tk.Frame(right, bg=BG)
        state.pack(side="right", padx=(0, px(14)))
        self.chip = ctk.CTkLabel(state, text="●  Checking…", font=F(11, "bold"), corner_radius=11, height=22,
                                 padx=10, text_color=DIM, fg_color=tint(DIM, 0.16, BG))
        self.chip.pack(anchor="e")
        self.checked = self._label(state, "", F.tk(11), DIM, bg=BG)
        self.checked.pack(anchor="e", pady=(px(2), 0))

        self.tabbar = ctk.CTkSegmentedButton(self, values=list(self.TABS), command=self.show_tab, height=32,
                                             font=F(12), fg_color=CARD, selected_color=tint(CYAN, 0.28, CARD),
                                             selected_hover_color=tint(CYAN, 0.36, CARD), unselected_color=CARD,
                                             unselected_hover_color=CARD_ALT, text_color=TEXT)
        self.tabbar.set("Overview")
        self.tabbar.grid(row=1, column=0, sticky="w", padx=18, pady=(0, 8))

        # --- offline / error banner (shown over data that's still on screen)
        banner_bg = tint(AMBER, 0.10, BG)
        self.banner = ctk.CTkFrame(self, fg_color=banner_bg, corner_radius=12, border_width=1,
                                   border_color=tint(AMBER, 0.40, BG))
        self.banner.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(self.banner, text="", image=app.icons.get("error", 16, AMBER), width=18,
                     fg_color=banner_bg).grid(row=0, column=0, padx=(14, 10), pady=10)
        self.banner_text = self._label(self.banner, "", F.tk(12, F.semi), SOFT, bg=banner_bg, justify="left")
        self.banner_text.grid(row=0, column=1, sticky="w", pady=px(10))
        app.button(self.banner, "Try again", self.refresh, icon="refresh", height=28).grid(
            row=0, column=2, padx=(10, 12), pady=8)

        # --- overview tab
        self.scroll = ScrollArea(self, bg=BG, fill_height=False)
        self.scroll.grid(row=3, column=0, sticky="nsew")
        self._tabs["Overview"] = self.scroll
        self.body = body = tk.Frame(self.scroll.body, bg=BG)
        body.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        body.grid_columnconfigure((0, 1), weight=1, uniform="server")

        # empty state (before the first answer, or when the hub was never reached)
        self.empty = tk.Frame(body, bg=BG)
        ctk.CTkLabel(self.empty, text="", image=app.icons.get("server", 40, DIM), width=48, height=48).pack(
            pady=(px(40), px(10)))
        self.empty_title = self._label(self.empty, "Checking your server…", F.tk(15, F.semi), SOFT, bg=BG,
                                       anchor="center")
        self.empty_title.pack()
        self.empty_text = self._label(self.empty, "", F.tk(12), MUTED, bg=BG, anchor="center", justify="center",
                                      wraplength=px(460))
        self.empty_text.pack(pady=(px(4), px(12)))
        self.empty_retry = app.button(self.empty, "Try again", self.refresh, icon="refresh", height=32)

        # problems: active ones with Mute, muted ones listed small with Unmute
        self.problems_card = app.card(body)
        slot = app.card_header(self.problems_card, "Problems", "error")
        self.problems_note = self._label(slot, "", F.tk(11), DIM)
        self.problems_note.pack(side="right")
        self.problems_box = tk.Frame(self.problems_card, bg=CARD)
        self.problems_box.pack(fill="x", padx=16, pady=(0, 12))
        self.problems_box.grid_columnconfigure(1, weight=1)

        # resources
        self.res_card = app.card(body)
        slot = app.card_header(self.res_card, "Resources", "health")
        self.res_note = self._label(slot, "", F.tk(11, F.semi), DIM)
        self.res_note.pack(side="right")
        meters = tk.Frame(self.res_card, bg=CARD)
        meters.pack(fill="x", padx=16, pady=(0, 4))
        meters.grid_columnconfigure(1, weight=1)
        meters.grid_columnconfigure(3, minsize=px(118))
        self.meters: Dict[str, Dict[str, Any]] = {}
        for i, (key, title, accent, warn, crit) in enumerate(self.METERS):
            self._label(meters, title, F.tk(12), MUTED).grid(row=i, column=0, sticky="w", padx=(0, px(12)),
                                                           pady=px(6))
            bar = ctk.CTkProgressBar(meters, width=60, height=8, corner_radius=4, border_width=0, fg_color=BORDER,
                                     progress_color=accent)
            bar.set(0)
            bar.grid(row=i, column=1, sticky="ew")
            value = self._label(meters, "—", F.tk(12, F.semi), TEXT, anchor="e", width=5)
            value.grid(row=i, column=2, sticky="e", padx=(px(10), 0))
            detail = self._label(meters, "", F.tk(11), DIM)
            detail.grid(row=i, column=3, sticky="w", padx=(px(10), 0))
            self.meters[key] = {"bar": bar, "value": value, "detail": detail, "accent": accent, "warn": warn,
                                "crit": crit}
        foot = tk.Frame(self.res_card, bg=CARD)
        foot.pack(fill="x", padx=16, pady=(4, 14))
        self.load_label = self._label(foot, "", F.tk(11), MUTED)
        self.load_label.pack(side="left")
        self.uptime_label = self._label(foot, "", F.tk(11), MUTED)
        self.uptime_label.pack(side="right")

        # services & containers
        self.svc_card = app.card(body)
        app.card_header(self.svc_card, "Services & containers", "gear")
        self.svc_box = tk.Frame(self.svc_card, bg=CARD)
        self.svc_box.pack(fill="x", padx=16, pady=(0, 14))
        self.svc_box.grid_columnconfigure(0, weight=1)

        # pm2 apps
        self.apps_card = app.card(body)
        slot = app.card_header(self.apps_card, "Apps", "apps", note="pm2")
        self.apps_note = self._label(slot, "", F.tk(11), DIM)
        self.apps_note.pack(side="right")
        self.apps_box = tk.Frame(self.apps_card, bg=CARD)
        self.apps_box.pack(fill="x", padx=16, pady=(0, 12))
        self.apps_box.grid_columnconfigure(1, weight=1)
        self.apps_head = self._table_head(self.apps_box, ("", "APP", "STATUS", "UPTIME", "MEMORY", "CPU",
                                                          "RESTARTS"), pad_from=2)
        self.apps_empty = self._label(self.apps_box, "No pm2 apps reported by the server.", F.tk(12), MUTED)

        # websites
        self.sites_card = app.card(body)
        slot = app.card_header(self.sites_card, "Websites", "globe")
        self.sites_note = self._label(slot, "", F.tk(11), DIM)
        self.sites_note.pack(side="right")
        self.sites_box = tk.Frame(self.sites_card, bg=CARD)
        self.sites_box.pack(fill="x", padx=16, pady=(0, 12))
        self.sites_box.grid_columnconfigure(0, weight=1)
        self.sites_head = self._table_head(self.sites_box, ("WEBSITE", "STATUS", "RESPONSE", "CERTIFICATE"),
                                           pad_from=1)
        self.sites_empty = self._label(self.sites_box, "No websites found in the server's nginx config.",
                                       F.tk(12), MUTED)

        self.scroll.bind("<Configure>", lambda _e: self._layout(), add="+")
        self.render()

    # ------------------------------------------------------------ widgets

    def _label(self, parent: tk.Misc, text: str, font: Any, color: str, bg: str = CARD, **kw: Any) -> tk.Label:
        """A plain Tk text label (one native window, unlike a CTkLabel)."""
        opts: Dict[str, Any] = dict(anchor="w", bd=0, padx=0, pady=0, highlightthickness=0)
        opts.update(kw)
        return tk.Label(parent, text=text, font=font, fg=color, bg=bg, **opts)

    def _chip(self, parent: tk.Misc, text: str, color: str) -> ctk.CTkLabel:
        return ctk.CTkLabel(parent, text=text, font=self.app.fonts(11, "bold"), corner_radius=9, height=20, padx=8,
                            text_color=color, fg_color=tint(color, 0.16))

    def _table_head(self, parent: tk.Misc, titles: Sequence[str], pad_from: int) -> List[tk.Label]:
        """Column titles in row 0 (columns from `pad_from` on get the cells' 12 px gap)."""
        F, px = self.app.fonts, self.app.px
        cells = []
        for column, title in enumerate(titles):
            cell = self._label(parent, title, F.tk(10, F.semi), DIM)
            cell.grid(row=0, column=column, sticky="w", padx=(px(12) if column >= pad_from else 0, 0),
                      pady=(0, px(4)))
            cells.append(cell)
        return cells

    def _put(self, widget: Any, **opts: Any) -> None:
        """Reconfigures a widget only when these options changed (fg for Tk labels,
        text_color for CTk widgets)."""
        key = str(widget)
        sig = tuple(sorted(opts.items()))
        if self._shown.get(key) != sig:
            self._shown[key] = sig
            widget.configure(**opts)

    def _clear(self, box: tk.Misc) -> None:
        for widget in box.winfo_children():
            self._shown.pop(str(widget), None)
            widget.destroy()

    def _sync_rows(self, name: str, rows: Dict[str, Dict[str, Any]], keys: List[str],
                   make: Callable[[str], Dict[str, Any]]) -> None:
        """Creates rows for new keys, destroys rows of keys that are gone, and re-grids the rows
        (below the header row) when the set or the order changed."""
        changed = False
        for key in [k for k in rows if k not in keys]:
            for widget, _column, _opts in rows.pop(key)["cells"]:
                self._shown.pop(str(widget), None)
                widget.destroy()
            changed = True
        for key in keys:
            if key not in rows:
                rows[key] = make(key)
                changed = True
        if changed or self._orders.get(name) != keys:
            self._orders[name] = list(keys)
            for i, key in enumerate(keys, start=1):
                for widget, column, opts in rows[key]["cells"]:
                    widget.grid(row=i, column=column, **opts)

    def _make_app_row(self, key: str, name: str) -> Dict[str, Any]:
        app, F, px = self.app, self.app.fonts, self.app.px
        box = self.apps_box
        cells: List[Tuple[Any, int, Dict[str, Any]]] = []
        row: Dict[str, Any] = {"cells": cells, "name": name}

        def cell(widget: Any, column: int, **opts: Any) -> Any:
            cells.append((widget, column, opts))
            return widget

        row["dot"] = cell(self._label(box, "●", F.tk(12), DIM), 0, sticky="w", padx=(0, px(8)), pady=px(3))
        row["title"] = cell(self._label(box, ellipsize(key, 34), F.tk(12, F.semi), TEXT), 1, sticky="w")
        for column, field in enumerate(("status", "uptime", "memory", "cpu", "restarts"), start=2):
            row[field] = cell(self._label(box, "—", F.tk(12), SOFT), column, sticky="w", padx=(px(12), 0))
        buttons = tk.Frame(box, bg=CARD)
        row["logs"] = app.button(buttons, "Logs", lambda: self.show_app_logs(name), icon="log", height=28,
                                 font_size=11)
        row["logs"].pack(side="left")
        row["restart"] = app.button(buttons, "Restart", lambda: self.restart_app(name), icon="refresh", height=28,
                                    font_size=11)
        row["restart"].pack(side="left", padx=(6, 0))
        cell(buttons, 7, sticky="e", padx=(px(12), 0), pady=px(3))
        return row

    def _make_site_row(self, domain: str) -> Dict[str, Any]:
        app, F, px = self.app, self.app.fonts, self.app.px
        box = self.sites_box
        cells: List[Tuple[Any, int, Dict[str, Any]]] = []
        row: Dict[str, Any] = {"cells": cells}
        url = site_url(domain)
        font = F.tk(12, F.semi)
        link = self._label(box, ellipsize(domain, 44), font, SKY if url else TEXT)
        if url:
            link.configure(cursor="hand2")
            link.bind("<Button-1>", lambda _e: self.open_site(domain))
            link.bind("<Enter>", lambda _e: link.configure(font=font + ("underline",)))
            link.bind("<Leave>", lambda _e: link.configure(font=font))
        row["link"] = link
        cells.append((link, 0, dict(sticky="w", pady=px(4))))
        row["chip"] = self._chip(box, "—", DIM)
        cells.append((row["chip"], 1, dict(sticky="w", padx=(12, 0))))
        row["ms"] = self._label(box, "—", F.tk(12), SOFT)
        cells.append((row["ms"], 2, dict(sticky="w", padx=(px(12), 0))))
        row["cert"] = self._label(box, "—", F.tk(12), SOFT)
        cells.append((row["cert"], 3, dict(sticky="w", padx=(px(12), 0))))
        row["mute"] = app.button(box, "Mute", lambda: self._toggle_site(domain), icon="mute", height=26,
                                 font_size=11)
        cells.append((row["mute"], 4, dict(sticky="e", padx=(12, 0), pady=2)))
        return row

    @property
    def text_dialog(self) -> ServerTextDialog:
        if self._text_dialog is None:
            self._text_dialog = ServerTextDialog(self.app)
        return self._text_dialog

    @property
    def prompt(self) -> PromptDialog:
        if self._prompt is None:
            self._prompt = PromptDialog(self.app)
        return self._prompt

    # ------------------------------------------------------------ lifecycle

    def on_show(self) -> None:
        if time.monotonic() - self._fetch_at > 5:
            self.fetch()
        self.sync_device()
        self.render()
        self._tab_shown()

    def tick(self) -> None:
        """Every second while visible: relative times, and the periodic re-reads."""
        if not self.built or not self.visible:
            return
        if self._dev_dirty:
            self.sync_device()
        if not self._fetching and time.monotonic() - self._fetch_at >= SERVER_REFRESH_SEC:
            self.fetch()
        now = time.monotonic()
        if self._tab == "Processes" and self._ready and not self._procs_busy and \
                now - self._procs_at >= SERVER_PROCESS_REFRESH_SEC:
            self.procs_refresh()
        elif self._tab == "History" and not self._graph_busy and now - self._graph_at >= SERVER_GRAPH_REFRESH_SEC:
            self.graph_refresh()
        elif self._tab == "System" and self._section == "Network & security" and self._ready and \
                not self._sys["network"]["busy"] and now - self._sys["network"]["asked"] >= SERVER_NETWORK_REFRESH_SEC:
            self.sys_load("network")
        self._render_times()

    def on_device(self, dev: Dict[str, Any]) -> None:
        """A hub device event: the server's heartbeats re-render the live parts (only marks
        the page dirty while it's hidden)."""
        if self.built and isinstance(dev, dict) and dev.get("device_type") == "server":
            self.sync_device()

    def sync_device(self) -> None:
        """Re-reads the server device from node.devices (copied: the node thread replaces it)."""
        if not self.built:
            return
        if not self.visible:
            self._dev_dirty = True
            return
        self._dev_dirty = False
        server = pick_server(dict(self.app.node.devices), self._server_id)
        self._server = server
        self._server_id = server.get("device_id") if server else None
        self._render_ready()
        if self._status is not None:
            self._render_resources(self._status.get("snapshot") or {})
            self._render_header()

    def _reachable(self) -> bool:
        return bool(self._server) and device_online(self._server) and bool(self.app.node.connected)

    def _not_ready_text(self) -> str:
        if not self.app.node.connected:
            return "This PC isn't connected to the hub right now — reconnecting…"
        if self._server is None:
            return ("The server agent isn't connected to the hub, so files, the terminal, processes and "
                    "service controls are unavailable. Start server_agent/agent.py with pm2 on the server.")
        return "The server agent is offline — it reconnects on its own. Files, terminal and controls wait for it."

    def show_tab(self, name: str) -> None:
        if name not in self.TABS or not self.built:
            return
        if self.tabbar.get() != name:
            self.tabbar.set(name)
        frame = self._tabs.get(name)
        if frame is None:
            builders = {"History": self._build_graphs, "System": self._build_system, "Files": self._build_files,
                        "Terminal": self._build_terminal, "Processes": self._build_procs}
            frame = self._tabs[name] = builders[name]()
            self._ready_sig = None  # the new tab's controls need their state
        for other, widget in self._tabs.items():
            if other != name:
                widget.grid_remove()
        frame.grid(row=3, column=0, sticky="nsew")
        self._tab = name
        self._render_ready()
        self._tab_shown()

    def _tab_shown(self) -> None:
        if not self.visible:
            return
        if self._tab == "Files" and self._files_path is None and self._ready and not self._files_busy:
            self.files_open("")
        elif self._tab == "Processes" and self._ready and time.monotonic() - self._procs_at > 5:
            self.procs_refresh()
        elif self._tab == "Terminal":
            self.term_input.focus_set()
        elif self._tab == "History" and time.monotonic() - self._graph_at > 5:
            self.graph_refresh()
        elif self._tab == "System":
            self._section_shown()

    def _layout(self, width: Optional[float] = None) -> None:
        """Resources | services side by side when wide (one column when narrow); problem
        messages wrap to the width."""
        width = width or self.scroll.winfo_width() / max(0.5, self.app.scale)
        if width < 100:
            return
        cols = 2 if width >= 960 else 1  # a services row needs ~400 px with its buttons
        if cols != self._cols and self._cards_on:
            self._cols = cols
            self.res_card.grid(row=1, column=0, columnspan=1 if cols == 2 else 2, sticky="nsew", padx=6, pady=6)
            self.svc_card.grid(row=1 if cols == 2 else 2, column=1 if cols == 2 else 0,
                               columnspan=1 if cols == 2 else 2, sticky="nsew", padx=6, pady=6)
            self.apps_card.grid(row=3, column=0, columnspan=2, sticky="ew", padx=6, pady=6)
            self.sites_card.grid(row=4, column=0, columnspan=2, sticky="ew", padx=6, pady=6)
        wrap = int(max(220, width - 24 - 32 - 140))  # card margins and paddings, dot, Mute button
        if wrap != self._wrap:
            self._wrap = wrap
            self.banner_text.configure(wraplength=self.app.px(max(200, width - 220)))
            if self._status is not None:
                self._render_problems(self._status)

    # ------------------------------------------------------------ hub calls

    def refresh(self) -> None:
        if not self._manual:
            self.fetch(manual=True, force=True)

    def check_sites(self) -> None:
        self.fetch(sites=True)

    def fetch(self, sites: bool = False, manual: bool = False, force: bool = False) -> None:
        """Reads the status in the worker pool; sites=True also re-checks every website (slow)."""
        if not self.built or self.app.closing:
            return
        if sites:
            if self._sites_busy:
                return
            self._sites_busy = True
        else:
            if self._fetching and not force:
                return
            self._fetching += 1
            self._manual += manual
        self._fetch_at = time.monotonic()
        self._seq += 1
        seq = self._seq
        path = f"/api/v1/server/status?refresh={'true' if sites else 'false'}"
        self.app.run_bg(hub_json, "GET", path, None, SERVER_SITES_TIMEOUT_SEC if sites else 20.0,
                        on_done=lambda res: self._got_status(seq, sites, manual, res, None),
                        on_error=lambda e: self._got_status(seq, sites, manual, None, e))
        self._render_header()

    def _got_status(self, seq: int, sites: bool, manual: bool, res: Any, err: Optional[Exception]) -> None:
        if sites:
            self._sites_busy = False
        else:
            self._fetching = max(0, self._fetching - 1)
            self._manual = max(0, self._manual - manual)
        if err is None and isinstance(res, dict):
            if not isinstance(res.get("snapshot"), dict):
                res = {**res, "snapshot": {}}
            self._status = res
            self._ok_seq = max(self._ok_seq, seq)
            self._ok_at = time.time()
            if self._error:
                self.app.local_log("[UI] Server page: the Willy server is reachable again")
            self._error, self._offline = "", False
            if sites:
                listed = [s for s in res["snapshot"].get("sites") or [] if isinstance(s, dict)]
                up = sum(1 for s in listed if s.get("up"))
                self.app.toast("Websites checked", f"{up} of {len(listed)} website{'s' if len(listed) != 1 else ''}"
                               " up." if listed else "No websites to check.",
                               kind="success" if up == len(listed) else "error", duration=4)
        else:
            if err is None:
                err = HubError("The hub sent an unexpected answer.")
            if isinstance(err, HubError) and err.status == 404:
                message = "This Willy server doesn't have the server monitor yet — update the hub."
            elif isinstance(err, HubError):
                message = str(err)
            else:
                message = f"Couldn't read the server status: {err}"
            if seq > self._ok_seq:  # an older failure never hides a newer good answer
                if message != self._error:
                    self.app.local_log(f"[-] Server page: {message}")
                self._error, self._offline = message, bool(getattr(err, "offline", False))
            if sites or manual:
                self.app.toast("Websites" if sites else "Server", message, kind="error")
        self.render()

    def _schedule_fetch(self, ms: int) -> None:
        if self._job is not None:
            self.after_cancel(self._job)

        def run() -> None:
            self._job = None
            if self.visible:
                self.fetch(force=True)

        self._job = self.after(ms, run)

    def _action(self, action: str, payload: Dict[str, Any], on_result: Callable[[Dict[str, Any]], None]) -> None:
        """Sends an action to the server device through the hub; on_result gets the result
        dict on the Tk thread (failures as {"success": False, "error": ...})."""
        node, device_id = self.app.node, self._server_id
        if not device_id:
            on_result({"success": False, "error": "DEVICE_OFFLINE"})
            return
        fut = node.submit(server_action_request(node, device_id, action, payload))
        if fut is None:
            on_result({"success": False, "error": "The Willy engine isn't running."})
            return

        def done(f: Any) -> None:
            try:
                res = f.result()
            except Exception as e:  # noqa: BLE001 - shown to the user
                res = {"success": False, "error": str(e)}
            on_result(res if isinstance(res, dict) else {"success": False, "error": "The server sent an "
                                                                                     "unexpected reply."})

        fut.add_done_callback(lambda f: self.app.post(done, f))

    def _need_ready(self, label: str) -> bool:
        if self._reachable():
            return True
        self.app.toast(label, self._not_ready_text(), kind="error")
        return False

    # ------------------------------------------------------------ overview actions

    def show_app_logs(self, name: str) -> None:
        path = f"/api/v1/server/apps/{urllib.parse.quote(name, safe='')}/logs?lines={SERVER_LOG_LINES}"

        def load(done: Callable[..., None]) -> None:
            self.app.run_bg(hub_json, "GET", path, None, 30.0,
                            on_done=lambda res: done(str((res or {}).get("logs") or "")),
                            on_error=lambda e: done(None, str(e) if isinstance(e, HubError)
                                                    else f"Couldn't load the logs: {e}"))

        self.text_dialog.show(f"{name} · logs", load)

    def restart_app(self, name: str) -> None:
        if name in self._restarting:
            return
        self.app.confirm.ask(f"Restart {name}?", restart_warning(name), "Restart", lambda: self._restart(name))

    def _restart(self, name: str) -> None:
        self._restarting.add(name)
        self.app.local_log(f"[UI] Restarting server app {name}")
        self._render_apps()
        path = f"/api/v1/server/apps/{urllib.parse.quote(name, safe='')}/restart"
        self.app.run_bg(hub_json, "POST", path, {}, 60.0, on_done=lambda res: self._restarted(name, res, None),
                        on_error=lambda e: self._restarted(name, None, e))

    def _restarted(self, name: str, res: Any, err: Optional[Exception]) -> None:
        self._restarting.discard(name)
        if err is None:
            message = str((res or {}).get("message") or f"Restarted {name}.")
            self.app.toast(f"Restart {name}", message, kind="success")
            self.app.local_log(f"[UI] Restart {name}: {message}")
        else:
            message = str(err) if isinstance(err, HubError) else f"The restart failed: {err}"
            self.app.toast(f"Restart {name}", message, kind="error")
            self.app.local_log(f"[-] Restart {name}: {message}")
        self._render_apps()
        # Re-read once it's back up (the hub restarts itself after answering, so give it longer).
        self._schedule_fetch(12000 if name == "willy-server" else 4000)

    def control(self, kind: str, name: str, action: str) -> None:
        """Restart / stop / start a systemd service or Docker container (asks first)."""
        if (kind, name) in self._controlling or not self._need_ready(f"{action.capitalize()} {name}"):
            return
        what = "service" if kind == "service" else "container"
        message = {"restart": f"The {what} {name} restarts and is unavailable for a moment.",
                   "stop": f"The {what} {name} stops until you start it again.",
                   "start": f"Starts the {what} {name} on the server."}[action]
        if kind == "service" and name == "nginx" and action in ("stop", "restart"):
            message += " Every website on the server is down while nginx is " + \
                       ("stopped." if action == "stop" else "restarting.")
        verb = action.capitalize()
        self.app.confirm.ask(f"{verb} {name}?", message, verb, lambda: self._control(kind, name, action),
                             danger=action != "start")

    def _control(self, kind: str, name: str, action: str) -> None:
        self._controlling.add((kind, name))
        self.app.local_log(f"[UI] Server: {action} {kind} {name}")
        self._rerender_services()
        self._render_svc_actions()
        self._action("control", {"kind": kind, "name": name, "action": action},
                     lambda res: self._controlled(kind, name, action, res))

    def _controlled(self, kind: str, name: str, action: str, res: Dict[str, Any]) -> None:
        self._controlling.discard((kind, name))
        label = f"{action.capitalize()} {name}"
        if action_ok(res):
            message = str(res.get("message") or "Done.")
            self.app.toast(label, message, kind="success")
            self.app.local_log(f"[UI] {label}: {message}")
        else:
            message = server_action_error(res, wait=SERVER_ACTION_WAIT_SEC["control"])
            self.app.toast(label, message, kind="error")
            self.app.local_log(f"[-] {label}: {message}")
        self._rerender_services()
        self._render_svc_actions()
        self._schedule_fetch(3000)
        if kind == "service" and self._sys["services"]["data"] is not None:
            self.sys_load("services")  # the System tab's list shows the new state

    def control_logs(self, kind: str, name: str) -> None:
        if not self._need_ready(f"{name} logs"):
            return

        def load(done: Callable[..., None]) -> None:
            self._action("control", {"kind": kind, "name": name, "action": "logs", "lines": SERVER_LOG_LINES},
                         lambda res: done(str(res.get("stdout") or "")) if action_ok(res)
                         else done(None, server_action_error(res)))

        self.text_dialog.show(f"{name} · logs", load)

    def set_muted(self, key: str, mute: bool) -> None:
        """Mutes (or unmutes) a problem on the hub: no alerts for it, and it leaves the list."""
        if key in self._muting:
            return
        self._muting.add(key)
        self.render()
        self.app.run_bg(hub_json, "POST", "/api/v1/server/ignore", {"key": key, "ignore": mute}, 20.0,
                        on_done=lambda res: self._muted(key, mute, res, None),
                        on_error=lambda e: self._muted(key, mute, None, e))

    def _muted(self, key: str, mute: bool, res: Any, err: Optional[Exception]) -> None:
        self._muting.discard(key)
        label = server_problem_label(key)
        if err is None:
            if self._status is not None:
                ignored = res.get("ignored") if isinstance(res, dict) else None
                if not isinstance(ignored, list):
                    before = set(self._status.get("ignored") or [])
                    ignored = sorted(before | {key} if mute else before - {key})
                problems = [p for p in self._status.get("problems") or []
                            if not (mute and isinstance(p, dict) and p.get("key") == key)]
                self._status = {**self._status, "ignored": ignored, "problems": problems}
            self.app.toast("Server alerts", f"Muted: {label}. Willy won't alert about it." if mute else
                           f"Unmuted: {label}. Willy alerts about it again.", kind="success", duration=3.5)
            self.fetch(force=True)  # an unmuted problem that's still there comes back
        else:
            self.app.toast("Server alerts", str(err), kind="error")
        self.render()

    def _toggle_site(self, domain: str) -> None:
        key = f"site:{domain}"
        ignored = (self._status or {}).get("ignored") or []
        self.set_muted(key, key not in ignored)

    def open_site(self, domain: str) -> None:
        url = site_url(domain)
        if url:
            self.app.run_bg(webbrowser.open, url)

    # ------------------------------------------------------------ overview render

    def render(self) -> None:
        if not self.built:
            return
        self._render_header()
        status = self._status
        if self._error and status is not None:
            self._put(self.banner_text, text=f"{self._error} Showing what it reported {fmt_ago(self._ok_at)}.")
            if not self._banner_on:
                self._banner_on = True
                self.banner.grid(row=2, column=0, sticky="ew", padx=18, pady=(0, 8))
        elif self._banner_on:
            self._banner_on = False
            self.banner.grid_remove()
        if status is None:
            if self._error:
                title = "Can't reach your Willy server" if self._offline else "Server status unavailable"
                text = self._error + (" Willy keeps trying every 30 seconds." if self._offline else "")
            else:
                title, text = "Checking your server…", ""
            self._put(self.empty_title, text=title)
            self._put(self.empty_text, text=text)
            if self._error:
                if not self.empty_retry.winfo_manager():
                    self.empty_retry.pack(pady=(0, 20))
            else:
                self.empty_retry.pack_forget()
            if not self.empty.winfo_manager():
                self.empty.grid(row=0, column=0, columnspan=2, sticky="ew")
            return
        if not self._cards_on:
            self._cards_on = True
            self.empty.grid_remove()
            self._cols = 0
            self._layout()
            if not self._cols:  # not laid out yet: a sensible first layout; <Configure> refines it
                self._layout(1000)
        snap = status.get("snapshot") or {}
        self._render_problems(status)
        self._render_resources(snap)
        self._render_services(snap)
        self._render_apps()
        self._render_sites()
        self._render_times()

    def _system(self, snap: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
        """The server's resources: the last check, overlaid with live heartbeat values while
        the server agent is online. Returns (system, live)."""
        live = server_live_system(self._server) if self._server and device_online(self._server) else {}
        base = snap.get("system") if isinstance(snap.get("system"), dict) else {}
        return {**base, **{k: v for k, v in live.items() if v is not None}}, bool(live)

    def _render_header(self) -> None:
        status = self._status
        snap = (status or {}).get("snapshot") or {}
        system, _live = self._system(snap)
        host = system.get("hostname")
        name = snap.get("name") or (self._server or {}).get("name") or "Willy Server"
        self._put(self.subtitle, text=" · ".join(str(x) for x in (name, host) if x) if status else self.SUBTITLE)
        if status is None:
            text, color = ("Unreachable", RED) if self._error and self._offline else \
                ("Unavailable", RED) if self._error else ("Checking…", DIM)
        elif self._error and self._offline:
            text, color = "Unreachable", RED
        else:
            text, color = server_state(status)
        self._put(self.chip, text=f"●  {text}", text_color=color, fg_color=tint(color, 0.16, BG))
        self._put(self.sites_btn, text="Checking sites…" if self._sites_busy else "Check sites now",
                  state="disabled" if self._sites_busy else "normal")
        self._put(self.refresh_btn, state="disabled" if self._manual else "normal")
        self._render_times()

    def _render_times(self) -> None:
        snap = (self._status or {}).get("snapshot") or {}
        now = time.time()
        if self._sites_busy:
            checked = "Checking every website…"
        elif self._fetching and self._status is None:
            checked = "Loading…"
        elif snap.get("checked_at"):
            checked = f"Checked {fmt_ago(snap.get('checked_at'), now)}"
        else:
            checked = ""
        self._put(self.checked, text=checked)
        if self._status is not None:
            at = _num(snap.get("sites_checked_at"))
            self._put(self.sites_note, text="Checking now…" if self._sites_busy else
                      f"Checked {fmt_ago(at, now)}" if at else "")
            if self._error:
                self._put(self.banner_text, text=f"{self._error} Showing what it reported {fmt_ago(self._ok_at, now)}.")
        if "Processes" in self._tabs:
            if self._procs_busy:
                text = "Loading…"
            elif self._procs_error:
                text = ellipsize(self._procs_error, 60)
            else:
                text = f"Updated {fmt_ago(self._procs_ok_at, now)}" if self._procs_ok_at else ""
            self._put(self.p_status, text=text, fg=SOFT_RED if self._procs_error and not self._procs_busy else DIM)
        if "History" in self._tabs:
            self._render_graph_status(now)
        if "System" in self._tabs:
            self._render_sys_status(now)

    def _render_problems(self, status: Dict[str, Any]) -> None:
        problems = server_problems(status)
        ignored = [str(k) for k in status.get("ignored") or [] if k]
        if not problems and not ignored:
            self.problems_card.grid_remove()
            self._problem_sig = None
            return
        self.problems_card.grid(row=0, column=0, columnspan=2, sticky="ew", padx=6, pady=6)
        sig = (tuple((p["key"], p["message"]) for p in problems), tuple(ignored), tuple(sorted(self._muting)),
               self._wrap)
        if sig == self._problem_sig:
            return
        self._problem_sig = sig
        app, F, px = self.app, self.app.fonts, self.app.px
        box = self.problems_box
        self._clear(box)
        severe = any(server_problem_severe(p["key"]) for p in problems)
        accent = RED if severe else AMBER
        self.problems_card.configure(border_color=mix(BORDER, accent, 0.45) if problems else BORDER)
        self._put(self.problems_note, text=f"{len(problems)} active" if problems else "All clear")
        row = 0
        for p in problems:
            color = RED if server_problem_severe(p["key"]) else AMBER
            self._label(box, "●", F.tk(12), color).grid(row=row, column=0, sticky="nw", padx=(0, px(10)),
                                                       pady=(px(5), 0))
            self._label(box, p["message"], F.tk(12), TEXT, justify="left", wraplength=px(self._wrap)).grid(
                row=row, column=1, sticky="w", pady=px(4))
            busy = p["key"] in self._muting
            btn = app.button(box, "Muting…" if busy else "Mute", lambda k=p["key"]: self.set_muted(k, True),
                             icon="mute", height=26, font_size=11)
            btn.grid(row=row, column=2, sticky="e", padx=(10, 0), pady=2)
            if busy:
                btn.configure(state="disabled")
            row += 1
        if not problems:
            self._label(box, "Nothing needs your attention right now.", F.tk(12), MUTED).grid(
                row=row, column=0, columnspan=3, sticky="w", pady=px(2))
            row += 1
        if ignored:
            self._label(box, "MUTED · no alerts for these", F.tk(10, F.semi), DIM).grid(
                row=row, column=0, columnspan=3, sticky="w", pady=(px(10) if row else 0, px(2)))
            row += 1
            for key in ignored:
                self._label(box, server_problem_label(key), F.tk(11), MUTED).grid(
                    row=row, column=0, columnspan=2, sticky="w", pady=px(1))
                busy = key in self._muting
                btn = app.button(box, "Unmuting…" if busy else "Unmute", lambda k=key: self.set_muted(k, False),
                                 kind="ghost", height=22, font_size=11)
                btn.grid(row=row, column=2, sticky="e", padx=(10, 0))
                if busy:
                    btn.configure(state="disabled")
                row += 1

    def _render_resources(self, snap: Dict[str, Any]) -> None:
        s, live = self._system(snap)
        self._put(self.res_note, text="●  Live" if live else ("From the last check" if s else ""),
                  fg=GREEN if live else DIM)
        used, total, free = _num(s.get("ram_used_mb")), _num(s.get("ram_total_mb")), _num(s.get("disk_free_gb"))
        disk_total = _num(s.get("disk_total_gb"))
        cores = s.get("cores")
        values = {
            "cpu": (_num(s.get("cpu_pct")), f"{cores} cores" if cores else ""),
            "ram": (_num(s.get("ram_pct")), f"{fmt_mem(used)} of {fmt_mem(total)}" if used is not None and total
                    else ""),
            "disk": (_num(s.get("disk_pct")), (f"{free:.1f} GB free" + (f" of {disk_total:.0f}" if disk_total
                                                                          else "")) if free is not None else ""),
            "swap": (_num(s.get("swap_pct")), ""),
        }
        for key, (pct, detail) in values.items():
            m = self.meters[key]
            color = _level_color(pct, m["accent"], m["warn"], m["crit"])
            self._put(m["bar"], progress_color=color)
            level = max(0.0, min(1.0, (pct or 0.0) / 100.0))
            if self._shown.get(f"{m['bar']}.level") != level:
                self._shown[f"{m['bar']}.level"] = level
                m["bar"].set(level)
            self._put(m["value"], text=f"{pct:.0f}%" if pct is not None else "—",
                      fg=color if color in (RED, AMBER) else TEXT)
            self._put(m["detail"], text=detail)
        load = s.get("load")
        self._put(self.load_label, text=f"Load  {fmt_load(load)}   (1 · 5 · 15 min)" if load else "")
        uptime, procs = _num(s.get("uptime_sec")), _num(s.get("process_count"))
        parts = [f"Up {fmt_duration(uptime)}" if uptime else "", f"{int(procs)} processes" if procs else ""]
        self._put(self.uptime_label, text=" · ".join(p for p in parts if p))

    def _rerender_services(self) -> None:
        self._svc_sig = None
        if self._status is not None:
            self._render_services(self._status.get("snapshot") or {})

    def _render_services(self, snap: Dict[str, Any]) -> None:
        services = snap.get("services") if isinstance(snap.get("services"), dict) else {}
        ctrs = [c for c in snap.get("containers") or [] if isinstance(c, dict) and c.get("name")]
        ready = bool(self._ready)
        sig = (tuple((str(k), str(v)) for k, v in services.items()),
               tuple((str(c["name"]), str(c.get("state") or ""), str(c.get("status") or "")) for c in ctrs),
               ready, tuple(sorted(self._controlling)))
        if sig == self._svc_sig:
            return
        self._svc_sig = sig
        app, F, px = self.app, self.app.fonts, self.app.px
        box = self.svc_box
        self._clear(box)
        row = 0

        def section(title: str) -> None:
            nonlocal row
            self._label(box, title, F.tk(10, F.semi), DIM).grid(row=row, column=0, columnspan=3, sticky="w",
                                                              pady=(px(8) if row else 0, px(2)))
            row += 1

        def item(kind: str, name: str, sub: str, state: str, color: str, running: bool) -> None:
            nonlocal row
            cell = tk.Frame(box, bg=CARD)
            cell.grid(row=row, column=0, sticky="ew", pady=px(3))
            self._label(cell, ellipsize(name, 22), F.tk(12, F.semi), TEXT).pack(anchor="w")
            if sub:
                self._label(cell, ellipsize(sub, 26), F.tk(11), DIM).pack(anchor="w")
            self._chip(box, state, color).grid(row=row, column=1, sticky="e", padx=(8, 0))
            busy = (kind, name) in self._controlling
            buttons = tk.Frame(box, bg=CARD)
            buttons.grid(row=row, column=2, sticky="e", padx=(px(10), 0))
            for text, command in (("Logs", lambda: self.control_logs(kind, name)),
                                  ("Restart", lambda: self.control(kind, name, "restart")),
                                  ("Stop" if running else "Start",
                                   lambda: self.control(kind, name, "stop" if running else "start"))):
                btn = app.button(buttons, "…" if busy and text != "Logs" else text, command, height=24,
                                 font_size=11)
                btn.pack(side="left", padx=(4, 0))
                if not ready or busy:
                    btn.configure(state="disabled")
            row += 1

        if services:
            section("SERVICES")
            for name, state in services.items():
                state = str(state or "unknown")
                item("service", str(name), "", state, service_color(state), state == "active")
        if ctrs:
            section("DOCKER CONTAINERS")
            for c in ctrs:
                state = str(c.get("state") or "unknown")
                item("container", str(c["name"]), str(c.get("status") or ""), state, container_color(state),
                     state in ("running", "restarting"))
        if not services and not ctrs:
            self._label(box, "No systemd services or Docker containers reported.", F.tk(12), MUTED).grid(
                row=0, column=0, columnspan=3, sticky="w", pady=px(2))

    def _render_apps(self) -> None:
        if self._status is None:
            return
        snap = self._status.get("snapshot") or {}
        pairs = server_app_keys(snap.get("apps"))
        keys = [k for k, _a in pairs]
        self._sync_rows("apps", self._app_rows, keys,
                        lambda k: self._make_app_row(k, str(dict(pairs)[k]["name"])))
        for cell in self.apps_head:
            if keys and not cell.winfo_manager():
                cell.grid()
            elif not keys:
                cell.grid_remove()
        if keys:
            self.apps_empty.grid_remove()
        else:
            self.apps_empty.grid(row=1, column=0, columnspan=8, sticky="w", pady=self.app.px(4))
        online = 0
        for key, a in pairs:
            row = self._app_rows[key]
            status = str(a.get("status") or "unknown")
            color = app_status_color(status)
            online += color == GREEN
            cpu, restarts = _num(a.get("cpu")), _num(a.get("restarts"))
            self._put(row["dot"], fg=color)
            self._put(row["status"], text=status, fg=SOFT if color == GREEN else color)
            self._put(row["uptime"], text=fmt_duration(a.get("uptime_sec")) if color == GREEN else "—")
            self._put(row["memory"], text=fmt_mem(a.get("memory_mb")))
            self._put(row["cpu"], text=f"{cpu:.0f}%" if cpu is not None else "—")
            self._put(row["restarts"], text=str(int(restarts)) if restarts is not None else "—",
                      fg=AMBER if (restarts or 0) >= 10 else SOFT)
            busy = row["name"] in self._restarting
            self._put(row["restart"], text="Restarting…" if busy else "Restart",
                      state="disabled" if busy else "normal")
        self._put(self.apps_note, text=f"{online} of {len(pairs)} online" if pairs else "")

    def _render_sites(self) -> None:
        if self._status is None:
            return
        snap = self._status.get("snapshot") or {}
        sites = sort_sites(snap.get("sites"))
        ignored = set(str(k) for k in self._status.get("ignored") or [])
        keys = [str(s["domain"]) for s in sites]
        self._sync_rows("sites", self._site_rows, keys, self._make_site_row)
        for cell in self.sites_head:
            if keys and not cell.winfo_manager():
                cell.grid()
            elif not keys:
                cell.grid_remove()
        if keys:
            self.sites_empty.grid_remove()
        else:
            self.sites_empty.grid(row=1, column=0, columnspan=5, sticky="w", pady=self.app.px(4))
        for site in sites:
            domain = str(site["domain"])
            row = self._site_rows[domain]
            text, color = site_chip(site)
            self._put(row["chip"], text=text, text_color=color, fg_color=tint(color, 0.16))
            if site.get("up") or site.get("status"):
                self._put(row["ms"], text=fmt_ms(site.get("ms")), fg=SOFT)
            else:
                self._put(row["ms"], text=ellipsize(site.get("error") or "No answer", 26), fg=SOFT_RED)
            days = site.get("cert_days")
            self._put(row["cert"], text=cert_text(days), fg=cert_color(days) if days is not None else DIM)
            key = f"site:{domain}"
            muted = key in ignored
            if muted or not site.get("up"):
                busy = key in self._muting
                self._put(row["mute"], text=("Unmuting…" if muted else "Muting…") if busy else
                          ("Unmute" if muted else "Mute"), state="disabled" if busy else "normal",
                          image=self.app.icons.get("bell" if muted else "mute", 14, SOFT))
                if not row["mute"].winfo_manager():
                    row["mute"].grid()
            else:
                row["mute"].grid_remove()

    def _render_ready(self) -> None:
        """Enables the server-agent controls (files, terminal, processes, service buttons)
        while the agent is reachable; otherwise each tab says why not."""
        ready = self._reachable()
        hint = "" if ready else self._not_ready_text()
        sig = (ready, hint, tuple(self._tabs))
        if sig == self._ready_sig:
            return
        self._ready_sig = sig
        changed = ready != self._ready
        self._ready = ready
        if "Files" in self._tabs:
            self._put(self.f_hint, text=hint)
            if hint:
                self.f_hint.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 8))
            else:
                self.f_hint.grid_remove()
            self._files_buttons()
            self._put(self.f_upload, state="normal" if ready else "disabled")
            for widget in (self.f_places, self.f_refresh):
                self._put(widget, state="normal" if ready else "disabled")
        if "Terminal" in self._tabs:
            self._put(self.t_hint, text=hint or "Ctrl+Enter runs · Ctrl+↑ / Ctrl+↓ for earlier commands · "
                                                f"commands stop after {SERVER_SHELL_TIMEOUT_SEC} s",
                      fg=AMBER if hint else DIM)
            self._put(self.t_run, state="normal" if ready and not self._term_busy else "disabled")
        if "Processes" in self._tabs:
            self._put(self.p_hint, text=hint)
            if hint:
                self.p_hint.pack(fill="x", padx=16, pady=(0, 8), before=self.p_table)
            else:
                self.p_hint.pack_forget()
            self._put(self.p_refresh, state="normal" if ready else "disabled")
        if "System" in self._tabs:
            self._put(self.s_hint, text=hint)
            if hint:
                self.s_hint.grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 8))
            else:
                self.s_hint.grid_remove()
            self._render_sys_controls()
        if changed:
            self._rerender_services()
            if ready:
                self._tab_shown()  # e.g. the Files tab was opened before the agent connected

    # ------------------------------------------------------------ files tab

    def _build_files(self) -> tk.Misc:
        app, F, px = self.app, self.app.fonts, self.app.px
        tab = tk.Frame(self, bg=BG)
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=1)
        card = app.card(tab)
        card.grid(row=0, column=0, sticky="nsew", padx=18, pady=(0, 14))
        card.grid_columnconfigure(0, weight=1)
        card.grid_rowconfigure(3, weight=1)

        bar = tk.Frame(card, bg=CARD)
        bar.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 8))
        bar.grid_columnconfigure(3, weight=1)
        self.f_up = app.button(bar, "Up", self.files_up, icon="chevron_up", height=30)
        self.f_up.grid(row=0, column=0, padx=(0, 6))
        self.f_places = app.button(bar, "Places", lambda: self.files_open(""), icon="folder", height=30)
        self.f_places.grid(row=0, column=1, padx=(0, 6))
        self.f_refresh = app.button(bar, "", self.files_refresh, icon="refresh", height=30)
        self.f_refresh.grid(row=0, column=2, padx=(0, 8))
        self.f_path = ctk.CTkEntry(bar, height=30, font=F(12, family=F.mono), fg_color=SURFACE,
                                   border_color=BORDER)
        self.f_path.grid(row=0, column=3, sticky="ew")
        self.f_path.bind("<Return>", lambda _e: self.files_open(self.f_path.get().strip()))

        acts = tk.Frame(card, bg=CARD)
        acts.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 8))
        self.f_buttons: Dict[str, ctk.CTkButton] = {}
        for key, text, icon, command in (
                ("open", "Open", "folder", self.files_activate), ("download", "Download", "download",
                                                                  self.files_download),
                ("rename", "Rename", None, self.files_rename), ("copy", "Copy to…", "copy", self.files_copy),
                ("move", "Move to…", None, self.files_move), ("zip", "Zip", None, self.files_zip),
                ("unzip", "Unzip", None, self.files_unzip), ("delete", "Delete", "delete", self.files_delete)):
            btn = app.button(acts, text, command, icon=icon, height=28, font_size=11)
            btn.pack(side="left", padx=(0, 6))
            self.f_buttons[key] = btn
        self.f_upload = app.button(acts, "Upload from PC", self.files_upload, icon="send", kind="outline", height=28,
                                   font_size=11)
        self.f_upload.pack(side="right")
        mkdir = app.button(acts, "New folder", self.files_mkdir, icon="folder", height=28, font_size=11)
        mkdir.pack(side="right", padx=(0, 6))
        self.f_buttons["mkdir"] = mkdir

        self.f_hint = self._label(card, "", F.tk(12, F.semi), AMBER, justify="left", wraplength=px(640))

        box = ctk.CTkFrame(card, fg_color=SURFACE, corner_radius=10, border_width=1, border_color=BORDER)
        box.grid(row=3, column=0, sticky="nsew", padx=16)
        box.grid_rowconfigure(0, weight=1)
        box.grid_columnconfigure(0, weight=1)
        self._mono = tkfont.Font(root=self, family=F.mono, size=-px(12))
        self.f_list = tk.Listbox(box, bg=SURFACE, fg=SOFT, font=self._mono, selectbackground=tint(CYAN, 0.30, SURFACE),
                                 selectforeground=TEXT, activestyle="none", highlightthickness=0, bd=0, relief="flat",
                                 exportselection=False, selectmode="browse")
        self.f_list.grid(row=0, column=0, sticky="nsew", padx=(px(8), 0), pady=px(8))
        scrollbar = ThinScrollbar(box, self.f_list.yview, SURFACE, app.scale)
        scrollbar.grid(row=0, column=1, sticky="ns", padx=(2, 6), pady=10)
        self.f_list.configure(yscrollcommand=scrollbar.set)
        self.f_list.bind("<<ListboxSelect>>", lambda _e: self._files_buttons())
        self.f_list.bind("<Double-Button-1>", lambda _e: self.files_activate())
        self.f_list.bind("<Return>", lambda _e: self.files_activate())
        self.f_list.bind("<BackSpace>", lambda _e: self.files_up())
        self.f_list.bind("<Delete>", lambda _e: self.files_delete())
        self.f_list.bind("<Configure>", lambda _e: self._files_queue_render(), add="+")

        self.f_status = self._label(card, "", F.tk(11), DIM)
        self.f_status.grid(row=4, column=0, sticky="ew", padx=16, pady=(6, 12))
        self._files_buttons()
        return tab

    def _files_selected(self) -> Optional[Dict[str, Any]]:
        if "Files" not in self._tabs:
            return None
        sel = self.f_list.curselection()
        if not sel or sel[0] >= len(self._files_entries):
            return None
        return self._files_entries[sel[0]]

    def _files_buttons(self) -> None:
        if "Files" not in self._tabs:
            return
        entry, ready = self._files_selected(), bool(self._ready)
        at_top = not self._files_path  # the "places" list: open only
        is_file = bool(entry) and not entry.get("folder")
        enabled = {
            "open": bool(entry), "download": is_file and not at_top,
            "rename": bool(entry) and not at_top, "copy": bool(entry) and not at_top,
            "move": bool(entry) and not at_top, "zip": bool(entry) and not at_top,
            "unzip": is_file and not at_top and str(entry.get("name", "")).lower().endswith(".zip"),
            "delete": bool(entry) and not at_top, "mkdir": not at_top and self._files_path is not None,
        }
        for key, btn in self.f_buttons.items():
            self._put(btn, state="normal" if ready and enabled[key] and not self._files_busy else "disabled")
        self._put(self.f_up, state="normal" if ready and self._files_path else "disabled")

    def _files_queue_render(self) -> None:
        if self._files_job is None:
            self._files_job = self.after(80, self._files_render)

    def _files_render(self) -> None:
        """Fills the list: one monospace line per entry, folders first in blue."""
        self._files_job = None
        width = self.f_list.winfo_width()
        char = max(1, self._mono.measure("0"))
        chars = max(40, int((width - self.app.px(8)) / char)) if width > 1 else 90
        name_w = max(16, chars - 34)
        selected = self.f_list.curselection()
        now = time.time()
        self.f_list.delete(0, "end")
        for i, entry in enumerate(self._files_entries):
            self.f_list.insert("end", file_row_text(entry, name_w, now))
            if entry.get("folder"):
                self.f_list.itemconfigure(i, fg=SKY)
        if selected and selected[0] < len(self._files_entries):
            self.f_list.selection_set(selected[0])
            self.f_list.see(selected[0])
        self._files_width = width
        self._files_buttons()

    def _files_status(self, text: str, color: str = DIM) -> None:
        if "Files" in self._tabs:
            self._put(self.f_status, text=text, fg=color)

    def files_open(self, path: str) -> None:
        if "Files" not in self._tabs or not self._need_ready("Server files"):
            return
        self._files_seq += 1
        seq = self._files_seq
        self._files_busy = True
        self._files_buttons()
        self._files_status(f"Opening {path or 'server folders'}…")
        self._action("list_dir", {"path": path}, lambda res: self._files_listed(seq, path, res))

    def _files_listed(self, seq: int, asked: str, res: Dict[str, Any]) -> None:
        if seq != self._files_seq:
            return  # another folder was opened meanwhile
        self._files_busy = False
        if not action_ok(res):
            self._files_status(server_action_error(res, "Couldn't open that folder."), SOFT_RED)
            self._files_buttons()
            return
        self._files_path = str(res.get("path") or "")
        self._files_parent = res.get("parent")
        self._files_entries = [e for e in res.get("entries") or [] if isinstance(e, dict) and e.get("path")]
        self.f_path.delete(0, "end")
        self.f_path.insert(0, self._files_path)
        self.f_list.selection_clear(0, "end")
        self.f_list.yview_moveto(0)
        self._files_render()
        if not self._files_path:
            drives = [d for d in res.get("drives") or [] if isinstance(d, dict)]
            note = "Server folders" + "".join(f" · {d.get('name')}: {d.get('free_gb')} GB free of "
                                              f"{d.get('total_gb')} GB" for d in drives[:3])
        else:
            folders = sum(1 for e in self._files_entries if e.get("folder"))
            files = len(self._files_entries) - folders
            note = f"{folders} folder{'s' if folders != 1 else ''} · {files} file{'s' if files != 1 else ''}" + \
                   (" · only the first 1000 are listed" if res.get("truncated") else "")
        self._files_note = note
        self._files_status(note)

    def files_refresh(self) -> None:
        self.files_open(self._files_path or "")

    def files_up(self) -> None:
        if self._files_path:
            self.files_open(str(self._files_parent or ""))

    def files_activate(self) -> None:
        entry = self._files_selected()
        if not entry or self._files_busy:
            return
        if entry.get("folder"):
            self.files_open(str(entry["path"]))
        else:
            self.files_view(entry)

    def files_view(self, entry: Dict[str, Any]) -> None:
        path = str(entry["path"])

        def load(done: Callable[..., None]) -> None:
            def got(res: Dict[str, Any]) -> None:
                if not action_ok(res):
                    done(None, server_action_error(res, "Couldn't read that file."))
                elif "content" not in res:
                    done(None, "That's a folder — open it in the list instead.")
                else:
                    done(str(res.get("content") or "") + ("\n\n… only the start of the file is shown."
                                                         if res.get("truncated") else ""))

            self._action("manage_file", {"op": "read", "path": path}, got)

        self.text_dialog.show(str(entry.get("name") or path), load)

    def _file_op(self, op: str, path: str, label: str, destination: str = "") -> None:
        """Runs a manage_file operation and re-lists the folder afterwards."""
        if not self._need_ready(label):
            return
        payload = {"op": op, "path": path}
        if destination:
            payload["destination"] = destination
        self._files_status(f"{label}…")
        self.app.local_log(f"[UI] Server files: {op} {path}" + (f" -> {destination}" if destination else ""))

        def done(res: Dict[str, Any]) -> None:
            if action_ok(res):
                self.app.toast(label, str(res.get("message") or "Done."), kind="success")
                if self._tab == "Files" and self._files_path is not None:
                    self.files_refresh()
            else:
                message = server_action_error(res, "That didn't work.")
                self.app.toast(label, message, kind="error")
                self._files_status(message, SOFT_RED)

        self._action("manage_file", payload, done)

    def files_rename(self) -> None:
        entry = self._files_selected()
        if entry:
            name = str(entry.get("name") or "")
            self.prompt.ask(f"Rename {name}", "New name (in the same folder):", name, "Rename",
                            lambda new: new != name and self._file_op("rename", entry["path"], f"Rename {name}", new),
                            validate=file_name_error)

    def _files_copy_or_move(self, op: str) -> None:
        entry = self._files_selected()
        if entry:
            name = str(entry.get("name") or "")
            verb = "Copy" if op == "copy" else "Move"
            self.prompt.ask(f"{verb} {name} to…", "Folder on the server (a full path, or one inside the home "
                                                  "folder). It's made if it doesn't exist.",
                            self._files_path or "", verb,
                            lambda dest: self._file_op(op, entry["path"], f"{verb} {name}", dest))

    def files_copy(self) -> None:
        self._files_copy_or_move("copy")

    def files_move(self) -> None:
        self._files_copy_or_move("move")

    def files_zip(self) -> None:
        entry = self._files_selected()
        if entry:
            self._file_op("zip", entry["path"], f"Zip {entry.get('name')}")

    def files_unzip(self) -> None:
        entry = self._files_selected()
        if entry:
            self._file_op("unzip", entry["path"], f"Unzip {entry.get('name')}")

    def files_delete(self) -> None:
        entry = self._files_selected()
        if not entry or not self._files_path:
            return
        name = str(entry.get("name") or "")
        self.app.confirm.ask(f"Delete {name}?", f"{'The folder' if entry.get('folder') else 'It'} moves to "
                                                "~/.willy-trash on the server, so you can restore it from there.",
                             "Delete", lambda: self._file_op("delete", entry["path"], f"Delete {name}"))

    def files_mkdir(self) -> None:
        folder = self._files_path
        if not folder:
            return
        self.prompt.ask("New folder", f"In {folder}:", "", "Create",
                        lambda name: self._file_op("mkdir", server_path_join(folder, name), f"New folder {name}"),
                        validate=file_name_error)

    def files_download(self) -> None:
        """Server -> hub (file_to_hub) -> this PC's Downloads\\Willy (file_relay.receive_file)."""
        entry = self._files_selected()
        if not entry or entry.get("folder") or not self._need_ready("Download"):
            return
        from pc_client.tools import file_relay

        name = str(entry.get("name") or "file")
        self._files_status(f"Getting {name} from the server…")

        def saved(res: Any) -> None:
            res = res if isinstance(res, dict) else {}
            if res.get("success"):
                self.app.toast("Download", str(res.get("message") or f"Saved {name}."), kind="success")
                self._files_status(str(res.get("message") or f"Saved {name}."))
            else:
                self.app.toast("Download", str(res.get("error") or f"{name} wasn't saved."), kind="error")
                self._files_status(str(res.get("error") or ""), SOFT_RED)

        def uploaded(res: Dict[str, Any]) -> None:
            meta = res.get("file") if action_ok(res) else None
            if not isinstance(meta, dict) or not meta.get("url"):
                message = server_action_error(res, f"The server couldn't send {name}.") if not action_ok(res) \
                    else f"The server didn't hand over {name}."
                self.app.toast("Download", message, kind="error")
                self._files_status(message, SOFT_RED)
                return
            self.app.run_bg(file_relay.receive_file, {"url": meta["url"], "name": meta.get("name") or name},
                            on_done=saved, on_error=lambda e: saved({"success": False, "error": str(e)}))

        self._action("file_to_hub", {"path": entry["path"]}, uploaded)

    def files_upload(self) -> None:
        """A PC file -> the hub -> the server's ~/willy-inbox (file_relay.send_file)."""
        from tkinter import filedialog

        from pc_client.tools import file_relay

        if not self._need_ready("Upload"):
            return
        chosen = filedialog.askopenfilename(parent=self, title="Upload a file to the server")
        if not chosen or not self._server_id:
            return
        path = Path(chosen)
        self.app.toast("Upload", f"Sending {path.name} to the server…")

        def done(res: Any) -> None:
            res = res if isinstance(res, dict) else {}
            if res.get("success"):
                self.app.toast("Upload", str(res.get("message") or f"Sent {path.name}.") + " It's in ~/willy-inbox.",
                               kind="success")
                if self._tab == "Files" and "willy-inbox" in str(self._files_path or ""):
                    self.files_refresh()
            else:
                self.app.toast("Upload", str(res.get("error") or f"{path.name} wasn't sent."), kind="error")

        self.app.run_bg(file_relay.send_file, path, self._server_id, on_done=done,
                        on_error=lambda e: done({"success": False, "error": f"{path.name} wasn't sent: {e}"}))

    # ------------------------------------------------------------ terminal tab

    def _build_terminal(self) -> tk.Misc:
        app, F, px = self.app, self.app.fonts, self.app.px
        tab = tk.Frame(self, bg=BG)
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=1)
        card = app.card(tab)
        card.grid(row=0, column=0, sticky="nsew", padx=18, pady=(0, 14))
        card.grid_columnconfigure(0, weight=1)
        card.grid_rowconfigure(1, weight=1)

        top = tk.Frame(card, bg=CARD)
        top.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 8))
        self._label(top, "Runs on the server with bash, in your home folder unless you pick another.",
                    F.tk(12), MUTED).pack(side="left")
        app.button(top, "Clear", self.term_clear, icon="delete", kind="ghost", height=28, font_size=11).pack(
            side="right")
        app.button(top, "Copy", self.term_copy, icon="copy", height=28, font_size=11).pack(side="right", padx=(0, 6))

        out = ctk.CTkFrame(card, fg_color=SURFACE, corner_radius=10, border_width=1, border_color=BORDER)
        out.grid(row=1, column=0, sticky="nsew", padx=16)
        out.grid_rowconfigure(0, weight=1)
        out.grid_columnconfigure(0, weight=1)
        self.t_out = tk.Text(out, bg=SURFACE, fg=SOFT, font=F.tk(12, F.mono), relief="flat", bd=0,
                             highlightthickness=0, wrap="char", height=12, padx=px(8), pady=px(6),
                             insertbackground=TEXT, selectbackground=tint(CYAN, 0.30, SURFACE), selectforeground=TEXT,
                             inactiveselectbackground=tint(CYAN, 0.22, SURFACE))
        self.t_out.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=8)
        scrollbar = ThinScrollbar(out, self.t_out.yview, SURFACE, app.scale)
        scrollbar.grid(row=0, column=1, sticky="ns", padx=(2, 6), pady=10)
        self.t_out.configure(yscrollcommand=scrollbar.set)
        for tag, color in (("prompt", CYAN), ("err", SOFT_RED), ("dim", DIM), ("ok", GREEN)):
            self.t_out.tag_configure(tag, foreground=color)
        self.t_out.insert("end", "Type a command below and press Ctrl+Enter. Output shows up here.\n", "dim")
        self.t_out.configure(state="disabled")

        bottom = tk.Frame(card, bg=CARD)
        bottom.grid(row=2, column=0, sticky="ew", padx=16, pady=(10, 0))
        bottom.grid_columnconfigure(1, weight=1)
        self._label(bottom, "Folder", F.tk(12), MUTED).grid(row=0, column=0, sticky="w", padx=(0, px(8)))
        self.t_cwd = ctk.CTkEntry(bottom, height=30, font=F(12, family=F.mono), fg_color=SURFACE, border_color=BORDER,
                                  placeholder_text="~ (home)", placeholder_text_color=DIM)
        self.t_cwd.grid(row=0, column=1, sticky="ew", pady=(0, 6))
        self._label(bottom, "Command", F.tk(12), MUTED).grid(row=1, column=0, sticky="nw", padx=(0, px(8)),
                                                             pady=(px(6), 0))
        entry = ctk.CTkFrame(bottom, fg_color=SURFACE, corner_radius=8, border_width=1, border_color=BORDER)
        entry.grid(row=1, column=1, sticky="ew")
        entry.grid_columnconfigure(0, weight=1)
        self.term_input = tk.Text(entry, bg=SURFACE, fg=TEXT, font=F.tk(12, F.mono), relief="flat", bd=0,
                                  highlightthickness=0, wrap="char", height=2, padx=px(8), pady=px(6),
                                  insertbackground=TEXT, selectbackground=tint(CYAN, 0.30, SURFACE),
                                  undo=True)
        self.term_input.grid(row=0, column=0, sticky="ew", padx=4, pady=3)
        self.term_input.bind("<Control-Return>", lambda _e: (self.term_run(), "break")[1])
        self.term_input.bind("<Control-Up>", lambda _e: (self._term_history(-1), "break")[1])
        self.term_input.bind("<Control-Down>", lambda _e: (self._term_history(1), "break")[1])
        self.t_run = app.button(bottom, "Run", self.term_run, icon="play", kind="primary", height=34, width=96)
        self.t_run.grid(row=1, column=2, sticky="n", padx=(10, 0))
        self.t_hint = self._label(card, "", F.tk(11), DIM, justify="left", wraplength=px(640))
        self.t_hint.grid(row=3, column=0, sticky="ew", padx=16, pady=(6, 12))
        return tab

    def _term_write(self, text: str, tag: Any = ()) -> None:
        self.t_out.configure(state="normal")
        self.t_out.insert("end", text, tag)
        self._term_lines += text.count("\n")
        if self._term_lines > SERVER_TERMINAL_MAX_LINES:
            extra = self._term_lines - SERVER_TERMINAL_MAX_LINES
            self.t_out.delete("1.0", f"{extra + 1}.0")
            self._term_lines = SERVER_TERMINAL_MAX_LINES
        self.t_out.configure(state="disabled")
        self.t_out.see("end")

    def _term_history(self, step: int) -> None:
        if not self._history:
            return
        self._hist_i = max(0, min(len(self._history), self._hist_i + step))
        text = self._history[self._hist_i] if self._hist_i < len(self._history) else ""
        self.term_input.delete("1.0", "end")
        self.term_input.insert("1.0", text)

    def term_run(self) -> None:
        """Runs the typed command on the server (pressing Run / Ctrl+Enter is the confirmation)."""
        command = self.term_input.get("1.0", "end").strip()
        if not command or self._term_busy or not self._need_ready("Terminal"):
            return
        if not self._history or self._history[-1] != command:
            self._history.append(command)
            del self._history[:-SERVER_HISTORY_LIMIT]
        self._hist_i = len(self._history)
        cwd = self.t_cwd.get().strip()
        payload: Dict[str, Any] = {"command": command, "timeout": SERVER_SHELL_TIMEOUT_SEC}
        if cwd:
            payload["cwd"] = cwd
        self.term_input.delete("1.0", "end")
        self._term_write(f"{cwd or '~'} $ ", "dim")
        self._term_write(command + "\n", "prompt")
        self._term_busy = True
        self._put(self.t_run, text="Running…", state="disabled")
        self.app.local_log(f"[UI] Server shell: {ellipsize(command, 80)}")
        self._action("run_shell", payload, self._term_done)

    def _term_done(self, res: Dict[str, Any]) -> None:
        self._term_busy = False
        self._put(self.t_run, text="Run", state="normal" if self._ready else "disabled")
        if "exit_code" in res:
            out, err = str(res.get("stdout") or ""), str(res.get("stderr") or "")
            if out:
                self._term_write(out if out.endswith("\n") else out + "\n")
            if err:
                self._term_write(err if err.endswith("\n") else err + "\n", "err")
            code = res.get("exit_code")
            took = _num(res.get("duration_sec"))
            self._term_write(f"exit {code}" + (f" · {took:.2f} s" if took is not None else "") + "\n\n",
                             "ok" if code == 0 else "err")
        else:
            self._term_write(server_action_error(res, "The command didn't run.") + "\n\n", "err")

    def term_clear(self) -> None:
        self.t_out.configure(state="normal")
        self.t_out.delete("1.0", "end")
        self.t_out.configure(state="disabled")
        self._term_lines = 0

    def term_copy(self) -> None:
        self.app.copy_text(self.t_out.get("1.0", "end").strip())

    # ------------------------------------------------------------ processes tab

    def _build_procs(self) -> tk.Misc:
        app, F, px = self.app, self.app.fonts, self.app.px
        scroll = ScrollArea(self, bg=BG, fill_height=False)
        inner = tk.Frame(scroll.body, bg=BG)
        inner.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        card = app.card(inner)
        card.pack(fill="x", padx=6)
        slot = app.card_header(card, "Top processes", "processes", note=f"top {SERVER_PROCESS_ROWS}")
        self.p_refresh = app.button(slot, "", self.procs_refresh, icon="refresh", kind="secondary", width=30,
                                    height=26)
        self.p_refresh.pack(side="right")
        self.p_sort = ctk.CTkSegmentedButton(slot, values=["Memory", "CPU"], command=self._procs_sort_changed,
                                             height=26, font=F(11), fg_color=CARD,
                                             selected_color=tint(CYAN, 0.28, CARD),
                                             selected_hover_color=tint(CYAN, 0.36, CARD), unselected_color=CARD,
                                             unselected_hover_color=CARD_ALT, text_color=TEXT)
        self.p_sort.set("Memory")
        self.p_sort.pack(side="right", padx=(0, 8))
        self.p_status = self._label(slot, "", F.tk(11), DIM)
        self.p_status.pack(side="right", padx=(0, px(10)))
        self.p_hint = self._label(card, "", F.tk(12, F.semi), AMBER, justify="left", wraplength=px(640))
        self.p_table = tk.Frame(card, bg=CARD)
        self.p_table.pack(fill="x", padx=16, pady=(0, 14))
        self.p_table.grid_columnconfigure(5, weight=1)
        self._table_head(self.p_table, ("PID", "NAME", "USER", "CPU", "MEMORY", "COMMAND"), pad_from=1)
        self.p_empty = self._label(self.p_table, "Loading processes…", F.tk(12), MUTED)
        self.p_empty.grid(row=1, column=0, columnspan=6, sticky="w", pady=px(4))
        return scroll

    def _procs_sort_changed(self, value: str) -> None:
        self._procs_sort = "cpu" if value == "CPU" else "memory"
        self.procs_refresh(force=True)

    def procs_refresh(self, force: bool = False) -> None:
        if "Processes" not in self._tabs or (self._procs_busy and not force) or not self._reachable():
            return
        self._procs_busy = True
        self._procs_at = time.monotonic()
        sort = self._procs_sort
        self._render_times()
        self._action("list_processes", {"limit": SERVER_PROCESS_ROWS, "sort": sort},
                     lambda res: self._procs_done(sort, res))

    def _procs_done(self, sort: str, res: Dict[str, Any]) -> None:
        self._procs_busy = False
        if sort != self._procs_sort:
            return  # the sort changed meanwhile; its own answer follows
        if action_ok(res) and isinstance(res.get("processes"), list):
            self._procs = [p for p in res["processes"] if isinstance(p, dict)][:SERVER_PROCESS_ROWS]
            self._procs_ok_at, self._procs_error = time.time(), ""
        else:
            self._procs_error = server_action_error(res, "Couldn't list the processes.")
        self._render_procs()
        self._render_times()

    def _render_procs(self) -> None:
        F, px = self.app.fonts, self.app.px
        procs = self._procs
        while len(self._proc_rows) < len(procs):
            r = len(self._proc_rows) + 1
            row = {}
            for column, field in enumerate(("pid", "name", "user", "cpu", "memory", "command")):
                font = F.tk(12, F.semi) if field == "name" else F.tk(11, F.mono) if field == "command" else F.tk(12)
                label = self._label(self.p_table, "", font, TEXT if field == "name" else
                                    DIM if field == "command" else SOFT)
                label.grid(row=r, column=column, sticky="w", padx=(px(12) if column else 0, 0), pady=px(2))
                row[field] = label
            self._proc_rows.append(row)
        for i, row in enumerate(self._proc_rows):
            if i >= len(procs):
                for label in row.values():
                    label.grid_remove()
                continue
            p = procs[i]
            cpu, mem = _num(p.get("cpu")), _num(p.get("memory_mb"))
            values = {"pid": str(p.get("pid", "")), "name": ellipsize(p.get("name") or "?", 26),
                      "user": ellipsize(p.get("user") or "", 14), "cpu": f"{cpu:.1f}%" if cpu is not None else "—",
                      "memory": fmt_mem(mem), "command": ellipsize(p.get("command") or "", 64)}
            for field, label in row.items():
                if not label.winfo_manager():
                    label.grid()
                extra = {"fg": RED if (cpu or 0) >= 50 else AMBER if (cpu or 0) >= 20 else SOFT} if field == "cpu" \
                    else {}
                self._put(label, text=values[field], **extra)
        if procs:
            self.p_empty.grid_remove()
        else:
            self._put(self.p_empty, text=self._procs_error or ("Loading processes…" if self._procs_busy
                                                               else "No processes."))
            self.p_empty.grid()

    # ------------------------------------------------------------ shared bits of the new tabs

    def _segmented(self, parent: tk.Misc, values: Sequence[str], command: Callable[[str], None],
                   height: int = 28) -> ctk.CTkSegmentedButton:
        return ctk.CTkSegmentedButton(parent, values=list(values), command=command, height=height,
                                      font=self.app.fonts(11), fg_color=CARD, selected_color=tint(CYAN, 0.28, CARD),
                                      selected_hover_color=tint(CYAN, 0.36, CARD), unselected_color=CARD,
                                      unselected_hover_color=CARD_ALT, text_color=TEXT)

    def _scroll_tab(self, parent: tk.Misc) -> Tuple[ScrollArea, tk.Frame]:
        scroll = ScrollArea(parent, bg=BG, fill_height=False)
        inner = tk.Frame(scroll.body, bg=BG)
        inner.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        return scroll, inner

    def _text_box(self, parent: tk.Misc, height: int, wrap: str = "none") -> Tuple[ctk.CTkFrame, tk.Text]:
        """A read-only monospace text box with a slim scrollbar (tags: err, warn, dim, head, ok)."""
        app, F, px = self.app, self.app.fonts, self.app.px
        box = ctk.CTkFrame(parent, fg_color=SURFACE, corner_radius=10, border_width=1, border_color=BORDER)
        box.grid_rowconfigure(0, weight=1)
        box.grid_columnconfigure(0, weight=1)
        text = tk.Text(box, bg=SURFACE, fg=SOFT, font=F.tk(11, F.mono), relief="flat", bd=0, highlightthickness=0,
                       wrap=wrap, height=height, padx=px(8), pady=px(6), insertbackground=TEXT,
                       selectbackground=tint(CYAN, 0.30, SURFACE), selectforeground=TEXT,
                       inactiveselectbackground=tint(CYAN, 0.22, SURFACE))
        text.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=8)
        bar = ThinScrollbar(box, text.yview, SURFACE, app.scale)
        bar.grid(row=0, column=1, sticky="ns", padx=(2, 6), pady=10)
        text.configure(yscrollcommand=bar.set, state="disabled")
        for tag, color in (("err", SOFT_RED), ("warn", AMBER), ("dim", DIM), ("head", MUTED), ("ok", GREEN)):
            text.tag_configure(tag, foreground=color)
        _own_wheel(text)
        return box, text

    @staticmethod
    def _fill_text(widget: tk.Text, chunks: Sequence[Tuple[str, Any]], end: bool = False) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        for text, tag in chunks:
            widget.insert("end", text, tag)
        widget.configure(state="disabled")
        widget.see("end" if end else "1.0")

    def _fail_text(self, res: Any, action: str, fallback: str) -> str:
        """A failed action's reason (the agent's own message when it gave no error)."""
        if isinstance(res, dict) and not res.get("error") and res.get("message"):
            fallback = str(res["message"])
        return server_action_error(res, fallback, wait=SERVER_ACTION_WAIT_SEC.get(action))

    # ------------------------------------------------------------ history tab

    def _build_graphs(self) -> tk.Misc:
        app, F, px = self.app, self.app.fonts, self.app.px
        scroll, inner = self._scroll_tab(self)
        inner.grid_columnconfigure((0, 1), weight=1, uniform="graphs")
        bar = tk.Frame(inner, bg=BG)
        bar.grid(row=0, column=0, columnspan=2, sticky="ew", padx=6, pady=(0, 4))
        self._label(bar, "Range", F.tk(12), MUTED, bg=BG).pack(side="left", padx=(0, px(8)))
        self.g_range = self._segmented(bar, [label for label, _h in SERVER_GRAPH_RANGES], self._graph_range_changed)
        self.g_range.set(next(label for label, h in SERVER_GRAPH_RANGES if h == self._graph_hours))
        self.g_range.pack(side="left")
        self.g_refresh = app.button(bar, "", lambda: self.graph_refresh(force=True), icon="refresh", kind="secondary",
                                    width=30, height=28)
        self.g_refresh.pack(side="right")
        self.g_status = self._label(bar, "", F.tk(11), DIM, bg=BG)
        self.g_status.pack(side="right", padx=(0, px(10)))
        self.g_charts: Dict[str, Dict[str, Any]] = {}

        def pct(v: float) -> str:
            return f"{v:.0f}%"

        for i, (key, title, color, icon) in enumerate(self.GRAPHS):
            card = app.card(inner)
            card.grid(row=1 + i // 2, column=i % 2, sticky="nsew", padx=6, pady=6)
            slot = app.card_header(card, title, icon)
            now = self._label(slot, "—", F.tk(13, F.semi), color)
            now.pack(side="right")
            chart = HistoryChart(card, app, [(key, title, color)], fmt=pct, y_max=100.0)
            chart.pack(fill="x", padx=8)
            stats = self._label(card, "", F.tk(11), DIM)
            stats.pack(anchor="w", padx=16, pady=(4, 12))
            self.g_charts[key] = {"chart": chart, "stats": stats, "fmt": pct, "keys": [(key, "", now)]}
        card = app.card(inner)
        card.grid(row=3, column=0, columnspan=2, sticky="nsew", padx=6, pady=6)
        slot = app.card_header(card, "Network", "updown", note="download and upload")
        up = self._label(slot, "↑ —", F.tk(12, F.semi), SOFT_PURPLE)
        up.pack(side="right")
        down = self._label(slot, "↓ —", F.tk(12, F.semi), CYAN)
        down.pack(side="right", padx=(0, px(14)))
        chart = HistoryChart(card, app, [("rx_kbps", "↓", CYAN), ("tx_kbps", "↑", SOFT_PURPLE)], fmt=fmt_kbit,
                             y_max=None, height=140)
        chart.pack(fill="x", padx=8)
        stats = self._label(card, "", F.tk(11), DIM)
        stats.pack(anchor="w", padx=16, pady=(4, 12))
        self.g_charts["net"] = {"chart": chart, "stats": stats, "fmt": fmt_kbit,
                                "keys": [("rx_kbps", "↓ ", down), ("tx_kbps", "↑ ", up)]}
        self._render_graphs()
        return scroll

    def _graph_range_changed(self, value: str) -> None:
        hours = dict(SERVER_GRAPH_RANGES).get(value)
        if hours and hours != self._graph_hours:
            self._graph_hours = hours
            self.graph_refresh(force=True)

    def graph_refresh(self, force: bool = False) -> None:
        """Reads the hub's resource history for the chosen range (in the worker pool)."""
        if "History" not in self._tabs or not self.built or self.app.closing or (self._graph_busy and not force):
            return
        self._graph_busy = True
        self._graph_at = time.monotonic()
        self._graph_seq += 1
        seq, hours = self._graph_seq, self._graph_hours
        path = f"/api/v1/server/history?hours={hours}&points={SERVER_GRAPH_POINTS}"
        self.app.run_bg(hub_json, "GET", path, None, 30.0,
                        on_done=lambda res: self._graph_done(seq, hours, res, None),
                        on_error=lambda e: self._graph_done(seq, hours, None, e))
        self._render_graph_status()

    def _graph_done(self, seq: int, hours: int, res: Any, err: Optional[Exception]) -> None:
        if seq != self._graph_seq:
            return  # another range was asked for meanwhile; its answer follows
        self._graph_busy = False
        if err is None and isinstance(res, dict):
            self._graph_points = [p for p in res.get("points") or [] if isinstance(p, dict)]
            self._graph_shown_hours = hours
            self._graph_ok_at, self._graph_error = time.time(), ""
        elif isinstance(err, HubError) and err.status == 404:
            self._graph_error = "This hub keeps no resource history yet — update the hub."
        elif isinstance(err, HubError):
            self._graph_error = str(err)
        else:
            self._graph_error = f"Couldn't read the history: {err}" if err else "The hub sent an unexpected answer."
        self._render_graphs()

    def _render_graphs(self) -> None:
        charts = getattr(self, "g_charts", None)
        if not charts:
            return
        points = self._graph_points
        if points:
            empty = ""
        elif self._graph_error:
            empty = self._graph_error
        elif self._graph_busy or self._graph_ok_at is None:
            empty = "Loading…"
        else:
            empty = "No history yet — the hub records a sample every minute."
        hours = getattr(self, "_graph_shown_hours", self._graph_hours)
        for entry in charts.values():
            entry["chart"].set_data(points, hours, empty)
            parts = []
            for key, prefix, label in entry["keys"]:
                pairs = history_values(points, key)
                last = next((v for _t, v in reversed(pairs) if v is not None), None)
                self._put(label, text=prefix + (entry["fmt"](last) if last is not None else "—"))
                text = history_stats_text(pairs, entry["fmt"])
                if text:
                    parts.append(prefix + text)
            self._put(entry["stats"], text="        ".join(parts))
        self._render_graph_status()

    def _render_graph_status(self, now: Optional[float] = None) -> None:
        if not getattr(self, "g_charts", None):
            return
        if self._graph_busy:
            text, color = "Loading…", DIM
        elif self._graph_error:
            text, color = ellipsize(self._graph_error, 70), SOFT_RED
        elif self._graph_ok_at:
            text, color = f"Updated {fmt_ago(self._graph_ok_at, now)} · re-read every minute", DIM
        else:
            text, color = "", DIM
        self._put(self.g_status, text=text, fg=color)
        self._put(self.g_refresh, state="disabled" if self._graph_busy else "normal")

    # ------------------------------------------------------------ system tab

    def _build_system(self) -> tk.Misc:
        app, F, px = self.app, self.app.fonts, self.app.px
        tab = tk.Frame(self, bg=BG)
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(2, weight=1)
        bar = tk.Frame(tab, bg=BG)
        bar.grid(row=0, column=0, sticky="ew", padx=18, pady=(0, 8))
        self.s_tabbar = self._segmented(bar, self.SECTIONS, self.show_section)
        self.s_tabbar.set(self._section)
        self.s_tabbar.pack(side="left")
        self.s_refresh = app.button(bar, "Refresh", self.section_refresh, icon="refresh", height=28, font_size=11)
        self.s_refresh.pack(side="right")
        self.s_status = self._label(bar, "", F.tk(11), DIM, bg=BG)
        self.s_status.pack(side="right", padx=(0, px(10)))
        self.s_hint = self._label(tab, "", F.tk(12, F.semi), AMBER, bg=BG, justify="left", wraplength=px(640))
        self._sys_tab = tab
        self.show_section(self._section, shown=False)
        return tab

    def show_section(self, name: str, shown: bool = True) -> None:
        """Shows a System section (built the first time); shown=False skips its first read."""
        if name not in self.SECTIONS or not hasattr(self, "s_tabbar"):
            return
        if self.s_tabbar.get() != name:
            self.s_tabbar.set(name)
        frame = self._sections.get(name)
        if frame is None:
            builders = {"Updates": self._build_updates, "Storage": self._build_storage,
                        "Network & security": self._build_netsec, "Services & jobs": self._build_services,
                        "Logs": self._build_logs}
            frame = self._sections[name] = builders[name](self._sys_tab)
        for other, widget in self._sections.items():
            if other != name:
                widget.grid_remove()
        frame.grid(row=2, column=0, sticky="nsew")
        self._section = name
        self._render_section(name)
        self._render_sys_controls()
        self._render_sys_status()
        if shown:
            self._section_shown()

    def _section_keys(self, section: str) -> List[str]:
        return ["journal"] if section == "Logs" else [k for k, _a in self.SECTION_LOADS[section]]

    def _section_shown(self) -> None:
        """The first time a section is on screen (with the agent reachable) it reads its data."""
        if not self.visible or self._tab != "System" or not self._ready:
            return
        for key, _action in self.SECTION_LOADS[self._section]:
            st = self._sys[key]
            if not st["tried"] and not st["busy"]:
                self.sys_load(key)
        if self._section == "Logs" and not self._sys["journal"]["tried"]:
            self.logs_refresh()

    def section_refresh(self) -> None:
        if not self._need_ready("Refresh"):
            return
        if self._section == "Logs":
            self.logs_refresh()
            return
        for key, _action in self.SECTION_LOADS[self._section]:
            self.sys_load(key)

    _SYS_ACTIONS = {"updates": "sys_updates", "storage": "storage", "network": "network", "security": "security",
                    "services": "services_list", "timers": "timers"}

    def sys_load(self, key: str) -> None:
        """Reads one agent view (updates, storage, network, security, services, timers)."""
        st = self._sys[key]
        if st["busy"] or not self.built or not self._reachable():
            return
        action = self._SYS_ACTIONS[key]
        st["busy"], st["tried"], st["asked"] = True, True, time.monotonic()
        self._render_sys(key)
        self._render_sys_status()
        self._action(action, {}, lambda res: self._sys_loaded(key, action, res))

    def _sys_loaded(self, key: str, action: str, res: Dict[str, Any]) -> None:
        st = self._sys[key]
        st["busy"] = False
        if action_ok(res):
            st["data"], st["error"], st["at"] = res, "", time.time()
            if key == "updates":
                self._reboot_needed = None  # the fresh check knows
        else:
            st["error"] = self._fail_text(res, action, "The server couldn't check that.")
        self._render_sys(key)
        self._render_sys_status()
        self._render_sys_controls()

    def _render_sys(self, key: str) -> None:
        renderers = {"updates": self._render_updates, "storage": self._render_storage,
                     "network": self._render_network, "security": self._render_security,
                     "services": self._render_svc_list, "timers": self._render_timers,
                     "journal": self._render_journal}
        renderers[key]()
        if key == "services":
            self._logs_units()

    def _render_section(self, name: str) -> None:
        for key in {"Updates": ("updates",), "Storage": ("storage",), "Network & security": ("network", "security"),
                    "Services & jobs": ("services", "timers"), "Logs": ("journal",)}[name]:
            self._render_sys(key)

    def _render_sys_status(self, now: Optional[float] = None) -> None:
        if not hasattr(self, "s_status"):
            return
        keys = self._section_keys(self._section)
        states = [self._sys[k] for k in keys]
        loading = any(s["busy"] and s["data"] is None for s in states)
        errors = [s["error"] for s in states if s["error"]]
        ats = [s["at"] for s in states if s["at"]]
        if self._section == "Updates" and self._applying:
            text, color = "Installing updates…", CYAN
        elif loading:
            text, color = {"Updates": "Checking for updates…", "Storage": "Measuring disks and folders…"}.get(
                self._section, "Loading…"), DIM
        elif any(s["busy"] for s in states):
            text, color = "Refreshing…", DIM
        elif errors:
            text, color = ellipsize(errors[0], 64), SOFT_RED
        elif ats:
            text, color = f"Updated {fmt_ago(min(ats), now)}", DIM
        else:
            text, color = "", DIM
        self._put(self.s_status, text=text, fg=color)
        busy = any(s["busy"] for s in states)
        self._put(self.s_refresh, state="normal" if self._ready and not busy else "disabled")
        self._render_reboot(now)

    def _render_sys_controls(self) -> None:
        if "Updates" in self._sections:
            self._render_update_buttons()
        if "Storage" in self._sections:
            self._render_storage()
        if "Services & jobs" in self._sections:
            self._render_svc_actions()
        if "Logs" in self._sections:
            busy = self._sys["journal"]["busy"]
            self._put(self.lg_refresh, text="Loading…" if busy else "Refresh",
                      state="normal" if self._ready and not busy else "disabled")
        if hasattr(self, "s_refresh"):
            busy = any(self._sys[k]["busy"] for k in self._section_keys(self._section))
            self._put(self.s_refresh, state="normal" if self._ready and not busy else "disabled")

    # ------------------------------------------------------------ system: updates

    def _build_updates(self, parent: tk.Misc) -> tk.Misc:
        app, F, px = self.app, self.app.fonts, self.app.px
        scroll, inner = self._scroll_tab(parent)
        card = app.card(inner)
        card.pack(fill="x", padx=6, pady=(0, 6))
        app.card_header(card, "Operating system updates", "download")
        body = tk.Frame(card, bg=CARD)
        body.pack(fill="x", padx=16, pady=(0, 14))
        self.u_summary = self._label(body, "", F.tk(13, F.semi), TEXT, justify="left", wraplength=px(680))
        self.u_summary.pack(anchor="w")
        self.u_chips = chips = tk.Frame(body, bg=CARD)
        self.u_count = self._chip(chips, "—", DIM)
        self.u_count.pack(side="left")
        self.u_security = self._chip(chips, "—", DIM)
        self.u_security.pack(side="left", padx=(6, 0))
        self.u_reboot = self._chip(chips, "—", DIM)
        self.u_reboot.pack(side="left", padx=(6, 0))
        self.u_kernel = self._label(chips, "", F.tk(11), DIM)
        self.u_kernel.pack(side="left", padx=(px(12), 0))
        self.u_release = self._label(body, "", F.tk(12), AMBER, justify="left", wraplength=px(680))
        self.u_buttons = buttons = tk.Frame(body, bg=CARD)
        buttons.pack(anchor="w", pady=(px(12), 0))
        self.u_check = app.button(buttons, "Check now", lambda: self.sys_load("updates"), icon="refresh", height=30,
                                  font_size=11)
        self.u_check.pack(side="left")
        self.u_sec_btn = app.button(buttons, "Install security updates", lambda: self.apply_updates(True),
                                    icon="lock", kind="outline", height=30, font_size=11)
        self.u_sec_btn.pack(side="left", padx=(8, 0))
        self.u_all_btn = app.button(buttons, "Install all updates", lambda: self.apply_updates(False),
                                    icon="download", kind="primary", height=30, font_size=11)
        self.u_all_btn.pack(side="left", padx=(8, 0))
        self.u_busy = self._label(body, "", F.tk(12, F.semi), CYAN, justify="left", wraplength=px(680))

        card = app.card(inner)
        card.pack(fill="x", padx=6, pady=6)
        app.card_header(card, "Restart the server", "power")
        body = tk.Frame(card, bg=CARD)
        body.pack(fill="x", padx=16, pady=(0, 14))
        self.r_text = self._label(body, "", F.tk(12), MUTED, justify="left", wraplength=px(680))
        self.r_text.pack(anchor="w")
        buttons = tk.Frame(body, bg=CARD)
        buttons.pack(anchor="w", pady=(px(10), 0))
        self.r_reboot = app.button(buttons, "Reboot server", self.reboot_server, icon="power", kind="danger", height=30,
                                   font_size=11)
        self.r_reboot.pack(side="left")
        self.r_cancel = app.button(buttons, "Cancel reboot", self.cancel_reboot, icon="close", height=30, font_size=11)
        self.r_cancel.pack(side="left", padx=(8, 0))

        card = app.card(inner)
        card.pack(fill="x", padx=6, pady=6)
        slot = app.card_header(card, "Available updates", "apps")
        self.u_pk_note = self._label(slot, "", F.tk(11), DIM)
        self.u_pk_note.pack(side="right")
        box, self.u_list = self._text_box(card, 12)
        box.pack(fill="x", padx=16, pady=(0, 14))

        self.u_out_card = card = app.card(inner)  # packed once an install ran
        app.card_header(card, "Last install", "log", note="the end of dnf's output")
        box, self.u_out = self._text_box(card, 10, wrap="word")
        box.pack(fill="x", padx=16, pady=(0, 14))
        return scroll

    def _updates_numbers(self) -> Tuple[Dict[str, Any], List[Dict[str, Any]], int]:
        d = self._sys["updates"]["data"] if isinstance(self._sys["updates"]["data"], dict) else {}
        pkgs = [p for p in d.get("packages") or [] if isinstance(p, dict) and p.get("name")]
        return d, pkgs, int(_num(d.get("security_count")) or 0)

    def _render_updates(self) -> None:
        if "Updates" not in self._sections:
            return
        st = self._sys["updates"]
        d, pkgs, sec = self._updates_numbers()
        n = len(pkgs)
        if d:
            summary, color = str(d.get("message") or f"{n} updates available."), TEXT
        elif st["busy"]:
            summary, color = "Checking for updates… dnf asks the package mirrors, which can take a minute.", MUTED
        elif st["error"]:
            summary, color = st["error"], SOFT_RED
        else:
            summary, color = "Press Check now to look for operating-system updates.", MUTED
        self._put(self.u_summary, text=summary, fg=color)
        if d:
            if not self.u_chips.winfo_manager():
                self.u_chips.pack(anchor="w", pady=(self.app.px(8), 0), after=self.u_summary)
            for chip, (text, c) in ((self.u_count, (f"{n} update{'s' if n != 1 else ''}", AMBER) if n
                                     else ("Up to date", GREEN)),
                                    (self.u_security, (f"{sec} security", RED) if sec else
                                     ("No security updates", GREEN)),
                                    (self.u_reboot, ("Reboot needed", AMBER) if self._needs_reboot() else
                                     ("No reboot needed", GREEN))):
                self._put(chip, text=text, text_color=c, fg_color=tint(c, 0.16))
            kernel = str(d.get("kernel") or "")
            self._put(self.u_kernel, text=f"Kernel {kernel}" if kernel else "")
        else:
            self.u_chips.pack_forget()
        newer = d.get("newer_release")
        if newer:
            note = "\n".join(line for line in str(d.get("release_note") or "").splitlines()[:5] if line.strip())
            self._put(self.u_release, text=f"A newer Amazon Linux release is available: {newer}. Installing updates "
                                           "keeps the current release; moving to the new one is a separate upgrade."
                                           + (f"\n{note}" if note else ""))
            if not self.u_release.winfo_manager():
                self.u_release.pack(anchor="w", pady=(self.app.px(10), 0), before=self.u_buttons)
        else:
            self.u_release.pack_forget()
        if self._applying:
            what = "security updates" if self._applying == "security" else "all updates"
            self._put(self.u_busy, text=f"Installing {what}… this can take several minutes. Websites keep running; "
                                        "you can keep using Willy meanwhile.")
            if not self.u_busy.winfo_manager():
                self.u_busy.pack(anchor="w", pady=(self.app.px(10), 0))
        else:
            self.u_busy.pack_forget()
        sig = (tuple((str(p.get("name")), str(p.get("version")), str(p.get("repo"))) for p in pkgs), bool(d),
               st["busy"], st["error"])
        if self._sys_sigs.get("packages") != sig:
            self._sys_sigs["packages"] = sig
            if pkgs:
                name_w = min(48, max(len(str(p["name"])) for p in pkgs))
                ver_w = min(34, max(len(str(p.get("version") or "")) for p in pkgs))
                chunks: List[Tuple[str, Any]] = [(f"{'PACKAGE':<{name_w}}  {'VERSION':<{ver_w}}  REPOSITORY\n", "head")]
                chunks += [(f"{ellipsize(p['name'], name_w):<{name_w}}  {ellipsize(p.get('version'), ver_w):<{ver_w}}  "
                            f"{p.get('repo') or ''}\n", "") for p in pkgs]
            elif d:
                chunks = [("Everything is up to date.", "ok")]
            else:
                chunks = [("Loading…" if st["busy"] else st["error"] or "Not checked yet.", "dim")]
            self._fill_text(self.u_list, chunks)
            self._put(self.u_pk_note, text=f"{n} package{'s' if n != 1 else ''}" + (" (the first 200)" if n >= 200
                                                                                      else "") if d else "")
        if self._apply_output:
            if not self.u_out_card.winfo_manager():
                self.u_out_card.pack(fill="x", padx=6, pady=6)
            if self._sys_sigs.get("apply_output") != self._apply_output:
                self._sys_sigs["apply_output"] = self._apply_output
                self._fill_text(self.u_out, [(self._apply_output, "")], end=True)
        self._render_update_buttons()
        self._render_reboot()

    def _needs_reboot(self) -> bool:
        if self._reboot_needed is not None:
            return self._reboot_needed
        d = self._updates_numbers()[0]
        return bool(d.get("reboot_needed"))

    def _render_update_buttons(self) -> None:
        if "Updates" not in self._sections:
            return
        st = self._sys["updates"]
        d, pkgs, sec = self._updates_numbers()
        idle = bool(self._ready) and not self._applying
        self._put(self.u_check, text="Checking…" if st["busy"] else "Check now",
                  state="normal" if idle and not st["busy"] else "disabled")
        self._put(self.u_sec_btn, text="Installing…" if self._applying == "security" else "Install security updates",
                  state="normal" if idle and not st["busy"] and sec else "disabled")
        self._put(self.u_all_btn, text="Installing…" if self._applying == "all" else "Install all updates",
                  state="normal" if idle and not st["busy"] and pkgs else "disabled")
        self._put(self.r_reboot, text="Working…" if self._reboot_busy else "Reboot server",
                  state="normal" if idle and not self._reboot_busy else "disabled")
        self._put(self.r_cancel, state="normal" if self._ready and not self._reboot_busy else "disabled")

    def _render_reboot(self, now: Optional[float] = None) -> None:
        if "Updates" not in self._sections:
            return
        now = now or time.time()
        if self._reboot_at is not None and now > self._reboot_at + 600:
            self._reboot_at = None  # long done
        if self._reboot_at is not None:
            left = self._reboot_at - now
            text = (f"Reboot scheduled: the server restarts in {int(left)} s. Websites and apps go down for about a "
                    "minute; Willy reconnects when it's back." if left > 0 else
                    "The server is restarting… websites are down until it's back (about a minute). Willy reconnects "
                    "on its own.")
            color = AMBER
        else:
            text = ("Restarts the server, e.g. to finish a kernel update. Every website and app on it is down for "
                    "about a minute while it boots.")
            if self._needs_reboot():
                text += " A reboot is needed right now."
            color = MUTED
        self._put(self.r_text, text=text, fg=color)

    def apply_updates(self, security_only: bool) -> None:
        if self._applying or not self._need_ready("Install updates"):
            return
        _d, pkgs, sec = self._updates_numbers()
        what = f"{sec} security update{'s' if sec != 1 else ''}" if security_only else \
            f"all {len(pkgs)} update{'s' if len(pkgs) != 1 else ''}"
        message = (f"Installs {what} on the server with dnf. This can take several minutes. Websites keep running, "
                   "but services can restart briefly as their packages update. If the kernel is updated, a reboot "
                   "finishes it — Willy tells you.")
        self.app.confirm.ask("Install security updates?" if security_only else "Install all updates?", message,
                             "Install", lambda: self._apply(security_only), danger=not security_only)

    def _apply(self, security_only: bool) -> None:
        if self._applying:
            return
        self._applying = "security" if security_only else "all"
        self.app.local_log(f"[UI] Server: installing {'security' if security_only else 'all'} updates")
        self.app.toast("Updates", "Installing on the server… this can take several minutes.", duration=4)
        self._render_updates()
        self._render_sys_status()
        self._action("apply_updates", {"security_only": security_only}, lambda res: self._applied(res))

    def _applied(self, res: Dict[str, Any]) -> None:
        self._applying = None
        if isinstance(res, dict):
            if res.get("output"):
                self._apply_output = str(res["output"])
            if "reboot_needed" in res:
                self._reboot_needed = bool(res.get("reboot_needed"))
        if action_ok(res):
            message = str(res.get("message") or "Updates installed.")
            self.app.toast("Updates", message, kind="success", duration=6)
            self.app.local_log(f"[UI] Server updates: {message}")
        else:
            message = self._fail_text(res, "apply_updates", "The update failed.")
            self.app.toast("Updates", message, kind="error", duration=6)
            self.app.local_log(f"[-] Server updates: {message}")
        self._render_updates()
        self._render_sys_status()
        self.sys_load("updates")  # what's left

    def reboot_server(self) -> None:
        if self._reboot_busy or self._applying or not self._need_ready("Reboot"):
            return
        self.app.confirm.ask(
            "Reboot the server?",
            "The server restarts in 1 minute. EVERY website and app on it goes down until it's back — usually about "
            "a minute — and Willy's server controls disconnect meanwhile. You can still stop it during that minute "
            "with Cancel reboot.", "Reboot", self._reboot, danger=True)

    def _reboot(self) -> None:
        self._reboot_busy = True
        self.app.local_log("[UI] Server: reboot requested")
        self._render_update_buttons()
        self._action("reboot", {}, self._rebooted)

    def _rebooted(self, res: Dict[str, Any]) -> None:
        self._reboot_busy = False
        if action_ok(res):
            self._reboot_at = time.time() + SERVER_REBOOT_DELAY_SEC
            message = str(res.get("message") or "The server reboots in 1 minute.")
            self.app.toast("Reboot", message, kind="success", duration=6)
            self.app.local_log(f"[UI] Server reboot: {message}")
        else:
            message = self._fail_text(res, "reboot", "The server didn't schedule the reboot.")
            self.app.toast("Reboot", message, kind="error")
            self.app.local_log(f"[-] Server reboot: {message}")
        self._render_update_buttons()
        self._render_reboot()

    def cancel_reboot(self) -> None:
        if self._reboot_busy or not self._need_ready("Cancel reboot"):
            return
        self._reboot_busy = True
        self._render_update_buttons()
        self._action("cancel_reboot", {}, self._reboot_cancelled)

    def _reboot_cancelled(self, res: Dict[str, Any]) -> None:
        self._reboot_busy = False
        if action_ok(res):
            self._reboot_at = None
            self.app.toast("Reboot", str(res.get("message") or "Reboot cancelled."), kind="success")
            self.app.local_log("[UI] Server reboot cancelled")
        else:
            self.app.toast("Cancel reboot", self._fail_text(res, "cancel_reboot", "No reboot was cancelled."),
                           kind="error")
        self._render_update_buttons()
        self._render_reboot()

    # ------------------------------------------------------------ system: storage

    def _build_storage(self, parent: tk.Misc) -> tk.Misc:
        app, F, px = self.app, self.app.fonts, self.app.px
        scroll, inner = self._scroll_tab(parent)
        card = app.card(inner)
        card.pack(fill="x", padx=6, pady=(0, 6))
        slot = app.card_header(card, "Disks", "disk")
        self.st_note = self._label(slot, "", F.tk(11), DIM)
        self.st_note.pack(side="right")
        self.st_disks = tk.Frame(card, bg=CARD)
        self.st_disks.pack(fill="x", padx=16, pady=(0, 14))
        self.st_disks.grid_columnconfigure(1, weight=1)
        card = app.card(inner)
        card.pack(fill="x", padx=6, pady=6)
        app.card_header(card, "Clean up", "delete")
        self.st_clean = tk.Frame(card, bg=CARD)
        self.st_clean.pack(fill="x", padx=16, pady=(0, 6))
        self.st_clean.grid_columnconfigure(0, weight=1)
        self._label(card, "Docker cleanup never removes volumes, so the data your apps keep in them stays safe. "
                          "Everything here asks before it deletes anything.", F.tk(11), DIM, justify="left",
                    wraplength=px(660)).pack(anchor="w", padx=16, pady=(0, 14))
        card = app.card(inner)
        card.pack(fill="x", padx=6, pady=6)
        app.card_header(card, "Biggest folders", "folder", note="home, /var, /opt, /usr/local")
        self.st_big = tk.Frame(card, bg=CARD)
        self.st_big.pack(fill="x", padx=16, pady=(0, 14))
        self.st_big.grid_columnconfigure(0, weight=1)
        return scroll

    def _render_storage(self) -> None:
        if "Storage" not in self._sections:
            return
        st = self._sys["storage"]
        d = st["data"] if isinstance(st["data"], dict) else {}
        sig = (id(st["data"]), st["busy"] and not d, st["error"] if not d else "", bool(self._ready),
               tuple(sorted(self._cleaning)))
        if sig == self._sys_sigs.get("storage"):
            return
        self._sys_sigs["storage"] = sig
        app, F, px = self.app, self.app.fonts, self.app.px
        for box in (self.st_disks, self.st_clean, self.st_big):
            self._clear(box)
        if not d:
            text = "Measuring disks and folders… (about 20 seconds)" if st["busy"] else \
                st["error"] or "Not checked yet."
            for box in (self.st_disks, self.st_clean, self.st_big):
                self._label(box, text, F.tk(12), SOFT_RED if st["error"] and not st["busy"] else MUTED,
                            justify="left", wraplength=px(640)).grid(row=0, column=0, columnspan=4, sticky="w")
            self._put(self.st_note, text="")
            return
        row = 0

        def meter(title: str, sub: str, pct: Optional[float], detail: str, accent: str, warn: float,
                  crit: float) -> None:
            nonlocal row
            box = self.st_disks
            cell = tk.Frame(box, bg=CARD)
            cell.grid(row=row, column=0, sticky="w", padx=(0, px(12)), pady=px(4))
            self._label(cell, ellipsize(title, 28), F.tk(12, F.semi), TEXT).pack(anchor="w")
            if sub:
                self._label(cell, ellipsize(sub, 36), F.tk(10), DIM).pack(anchor="w")
            color = _level_color(pct, accent, warn, crit)
            bar = ctk.CTkProgressBar(box, width=60, height=8, corner_radius=4, border_width=0, fg_color=BORDER,
                                     progress_color=color)
            bar.set(max(0.0, min(1.0, (pct or 0.0) / 100.0)))
            bar.grid(row=row, column=1, sticky="ew")
            self._label(box, f"{pct:.0f}%" if pct is not None else "—", F.tk(12, F.semi),
                        color if color in (RED, AMBER) else TEXT, anchor="e", width=5).grid(
                row=row, column=2, sticky="e", padx=(px(10), 0))
            self._label(box, detail, F.tk(11), DIM).grid(row=row, column=3, sticky="w", padx=(px(10), 0))
            row += 1

        mounts = [m for m in d.get("mounts") or [] if isinstance(m, dict)]
        for m in mounts:
            meter(str(m.get("mount") or "?"), " · ".join(str(x) for x in (m.get("device"), m.get("fs")) if x),
                  _num(m.get("pct")), f"{fmt_gb(m.get('used_gb'))} of {fmt_gb(m.get('total_gb'))} · "
                                      f"{fmt_gb(m.get('free_gb'))} free", SKY, 80, 90)
        swap = d.get("swap") if isinstance(d.get("swap"), dict) else {}
        if _num(swap.get("total_gb")):
            meter("Swap", "", _num(swap.get("pct")), f"{fmt_gb(swap.get('used_gb'))} of {fmt_gb(swap.get('total_gb'))} "
                                                     "used", GREEN, 50, 80)
        elif swap:
            self._label(self.st_disks, "Swap · none set up", F.tk(12), MUTED).grid(row=row, column=0, columnspan=4,
                                                                                 sticky="w", pady=px(4))
        if not mounts and not swap:
            self._label(self.st_disks, "The server didn't report any disks.", F.tk(12), MUTED).grid(row=0, column=0,
                                                                                               sticky="w")
        root = next((m for m in mounts if m.get("mount") == "/"), None)
        self._put(self.st_note, text=f"/ has {fmt_gb(root.get('free_gb'))} free" if root else "")

        labels = {**CLEANUP_LABELS, **(d.get("cleanable_labels") if isinstance(d.get("cleanable_labels"), dict)
                                       else {})}
        cleanable = d.get("cleanable") if isinstance(d.get("cleanable"), dict) else {}
        keys = [k for k in labels if k in cleanable] + [k for k in cleanable if k not in labels]
        for r, key in enumerate(keys):
            size, worth = cleanable_text(cleanable[key])
            cell = tk.Frame(self.st_clean, bg=CARD)
            cell.grid(row=r, column=0, sticky="ew", pady=px(4))
            self._label(cell, str(labels.get(key, key)), F.tk(12, F.semi), TEXT).pack(anchor="w")
            self._label(cell, size, F.tk(11), SOFT if worth else DIM, justify="left",
                        wraplength=px(520)).pack(anchor="w")
            busy = key in self._cleaning
            btn = app.button(self.st_clean, "Cleaning…" if busy else "Clean", lambda k=key: self.clean(k),
                             icon="delete", height=28, font_size=11)
            btn.grid(row=r, column=1, sticky="e", padx=(px(12), 0))
            if busy or not worth or not self._ready:
                btn.configure(state="disabled")
        if not keys:
            self._label(self.st_clean, "Nothing to clean up was reported.", F.tk(12), MUTED).grid(row=0, column=0,
                                                                                               sticky="w")

        big = [b for b in d.get("biggest") or [] if isinstance(b, dict) and b.get("path")]
        top = max((_num(b.get("gb")) or 0.0 for b in big), default=0.0) or 1.0
        for r, b in enumerate(big):
            self._label(self.st_big, ellipsize(b["path"], 72), F.tk(11, F.mono), SOFT).grid(row=r, column=0, sticky="w",
                                                                                          pady=px(2))
            bar = ctk.CTkProgressBar(self.st_big, width=120, height=6, corner_radius=3, border_width=0, fg_color=BORDER,
                                     progress_color=SKY)
            bar.set(max(0.0, min(1.0, (_num(b.get("gb")) or 0.0) / top)))
            bar.grid(row=r, column=1, padx=(px(12), 0))
            self._label(self.st_big, fmt_gb(b.get("gb")), F.tk(12, F.semi), TEXT, anchor="e", width=8).grid(
                row=r, column=2, sticky="e", padx=(px(12), 0))
        if not big:
            self._label(self.st_big, "No folder sizes were reported.", F.tk(12), MUTED).grid(row=0, column=0,
                                                                                            sticky="w")

    def clean(self, what: str) -> None:
        if what in self._cleaning or not self._need_ready("Clean up"):
            return
        d = self._sys["storage"]["data"] if isinstance(self._sys["storage"]["data"], dict) else {}
        labels = {**CLEANUP_LABELS, **(d.get("cleanable_labels") if isinstance(d.get("cleanable_labels"), dict)
                                       else {})}
        label = str(labels.get(what, what))
        self.app.confirm.ask(f"Clean up: {label}?", cleanup_warning(what), "Clean up",
                             lambda: self._clean(what, label), danger=True)

    def _clean(self, what: str, label: str) -> None:
        self._cleaning.add(what)
        self.app.local_log(f"[UI] Server cleanup: {what}")
        self._render_storage()
        self._action("cleanup", {"what": what}, lambda res: self._cleaned(what, label, res))

    def _cleaned(self, what: str, label: str, res: Dict[str, Any]) -> None:
        self._cleaning.discard(what)
        if action_ok(res):
            message = str(res.get("message") or f"Cleaned up: {label}.")
            self.app.toast("Clean up", message, kind="success")
            self.app.local_log(f"[UI] Server cleanup: {message}")
        else:
            message = self._fail_text(res, "cleanup", f"Cleanup of {label} failed.")
            self.app.toast("Clean up", message, kind="error")
            self.app.local_log(f"[-] Server cleanup {what}: {message}")
        self._render_storage()
        self.sys_load("storage")  # the new sizes

    # ------------------------------------------------------------ system: network & security

    def _build_netsec(self, parent: tk.Misc) -> tk.Misc:
        app, F, px = self.app, self.app.fonts, self.app.px
        scroll, inner = self._scroll_tab(parent)
        card = app.card(inner)
        card.pack(fill="x", padx=6, pady=(0, 6))
        slot = app.card_header(card, "Network", "wifi")
        self.n_note = self._label(slot, "", F.tk(11), DIM)
        self.n_note.pack(side="right")
        body = tk.Frame(card, bg=CARD)
        body.pack(fill="x", padx=16, pady=(0, 14))
        rates = tk.Frame(body, bg=CARD)
        rates.pack(anchor="w")
        self.n_down = self._label(rates, "↓ —", F.tk(18, F.semi), CYAN)
        self.n_down.pack(side="left")
        self.n_up = self._label(rates, "↑ —", F.tk(18, F.semi), SOFT_PURPLE)
        self.n_up.pack(side="left", padx=(px(22), 0))
        self.n_conns = self._label(rates, "", F.tk(12), MUTED)
        self.n_conns.pack(side="left", padx=(px(22), 0))
        self.n_ifaces = tk.Frame(body, bg=CARD)
        self.n_ifaces.pack(fill="x", pady=(px(10), 0))
        self.n_ifaces.grid_columnconfigure(2, weight=1)
        card = app.card(inner)
        card.pack(fill="x", padx=6, pady=6)
        slot = app.card_header(card, "Listening ports", "link")
        self.n_ports_note = self._label(slot, "", F.tk(11, F.semi), DIM)
        self.n_ports_note.pack(side="right")
        self.n_ports = tk.Frame(card, bg=CARD)
        self.n_ports.pack(fill="x", padx=16, pady=(0, 6))
        self.n_ports.grid_columnconfigure(3, weight=1)
        self._label(card, "PUBLIC ports accept connections from the internet unless a firewall or the cloud's "
                          "security group blocks them; local ones only answer on the server itself.", F.tk(11), DIM,
                    justify="left", wraplength=px(660)).pack(anchor="w", padx=16, pady=(0, 14))
        card = app.card(inner)
        card.pack(fill="x", padx=6, pady=6)
        app.card_header(card, "Security", "lock", note="SSH logins")
        self.sec_box = tk.Frame(card, bg=CARD)
        self.sec_box.pack(fill="x", padx=16, pady=(0, 14))
        return scroll

    def _render_network(self) -> None:
        if "Network & security" not in self._sections:
            return
        st = self._sys["network"]
        d = st["data"] if isinstance(st["data"], dict) else {}
        rate = d.get("rate") if isinstance(d.get("rate"), dict) else {}
        self._put(self.n_down, text=f"↓ {fmt_kbit(rate.get('down_kbps'))}")
        self._put(self.n_up, text=f"↑ {fmt_kbit(rate.get('up_kbps'))}")
        est = _num(d.get("established"))
        self._put(self.n_conns, text=f"{int(est)} active connection{'s' if est != 1 else ''}" if est is not None
                  else "")
        self._put(self.n_note, text=f"Live · every {SERVER_NETWORK_REFRESH_SEC:.0f} s" if d else "")
        ifaces = [i for i in d.get("interfaces") or [] if isinstance(i, dict)]
        ports = [p for p in d.get("listening") or [] if isinstance(p, dict)]
        sig = (tuple((str(i.get("interface")), str(i.get("state")), tuple(map(str, i.get("addresses") or [])))
                     for i in ifaces),
               tuple((str(p.get("proto")), str(p.get("address")), str(p.get("port")), bool(p.get("public")),
                      str(p.get("process")), str(p.get("pid"))) for p in ports),
               bool(d), st["busy"] and not d, st["error"] if not d else "")
        if sig == self._sys_sigs.get("network"):
            return
        self._sys_sigs["network"] = sig
        F, px = self.app.fonts, self.app.px
        self._clear(self.n_ifaces)
        self._clear(self.n_ports)
        if not d:
            text = "Loading…" if st["busy"] else st["error"] or "Not checked yet."
            color = SOFT_RED if st["error"] and not st["busy"] else MUTED
            for box in (self.n_ifaces, self.n_ports):
                self._label(box, text, F.tk(12), color, justify="left", wraplength=px(640)).grid(
                    row=0, column=0, columnspan=5, sticky="w")
            self._put(self.n_ports_note, text="")
            return
        for r, i in enumerate(ifaces):
            state = str(i.get("state") or "?").upper()
            color = GREEN if state == "UP" else RED if state == "DOWN" else DIM
            self._label(self.n_ifaces, str(i.get("interface") or "?"), F.tk(12, F.semi), TEXT).grid(
                row=r, column=0, sticky="w", pady=px(3))
            self._chip(self.n_ifaces, state, color).grid(row=r, column=1, sticky="w", padx=(12, 0))
            self._label(self.n_ifaces, "   ".join(str(a) for a in i.get("addresses") or []) or "no address",
                        F.tk(11, F.mono), SOFT, justify="left", wraplength=px(520)).grid(
                row=r, column=2, sticky="w", padx=(px(12), 0))
        if not ifaces:
            self._label(self.n_ifaces, "No network interfaces reported.", F.tk(12), MUTED).grid(row=0, column=0,
                                                                                           sticky="w")
        if ports:
            self._table_head(self.n_ports, ("PORT", "PROTOCOL", "ADDRESS", "PROCESS", ""), pad_from=1)
        for r, p in enumerate(ports, start=1):
            public = bool(p.get("public"))
            pid = p.get("pid")
            process = str(p.get("process") or "?") + (f" ({pid})" if pid else "")
            self._label(self.n_ports, str(p.get("port") or "?"), F.tk(12, F.semi), AMBER if public else TEXT).grid(
                row=r, column=0, sticky="w", pady=px(2))
            self._label(self.n_ports, str(p.get("proto") or ""), F.tk(12), SOFT).grid(row=r, column=1, sticky="w",
                                                                                   padx=(px(12), 0))
            self._label(self.n_ports, str(p.get("address") or "*"), F.tk(11, F.mono), SOFT).grid(
                row=r, column=2, sticky="w", padx=(px(12), 0))
            self._label(self.n_ports, ellipsize(process, 40), F.tk(12), AMBER if public else SOFT).grid(
                row=r, column=3, sticky="w", padx=(px(12), 0))
            if public:
                self._chip(self.n_ports, "PUBLIC", AMBER).grid(row=r, column=4, sticky="e", padx=(12, 0))
            else:
                self._label(self.n_ports, "local", F.tk(11), DIM).grid(row=r, column=4, sticky="e", padx=(px(12), 0))
        if not ports:
            self._label(self.n_ports, "No listening ports reported.", F.tk(12), MUTED).grid(row=0, column=0, sticky="w")
        public = sum(1 for p in ports if p.get("public"))
        self._put(self.n_ports_note, text=f"{public} open to the internet · {len(ports)} in all",
                  fg=AMBER if public else GREEN)

    def _render_security(self) -> None:
        if "Network & security" not in self._sections:
            return
        st = self._sys["security"]
        d = st["data"] if isinstance(st["data"], dict) else {}
        sig = (id(st["data"]), st["busy"] and not d, st["error"] if not d else "")
        if sig == self._sys_sigs.get("security"):
            return
        self._sys_sigs["security"] = sig
        F, px = self.app.fonts, self.app.px
        box = self.sec_box
        self._clear(box)
        if not d:
            text = "Reading the SSH logs… (up to a minute)" if st["busy"] else st["error"] or "Not checked yet."
            self._label(box, text, F.tk(12), SOFT_RED if st["error"] and not st["busy"] else MUTED, justify="left",
                        wraplength=px(640)).pack(anchor="w")
            return
        tiles = tk.Frame(box, bg=CARD)
        tiles.pack(fill="x")
        failed = int(_num(d.get("failed_ssh_24h")) or 0)
        values = [("Failed SSH logins · 24 h", f"{failed:,}", GREEN if failed == 0 else AMBER),
                  ("SSH password login", *ssh_setting("password", d.get("ssh_password_login"))),
                  ("Root login over SSH", *ssh_setting("root", d.get("root_login"))),
                  ("fail2ban", *ssh_setting("fail2ban", d.get("fail2ban")))]
        self.sec_tiles: Dict[str, tk.Label] = {}
        for col, (title, value, color) in enumerate(values):
            tiles.grid_columnconfigure(col, weight=1, uniform="sec")
            tile = ctk.CTkFrame(tiles, fg_color=SURFACE, corner_radius=10, border_width=1, border_color=BORDER)
            tile.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 8, 0))
            self._label(tile, title.upper(), F.tk(10, F.semi), DIM, bg=SURFACE).pack(anchor="w", padx=12, pady=(10, 2))
            label = self._label(tile, value, F.tk(15, F.semi), color, bg=SURFACE)
            label.pack(anchor="w", padx=12, pady=(0, 10))
            self.sec_tiles[title] = label

        def section(title: str, lines: List[str], empty: str) -> None:
            self._label(box, title, F.tk(10, F.semi), DIM).pack(anchor="w", pady=(px(12), px(2)))
            for line in lines:
                self._label(box, ellipsize(line, 130), F.tk(11, F.mono), SOFT).pack(anchor="w")
            if not lines:
                self._label(box, empty, F.tk(12), MUTED).pack(anchor="w")

        attackers = [a for a in d.get("top_attackers") or [] if isinstance(a, dict) and a.get("ip")]
        width = max((len(str(a["ip"])) for a in attackers), default=0)
        section("MOST FAILED LOGINS FROM · 24 h",
                [f"{str(a['ip']):<{width}}  {int(_num(a.get('attempts')) or 0):>6,} attempts" for a in attackers],
                "No failed logins.")
        section("LOGGED IN NOW", [str(x) for x in d.get("logged_in") or []], "Nobody is logged in.")
        section("RECENT LOGINS", [str(x) for x in d.get("recent_logins") or []], "No recent logins recorded.")

    # ------------------------------------------------------------ system: services & jobs

    def _build_services(self, parent: tk.Misc) -> tk.Misc:
        app, F, px = self.app, self.app.fonts, self.app.px
        scroll, inner = self._scroll_tab(parent)
        card = app.card(inner)
        card.pack(fill="x", padx=6, pady=(0, 6))
        slot = app.card_header(card, "Services", "gear", note="systemd")
        self.sv_note = self._label(slot, "", F.tk(11), DIM)
        self.sv_note.pack(side="right")
        bar = tk.Frame(card, bg=CARD)
        bar.pack(fill="x", padx=16, pady=(0, 8))
        self.sv_filter = ctk.CTkEntry(bar, height=30, width=240, font=F(12), fg_color=SURFACE, border_color=BORDER,
                                      placeholder_text="Filter services…", placeholder_text_color=DIM)
        self.sv_filter.pack(side="left")
        self.sv_filter.bind("<KeyRelease>", lambda _e: self._svc_queue_render())
        self._label(bar, "Failed ones come first, in red. Double-click a service for its logs.", F.tk(11), DIM).pack(
            side="left", padx=(px(12), 0))
        box = ctk.CTkFrame(card, fg_color=SURFACE, corner_radius=10, border_width=1, border_color=BORDER)
        box.pack(fill="x", padx=16)
        box.grid_columnconfigure(0, weight=1)
        self._sv_font = tkfont.Font(root=self, family=F.mono, size=-px(12))
        self.sv_head = tk.Label(box, text="", font=self._sv_font, fg=DIM, bg=SURFACE, anchor="w", bd=0, padx=0)
        self.sv_head.grid(row=0, column=0, columnspan=2, sticky="ew", padx=(px(8) + 1, 0), pady=(px(8), 0))
        self.sv_list = tk.Listbox(box, bg=SURFACE, fg=SOFT, font=self._sv_font, height=14,
                                  selectbackground=tint(CYAN, 0.30, SURFACE), selectforeground=TEXT, activestyle="none",
                                  highlightthickness=0, bd=0, relief="flat", exportselection=False, selectmode="browse")
        self.sv_list.grid(row=1, column=0, sticky="nsew", padx=(px(8), 0), pady=(px(2), px(8)))
        scrollbar = ThinScrollbar(box, self.sv_list.yview, SURFACE, app.scale)
        scrollbar.grid(row=1, column=1, sticky="ns", padx=(2, 6), pady=10)
        self.sv_list.configure(yscrollcommand=scrollbar.set)
        _own_wheel(self.sv_list)
        self.sv_list.bind("<<ListboxSelect>>", lambda _e: self._svc_select())
        self.sv_list.bind("<Double-Button-1>", lambda _e: self._svc_do("logs"))
        acts = tk.Frame(card, bg=CARD)
        acts.pack(fill="x", padx=16, pady=(10, 14))
        self.sv_selected = self._label(acts, "", F.tk(12, F.semi), MUTED)
        self.sv_selected.pack(side="left")
        self.sv_boot = ctk.CTkSwitch(acts, text="At boot", command=self._svc_boot_clicked, font=F(11), text_color=SOFT,
                                     progress_color=GREEN, button_color=TEXT, button_hover_color=SOFT, fg_color=BORDER,
                                     switch_width=36, switch_height=18)
        self.sv_boot.pack(side="right", padx=(px(12), 0))
        self.sv_buttons: Dict[str, ctk.CTkButton] = {}
        for action, text, icon in (("restart", "Restart", "refresh"), ("stop", "Stop", "stop"),
                                   ("start", "Start", "play"), ("logs", "Logs", "log")):
            btn = app.button(acts, text, lambda a=action: self._svc_do(a), icon=icon, height=28, font_size=11)
            btn.pack(side="right", padx=(6, 0))
            self.sv_buttons[action] = btn

        card = app.card(inner)
        card.pack(fill="x", padx=6, pady=6)
        slot = app.card_header(card, "Scheduled jobs", "clock", note="cron and systemd timers")
        self.tm_note = self._label(slot, "", F.tk(11), DIM)
        self.tm_note.pack(side="right")
        box, self.tm_text = self._text_box(card, 12)
        box.pack(fill="x", padx=16, pady=(0, 14))
        return scroll

    def _svc_queue_render(self) -> None:
        if self._svc_job is not None:
            self.after_cancel(self._svc_job)

        def run() -> None:
            self._svc_job = None
            self._render_svc_list()

        self._svc_job = self.after(150, run)

    def _svc_all(self) -> List[Dict[str, Any]]:
        d = self._sys["services"]["data"]
        return [r for r in (d.get("services") if isinstance(d, dict) else None) or []
                if isinstance(r, dict) and r.get("name")]

    def _svc_row(self, name: Optional[str]) -> Optional[Dict[str, Any]]:
        return next((r for r in self._svc_all() if r["name"] == name), None) if name else None

    def _render_svc_list(self) -> None:
        if "Services & jobs" not in self._sections:
            return
        st = self._sys["services"]
        everything = self._svc_all()
        rows = filter_services(everything, self.sv_filter.get())
        name_w = max(14, min(32, max((len(str(r["name"])) for r in rows), default=14)))
        self._put(self.sv_head, text=f"{'SERVICE':<{name_w}}  {'STATE':<20}  {'AT BOOT':<9}  DESCRIPTION")
        self._svc_rows = rows
        self.sv_list.delete(0, "end")
        for i, r in enumerate(rows):
            self.sv_list.insert("end", service_line(r, name_w))
            active = str(r.get("active") or "")
            self.sv_list.itemconfigure(i, fg=SOFT_RED if active == "failed" else SOFT if active == "active" else
                                       AMBER if active in ("activating", "deactivating", "reloading") else DIM)
            if r["name"] == self._svc_selected:
                self.sv_list.selection_set(i)
                self.sv_list.see(i)
        if not rows:
            if everything:
                note = "No service matches the filter."
            elif st["busy"]:
                note = "Loading services…"
            else:
                note = st["error"] or "Not loaded yet."
            self.sv_list.insert("end", "  " + note)
            self.sv_list.itemconfigure(0, fg=SOFT_RED if st["error"] and not everything and not st["busy"] else DIM)
        running = sum(1 for r in everything if r.get("active") == "active")
        failed = sum(1 for r in everything if r.get("active") == "failed")
        self._put(self.sv_note, text=f"{running} running · {failed} failed · {len(everything)} in all" if everything
                  else "", fg=SOFT_RED if failed else DIM)
        self._render_svc_actions()

    def _svc_select(self) -> None:
        sel = self.sv_list.curselection()
        if sel and sel[0] < len(self._svc_rows):
            self._svc_selected = str(self._svc_rows[sel[0]]["name"])
        self._render_svc_actions()

    def _render_svc_actions(self) -> None:
        if "Services & jobs" not in self._sections:
            return
        row = self._svc_row(self._svc_selected)
        ready = bool(self._ready)
        if row is None:
            self._put(self.sv_selected, text="Select a service to start, stop or restart it.", fg=MUTED)
            for btn in self.sv_buttons.values():
                self._put(btn, state="disabled")
            self.sv_boot.deselect()
            self._put(self.sv_boot, state="disabled", text="At boot")
            return
        name = str(row["name"])
        active = str(row.get("active") or "unknown")
        busy = ("service", name) in self._controlling
        running = active in ("active", "activating", "reloading")
        sub = str(row.get("sub") or "")
        self._put(self.sv_selected, text=ellipsize(f"{name} · {active}" + (f" ({sub})" if sub and sub != active
                                                                           else ""), 56), fg=service_color(active))
        states = {"logs": ready, "start": ready and not busy and not running, "stop": ready and not busy and running,
                  "restart": ready and not busy}
        for action, btn in self.sv_buttons.items():
            text = {"logs": "Logs", "start": "Start", "stop": "Stop", "restart": "Restart"}[action]
            self._put(btn, text="…" if busy and action != "logs" else text,
                      state="normal" if states[action] else "disabled")
        boot = boot_state(row.get("boot"))
        if boot:
            self.sv_boot.select()
        else:
            self.sv_boot.deselect()
        booting = name in self._booting
        self._put(self.sv_boot, text="At boot…" if booting else "At boot" if boot is not None else
                  f"At boot: {row.get('boot') or 'n/a'}",
                  state="normal" if ready and boot is not None and not booting else "disabled")

    def _svc_do(self, action: str) -> None:
        row = self._svc_row(self._svc_selected)
        if row is None:
            return
        if action == "logs":
            self.control_logs("service", str(row["name"]))
        else:
            self.control("service", str(row["name"]), action)

    def _svc_boot_clicked(self) -> None:
        row = self._svc_row(self._svc_selected)
        want = bool(self.sv_boot.get())
        current = boot_state(row.get("boot")) if row else None
        if current:  # the switch only moves once the server made the change
            self.sv_boot.select()
        else:
            self.sv_boot.deselect()
        if row is None or current is None or want == current or not self._need_ready("At boot"):
            return
        name = str(row["name"])
        if want:
            message = (f"{name} will start automatically whenever the server boots. Nothing changes right now — "
                       "it isn't started or stopped.")
        else:
            message = f"{name} won't start automatically after the next reboot. It keeps running now."
            if name in _BOOT_CRITICAL:
                message += f" Careful: the server relies on {name} — after a reboot it would be missing until " \
                           "someone starts it by hand."
        self.app.confirm.ask(f"Start {name} at boot?" if want else f"Stop starting {name} at boot?", message,
                             "Enable" if want else "Disable", lambda: self._set_boot(name, want), danger=not want)

    def _set_boot(self, name: str, enable: bool) -> None:
        self._booting.add(name)
        self.app.local_log(f"[UI] Server: {'enable' if enable else 'disable'} {name} at boot")
        self._render_svc_actions()
        self._action("service_boot", {"name": name, "enable": enable},
                     lambda res: self._boot_set(name, enable, res))

    def _boot_set(self, name: str, enable: bool, res: Dict[str, Any]) -> None:
        self._booting.discard(name)
        label = f"{name} at boot"
        if action_ok(res):
            row = self._svc_row(name)
            if row is not None:
                row["boot"] = "enabled" if enable else "disabled"
            message = str(res.get("message") or f"{name} will {'start' if enable else 'not start'} at boot.")
            self.app.toast(label, message, kind="success")
            self.app.local_log(f"[UI] {message}")
            self._render_svc_list()
            self.sys_load("services")
        else:
            message = self._fail_text(res, "service_boot", "The boot setting didn't change.")
            self.app.toast(label, message, kind="error")
            self.app.local_log(f"[-] {label}: {message}")
        self._render_svc_actions()

    def _render_timers(self) -> None:
        if "Services & jobs" not in self._sections:
            return
        st = self._sys["timers"]
        d = st["data"] if isinstance(st["data"], dict) else {}
        sig = (id(st["data"]), st["busy"] and not d, st["error"] if not d else "")
        if sig == self._sys_sigs.get("timers"):
            return
        self._sys_sigs["timers"] = sig
        if not d:
            self._fill_text(self.tm_text, [("Loading…" if st["busy"] else st["error"] or "Not loaded yet.",
                                            "err" if st["error"] and not st["busy"] else "dim")])
            self._put(self.tm_note, text="")
            return
        cron = [str(x) for x in d.get("cron") or [] if str(x).strip()]
        timers = [t for t in d.get("timers") or [] if isinstance(t, dict)]
        chunks: List[Tuple[str, Any]] = [("CRON JOBS (the agent user's crontab)\n", "head")]
        chunks += [(line + "\n", "") for line in cron] or [("No cron jobs.\n", "dim")]
        chunks.append(("\nSYSTEMD TIMERS (next run · left · last run · passed · unit · activates)\n", "head"))
        chunks += [(str(t.get("line") or t.get("timer") or "") + "\n", "") for t in timers] or [("No timers.\n", "dim")]
        self._fill_text(self.tm_text, chunks)
        self._put(self.tm_note, text=f"{len(cron)} cron job{'s' if len(cron) != 1 else ''} · {len(timers)} "
                                     f"timer{'s' if len(timers) != 1 else ''}")

    # ------------------------------------------------------------ system: logs (journal)

    def _build_logs(self, parent: tk.Misc) -> tk.Misc:
        app, F, px = self.app, self.app.fonts, self.app.px
        tab = tk.Frame(parent, bg=BG)
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=1)
        card = app.card(tab)
        card.grid(row=0, column=0, sticky="nsew", padx=18, pady=(0, 14))
        card.grid_columnconfigure(0, weight=1)
        card.grid_rowconfigure(1, weight=1)
        bar = tk.Frame(card, bg=CARD)
        bar.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 8))
        combo = dict(height=30, font=F(12), dropdown_font=F(12), fg_color=SURFACE, border_color=BORDER,
                     button_color=BORDER, button_hover_color=BORDER_HI, dropdown_fg_color=CARD_ALT,
                     dropdown_hover_color=BORDER, dropdown_text_color=TEXT, text_color=TEXT)
        menu = dict(height=30, font=F(12), dropdown_font=F(12), fg_color=SURFACE, button_color=BORDER,
                    button_hover_color=BORDER_HI, dropdown_fg_color=CARD_ALT, dropdown_hover_color=BORDER,
                    dropdown_text_color=TEXT, text_color=TEXT)
        fields = (("Unit", "lg_unit", lambda: ctk.CTkComboBox(bar, values=[SERVER_ALL_UNITS], width=200, **combo)),
                  ("Priority", "lg_prio", lambda: ctk.CTkOptionMenu(bar, values=list(SERVER_JOURNAL_PRIORITIES),
                                                                    width=110, **menu)),
                  ("Since", "lg_since", lambda: ctk.CTkComboBox(bar, values=list(SERVER_JOURNAL_SINCE), width=150,
                                                                **combo)),
                  ("Lines", "lg_lines", lambda: ctk.CTkOptionMenu(bar, values=list(SERVER_JOURNAL_LINES), width=80,
                                                                  **menu)))
        for col, (title, attr, make) in enumerate(fields):
            self._label(bar, title, F.tk(11), DIM).grid(row=0, column=col, sticky="w", padx=(0 if col == 0 else 10, 0))
            widget = make()
            widget.grid(row=1, column=col, sticky="w", padx=(0 if col == 0 else 10, 0))
            setattr(self, attr, widget)
        self.lg_unit.set(SERVER_ALL_UNITS)
        self.lg_prio.set("any")
        self.lg_since.set("1 hour ago")
        self.lg_lines.set("200")
        # second row: the search box (under unit, priority and since) and Refresh
        self._label(bar, "Search", F.tk(11), DIM).grid(row=2, column=0, sticky="w", pady=(px(8), 0))
        self.lg_search = ctk.CTkEntry(bar, height=30, font=F(12), fg_color=SURFACE, border_color=BORDER,
                                      placeholder_text="text or pattern (optional)", placeholder_text_color=DIM)
        self.lg_search.grid(row=3, column=0, columnspan=3, sticky="ew")
        self.lg_refresh = app.button(bar, "Refresh", self.logs_refresh, icon="refresh", kind="primary", height=30)
        self.lg_refresh.grid(row=3, column=3, sticky="w", padx=(10, 0))
        for widget in (self.lg_search, self.lg_unit, self.lg_since):
            widget.bind("<Return>", lambda _e: self.logs_refresh())
        box, self.lg_text = self._text_box(card, 20, wrap="word")
        box.grid(row=1, column=0, sticky="nsew", padx=16)
        self.lg_text.unbind("<MouseWheel>")  # not inside a ScrollArea: the box scrolls on its own
        self.lg_status = self._label(card, "", F.tk(11), DIM)
        self.lg_status.grid(row=2, column=0, sticky="ew", padx=16, pady=(6, 12))
        self._logs_units()
        return tab

    def _logs_units(self) -> None:
        if not hasattr(self, "lg_unit"):
            return
        names = sorted({str(r["name"]) for r in self._svc_all()}, key=str.lower)
        values = [SERVER_ALL_UNITS] + names
        if self._sys_sigs.get("units") != values:
            self._sys_sigs["units"] = values
            self.lg_unit.configure(values=values)

    def logs_refresh(self) -> None:
        """Reads the system journal with the Logs fields' filters."""
        if not hasattr(self, "lg_unit") or self._sys["journal"]["busy"] or not self._need_ready("System logs"):
            return
        payload, problem = journal_payload(self.lg_unit.get(), self.lg_prio.get(), self.lg_since.get(),
                                           self.lg_search.get(), self.lg_lines.get())
        if problem:
            self.app.toast("System logs", problem, kind="error")
            self._put(self.lg_status, text=problem, fg=SOFT_RED)
            return
        st = self._sys["journal"]
        st["busy"], st["tried"], st["asked"] = True, True, time.monotonic()
        self._render_journal()
        self._render_sys_status()
        self._action("journal", payload, lambda res: self._journal_done(payload, res))

    def _journal_done(self, payload: Dict[str, Any], res: Dict[str, Any]) -> None:
        st = self._sys["journal"]
        st["busy"] = False
        if isinstance(res, dict) and isinstance(res.get("logs"), str) and not res.get("error"):
            # journalctl -g exits 1 when nothing matches: that's an empty answer, not a failure
            st["data"], st["error"], st["at"] = {"logs": res["logs"], "payload": payload}, "", time.time()
        else:
            st["error"] = self._fail_text(res, "journal", "Couldn't read the system logs.")
        self._render_journal()
        self._render_sys_status()

    def _render_journal(self) -> None:
        if "Logs" not in self._sections:
            return
        st = self._sys["journal"]
        d = st["data"] if isinstance(st["data"], dict) else {}
        busy = st["busy"]
        self._put(self.lg_refresh, text="Loading…" if busy else "Refresh",
                  state="normal" if self._ready and not busy else "disabled")
        sig = (id(st["data"]), busy and not d, st["error"] if not d else "")
        if sig != self._sys_sigs.get("journal"):
            self._sys_sigs["journal"] = sig
            if d:
                lines = str(d.get("logs") or "").splitlines()
                chunks: List[Tuple[str, Any]] = [
                    (line + "\n", "err" if _LOG_ERR_RE.search(line) else "warn" if _LOG_WARN_RE.search(line) else "")
                    for line in lines] or [("No log lines match these filters.", "dim")]
                self._fill_text(self.lg_text, chunks, end=True)
            else:
                self._fill_text(self.lg_text, [("Reading the journal…" if busy else st["error"] or
                                                "Press Refresh to read the system journal.",
                                                "err" if st["error"] and not busy else "dim")])
        if busy:
            text, color = "Loading…", DIM
        elif st["error"]:
            text, color = st["error"], SOFT_RED
        elif d:
            p = d.get("payload") or {}
            what = [f"unit {p['unit']}" if p.get("unit") else "all units",
                    f"priority {p['priority']} and worse" if p.get("priority") else "",
                    f"since {p['since']}" if p.get("since") else "", f"matching “{p['grep']}”" if p.get("grep") else ""]
            n = len(str(d.get("logs") or "").splitlines())
            text = f"{n} line{'s' if n != 1 else ''} · " + " · ".join(w for w in what if w) + \
                   f" · read {time.strftime('%H:%M:%S', time.localtime(st['at']))}"
            color = DIM
        else:
            text, color = "", DIM
        self._put(self.lg_status, text=text, fg=color)


# ----------------------------------------------------------------------------- app

class VoicePill:
    """A small always-on-top pill at the bottom of the screen while "Hey Willy" is active."""

    COLORS = {"listening": "#22d3ee", "thinking": "#a78bfa", "speaking": "#34d399", "loading": "#fbbf24",
              "training": "#f472b6"}
    TITLES = {"listening": "Willy is listening", "thinking": "Willy", "speaking": "Willy", "loading": "Hey Willy",
              "training": "Learning your voice"}

    def __init__(self, app: Any):
        self.app = app
        self.win: Optional[tk.Toplevel] = None
        self._hide_job: Optional[str] = None
        self._heard = ""

    def _build(self) -> None:
        win = tk.Toplevel(self.app)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        try:
            win.attributes("-alpha", 0.94)
        except tk.TclError:
            pass
        win.configure(bg="#0f172a")
        frame = tk.Frame(win, bg="#0f172a", padx=16, pady=10)
        frame.pack(fill="both", expand=True)
        self.dot = tk.Canvas(frame, width=14, height=14, bg="#0f172a", highlightthickness=0)
        self.dot.grid(row=0, column=0, rowspan=2, padx=(0, 10))
        self.oval = self.dot.create_oval(2, 2, 12, 12, fill="#22d3ee", outline="")
        self.title = tk.Label(frame, text="Willy", fg="#e2e8f0", bg="#0f172a", font=("Segoe UI Semibold", 11))
        self.title.grid(row=0, column=1, sticky="w")
        self.body = tk.Label(frame, text="", fg="#94a3b8", bg="#0f172a", font=("Segoe UI", 10),
                             wraplength=420, justify="left")
        self.body.grid(row=1, column=1, sticky="w")
        win.bind("<Button-1>", lambda _e: self.hide())
        self.win = win

    def _place(self) -> None:
        self.win.update_idletasks()
        w, h = max(260, self.win.winfo_reqwidth()), self.win.winfo_reqheight()
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        self.win.geometry(f"{w}x{h}+{(sw - w) // 2}+{sh - h - 90}")

    def heard(self, text: str) -> None:
        self._heard = text

    def show(self, state: str, detail: str = "") -> None:
        if state in ("idle", "muted", "off", "error"):
            if self.win is not None:
                if state == "idle":
                    self._schedule_hide(1800)
                else:
                    self.hide()
            self._heard = ""
            return
        if self.win is None:
            self._build()
        if self._hide_job:
            self.app.after_cancel(self._hide_job)
            self._hide_job = None
        self.dot.itemconfigure(self.oval, fill=self.COLORS.get(state, "#22d3ee"))
        self.title.configure(text=self.TITLES.get(state, "Willy"))
        if state in ("speaking", "loading", "training"):
            body = detail
        elif state == "thinking":
            body = f"\u201c{self._heard}\u201d" if self._heard else "Thinking\u2026"
        elif detail == "follow-up":
            body = "Anything else?"
        else:
            body = "Listening\u2026"
        self.body.configure(text=body)
        self.win.deiconify()
        self._place()

    def _schedule_hide(self, ms: int) -> None:
        if self._hide_job:
            self.app.after_cancel(self._hide_job)
        self._hide_job = self.app.after(ms, self.hide)

    def hide(self) -> None:
        self._hide_job = None
        if self.win is not None:
            try:
                self.win.withdraw()
            except tk.TclError:
                self.win = None


class WillyDesktopApp(ctk.CTk):
    PAGES = (("overview", "Overview", OverviewPage), ("assistant", "Assistant", AssistantPage),
             ("processes", "Processes", ProcessesPage), ("devices", "Devices", DevicesPage),
             ("phone", "Phone", PhonePage), ("activity", "Activity", ActivityPage), ("tools", "Tools", ToolsPage),
             ("log", "Log", LogPage), ("server", "Server", ServerPage))

    def __init__(self, start_hidden: bool = False, instance: Optional[Any] = None):
        super().__init__(fg_color=BG)
        self.title(APP_TITLE)
        self.closing = False
        self.instance = instance  # pc_client.instance.SingleInstance, when launched through main.py
        self.window_hidden = False
        self._tray_hint_shown = False
        self._ticks = 0
        self._last_activity = time.monotonic()
        self._queue: "queue.SimpleQueue[Tuple[Callable[..., Any], tuple]]" = queue.SimpleQueue()
        self._jobs: Dict[str, Optional[str]] = {}
        self.scale = float(ctk.ScalingTracker.get_widget_scaling(self))
        self.fonts = Fonts(self, self.scale)
        self.icons = Icons(self.scale)
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="willy-ui")
        self.silent_executor = LocalExecutor(notifier=lambda *_a: None)  # sliders: no pop-up per change
        self.specs: Dict[str, Any] = {}
        self.telemetry: Dict[str, Any] = {}
        self.server_info: Dict[str, Any] = {}
        self.log_lines: deque = deque(maxlen=MAX_LOG_LINES)
        self._status = ("connecting", "")
        self._status_at = time.monotonic()
        self._last_error = ""
        self._texts: Dict[str, str] = {}
        self._unseen_activity = 0
        self._history_seeded = False
        self._brightness_checked = False
        self._telemetry_at = time.monotonic()  # grace period before sampling locally
        self._local_sampling = False
        self._audio_lock = threading.Lock()
        self._audio_state: Dict[str, Any] = {"ready": None, "buffer": None}
        self._node_thread: Optional[threading.Thread] = None

        self._place_window()
        self._make_brand_images()
        self.node = PCClientNode(
            on_status=lambda state, detail: self.post(self._on_status, state, detail),
            on_log=lambda text: self.post(self._on_log, time.strftime("%H:%M:%S"), text),
            on_event=lambda event: self.post(self._on_event, event),
            on_telemetry=lambda telemetry: self.post(self._on_telemetry, telemetry),
            notifier=lambda title, message, query=None: self.post(self._on_notify, title, message, query),
        )
        self._build_header()
        self._build_sidebar()
        self.content = tk.Frame(self, bg=BG, bd=0, highlightthickness=0)
        self.content.grid(row=1, column=1, sticky="nsew")
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(1, weight=1)

        self.pages: Dict[str, BasePage] = {key: cls(self.content, self) for key, _label, cls in self.PAGES}
        self.current: Optional[str] = None
        self.toasts = ToastManager(self)
        self._confirm: Optional[ConfirmDialog] = None
        self._quickdrop: Optional[QuickDropDialog] = None
        self.show_page("overview")

        self.protocol("WM_DELETE_WINDOW", self._on_window_close)
        self.bind_all("<MouseWheel>", self._on_mousewheel, add="+")
        for i, (key, _label, _cls) in enumerate(self.PAGES, start=1):
            self.bind_all(f"<Control-Key-{i}>", lambda _e, k=key: self.show_page(k), add="+")
        self.bind_all("<Control-k>", lambda _e: self._focus_assistant(), add="+")

        self.tray = TrayIcon(self.post, {
            "open": self.show_window,
            "dashboard": self.open_dashboard,
            "call": self.open_voice_call,
            "ring_phone": self.ring_phone,
            "reconnect": self.reconnect_hub,
            "quit": self.quit_app,
            "autostart_changed": self._autostart_changed,
            "toggle_voice": self.toggle_hey_willy,
            "toggle_mic": self.toggle_mic_mute,
            "talk": self.talk_now,
            "train_voice": self.train_hey_willy,
            "pair": self.pair_with_account,
            "voice_enabled": lambda: self.voice is not None,
            "mic_muted": lambda: bool(self.voice and self.voice.muted),
        }, hub=urllib.parse.urlparse(self.node.server_url).netloc or self.node.server_url)
        self.voice: Optional[HeyWilly] = None
        self._voice_pill: Optional[VoicePill] = None
        # The server can't warn about itself when it's down: this PC watches it from outside.
        self._hub_down_since: Optional[float] = None
        self._hub_down_alerted = False
        self.tray.start()
        if prefs.get("hey_willy"):
            self.after(2500, self.start_hey_willy)
        if instance is not None:
            instance.listen(on_show=lambda: self.post(self.show_window), on_quit=lambda: self.post(self.quit_app))
        if start_hidden and self.tray.running:
            self.hide_window(hint=False)  # before mainloop: the window never flashes up

        self.after(30, lambda: style_titlebar(self))
        self._start_node()
        self._pump()
        self._tick()
        self.run_bg(collect_static_specs, False, on_done=self._got_specs)
        self.after(4000, lambda: self.run_bg(collect_static_specs, on_done=self._got_specs))
        self.after(1500, self._prebuild_pages)

    # ------------------------------------------------------------- plumbing

    def px(self, value: float) -> int:
        return round(value * self.scale)

    def glyph(self, name: str) -> str:
        code = ICONS.get(name)
        return chr(code) if code and self.fonts.icon else ""

    def post(self, fn: Callable[..., Any], *args: Any) -> None:
        """Thread-safe: queue a call for the Tk thread (callbacks from the node / workers)."""
        if not self.closing:
            self._queue.put((fn, args))

    def _pump(self) -> None:
        """Drains queued callbacks on the Tk thread (time-boxed so a burst never stalls the UI).
        Polls fast right after activity and relaxes to ~11 Hz when idle."""
        if self.closing:
            return
        deadline = time.perf_counter() + 0.03
        handled = 0
        while True:
            try:
                fn, args = self._queue.get_nowait()
            except queue.Empty:
                break
            try:
                fn(*args)
            except Exception:
                self._report(traceback.format_exc())
            handled += 1
            if time.perf_counter() > deadline:
                break
        now = time.monotonic()
        if handled:
            self._last_activity = now
        delay = 8 if handled else (25 if now - self._last_activity < 1.5 else 90)
        self._jobs["pump"] = self.after(delay, self._pump)

    def _report(self, text: str) -> None:
        print(text, file=sys.stderr)
        last = text.strip().splitlines()[-1] if text.strip() else "error"
        self._on_log(time.strftime("%H:%M:%S"), f"[-] UI error: {last}")

    def report_callback_exception(self, exc: Any, val: Any, tb: Any) -> None:  # Tk callback errors
        self._report("".join(traceback.format_exception(exc, val, tb)))

    def run_bg(self, fn: Callable[..., Any], *args: Any, on_done: Optional[Callable[[Any], None]] = None,
               on_error: Optional[Callable[[Exception], None]] = None) -> None:
        """Runs blocking work in the worker pool; results come back on the Tk thread."""
        if self.closing:
            return

        def task() -> None:
            try:
                result = fn(*args)
            except Exception as e:  # noqa: BLE001 - reported to the UI
                if on_error:
                    self.post(on_error, e)
                else:
                    self.post(self._report, "".join(traceback.format_exception(type(e), e, e.__traceback__)))
                return
            if on_done:
                self.post(on_done, result)

        try:
            self.pool.submit(task)
        except RuntimeError:
            pass

    def local_log(self, text: str) -> None:
        self._on_log(time.strftime("%H:%M:%S"), text)

    def toast(self, title: str, message: str, query: Optional[str] = None, kind: str = "info",
              duration: float = 4.5) -> None:
        self.toasts.show(title, message, query, kind, duration)

    def copy_text(self, text: str) -> None:
        if not text:
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self.toast("Copied", ellipsize(text, 90), kind="success", duration=2.2)

    @property
    def confirm(self) -> ConfirmDialog:
        if self._confirm is None:
            self._confirm = ConfirmDialog(self)
        return self._confirm

    @property
    def quickdrop(self) -> QuickDropDialog:
        if self._quickdrop is None:
            self._quickdrop = QuickDropDialog(self)
        return self._quickdrop

    # ------------------------------------------------------------ window

    def _place_window(self) -> None:
        wscale = float(ctk.ScalingTracker.get_window_scaling(self))
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        width = int(min(1180, sw / wscale * 0.94))
        height = int(min(760, sh / wscale * 0.88))
        x = max(0, int((sw - width * wscale) / 2))
        y = max(0, int((sh - height * wscale) / 2 - 20 * wscale))
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.minsize(min(980, width), min(640, height))

    def _make_brand_images(self) -> None:
        logo = logo_image  # shared with the tray icon and the exe icon

        def avatar(px: int) -> Image.Image:
            ss = 4
            big = px * ss
            img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            d.ellipse((0, 0, big - 1, big - 1), fill=_rgb(tint(CYAN, 0.18, SURFACE)) + (255,),
                      outline=_rgb(tint(CYAN, 0.55, SURFACE)) + (255,), width=ss)
            bolt = self.icons.pil("bolt", int(big * 0.5), CYAN)
            if bolt is not None:
                img.alpha_composite(bolt, ((big - bolt.width) // 2, (big - bolt.height) // 2))
            return img.resize((px, px), Image.LANCZOS)

        logo_px = self.px(34)
        self.logo = ctk.CTkImage(light_image=logo(logo_px * 2), dark_image=logo(logo_px * 2), size=(34, 34))
        self.avatar = ctk.CTkImage(light_image=avatar(self.px(30) * 2), dark_image=avatar(self.px(30) * 2),
                                   size=(30, 30))
        self.avatar_large = ctk.CTkImage(light_image=avatar(self.px(56) * 2), dark_image=avatar(self.px(56) * 2),
                                         size=(56, 56))
        self.avatar_photo = ImageTk.PhotoImage(avatar(self.px(30)))  # canvas versions (chat view)
        self.avatar_large_photo = ImageTk.PhotoImage(avatar(self.px(56)))
        try:
            self._icon_photos = [ImageTk.PhotoImage(logo(64)), ImageTk.PhotoImage(logo(32))]
            self._iconbitmap_method_called = True  # stop CTk from swapping in its default icon
            self.iconphoto(True, *self._icon_photos)
            if sys.platform == "win32":
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Willy.PC.CommandCenter")
        except Exception:
            pass

    # ---------------------------------------------------------- styling

    def button_style(self, kind: str) -> Dict[str, Any]:
        return {
            "primary": dict(fg_color=CYAN, hover_color="#67E8F9", text_color=BG, border_width=0),
            "secondary": dict(fg_color=CARD_ALT, hover_color=BORDER, text_color=TEXT, border_width=1,
                              border_color=BORDER),
            "ghost": dict(fg_color="transparent", hover_color=CARD_ALT, text_color=MUTED, border_width=0),
            "success": dict(fg_color=GREEN, hover_color="#34D399", text_color=BG, border_width=0),
            "danger": dict(fg_color=RED, hover_color="#F87171", text_color="#FFFFFF", border_width=0),
            "outline": dict(fg_color="transparent", hover_color=tint(CYAN, 0.12, SURFACE), text_color=CYAN,
                            border_width=1, border_color=tint(CYAN, 0.45, SURFACE)),
        }[kind]

    def button(self, parent: tk.Misc, text: str, command: Callable[[], None], *, icon: Optional[str] = None,
               kind: str = "secondary", width: int = 0, height: int = 34, icon_color: Optional[str] = None,
               font_size: int = 12) -> ctk.CTkButton:
        style = self.button_style(kind)
        color = icon_color or {"primary": BG, "success": BG, "danger": "#FFFFFF", "outline": CYAN,
                               "ghost": MUTED}.get(kind, SOFT)
        image = self.icons.get(icon, 14, color) if icon else None
        return ctk.CTkButton(parent, text=text, command=command, image=image, compound="left",
                             width=width or (height if not text else 28), height=height, corner_radius=10,
                             font=self.fonts.s(font_size) if kind in ("primary", "success", "danger")
                             else self.fonts(font_size), **style)

    def slider_style(self, accent: str) -> Dict[str, Any]:
        return dict(fg_color=BORDER, progress_color=accent, button_color=TEXT, button_hover_color=accent)

    def card(self, parent: tk.Misc, bg: str = CARD) -> ctk.CTkFrame:
        return ctk.CTkFrame(parent, fg_color=bg, corner_radius=16, border_width=1, border_color=BORDER)

    def card_header(self, card: ctk.CTkFrame, title: str, icon: Optional[str] = None,
                    note: Optional[str] = None) -> tk.Frame:
        F = self.fonts
        head = tk.Frame(card, bg=card.cget("fg_color"))
        head.pack(fill="x", padx=16, pady=(12, 8))
        if icon:
            ctk.CTkLabel(head, text="", image=self.icons.get(icon, 14, DIM), width=16, height=20).pack(side="left")
        ctk.CTkLabel(head, text=title.upper(), font=F.s(11), text_color=MUTED, height=20).pack(
            side="left", padx=(8 if icon else 0, 0))
        if note:
            ctk.CTkLabel(head, text=f"· {note}", font=F(11), text_color=DIM, height=20).pack(side="left", padx=(6, 0))
        slot = tk.Frame(head, bg=card.cget("fg_color"))
        slot.pack(side="right")
        return slot

    def pill_image(self, width: int, height: int, fill: Optional[str], outline: str,
                   radius: int = 8) -> ImageTk.PhotoImage:
        """Antialiased rounded pill (transparent outside) for canvas buttons."""
        ss = 3
        w, h = self.px(width), self.px(height)
        img = Image.new("RGBA", (w * ss, h * ss), (0, 0, 0, 0))
        ImageDraw.Draw(img).rounded_rectangle(
            (0, 0, w * ss - 1, h * ss - 1), radius=self.px(radius) * ss,
            fill=_rgb(fill) + (255,) if fill else None, outline=_rgb(outline) + (255,), width=ss)
        return ImageTk.PhotoImage(img.resize((w, h), Image.LANCZOS))

    def legend(self, parent: tk.Misc, color: str, text: str) -> ctk.CTkLabel:
        img = Image.new("RGBA", (self.px(10) * 3, self.px(10) * 3), (0, 0, 0, 0))
        ImageDraw.Draw(img).ellipse((0, 0, img.width - 1, img.height - 1), fill=_rgb(color) + (255,))
        img = img.resize((self.px(10), self.px(10)), Image.LANCZOS)
        return ctk.CTkLabel(parent, text=f" {text}", image=ctk.CTkImage(img, img, size=(10, 10)), compound="left",
                            font=self.fonts(12), text_color=SOFT, height=20)

    def page_header(self, parent: tk.Misc, title: str, subtitle: str) -> Tuple[tk.Frame, tk.Frame, ctk.CTkLabel]:
        F = self.fonts
        head = tk.Frame(parent, bg=BG)
        head.grid_columnconfigure(0, weight=1)
        left = tk.Frame(head, bg=BG)
        left.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(left, text=title, font=F.s(22), text_color=TEXT, anchor="w", height=30).pack(anchor="w")
        sub = ctk.CTkLabel(left, text=subtitle, font=F(12), text_color=MUTED, anchor="w", height=16)
        sub.pack(anchor="w")
        right = tk.Frame(head, bg=BG)
        right.grid(row=0, column=1, sticky="e")
        return head, right, sub

    # ------------------------------------------------------------ header

    def _build_header(self) -> None:
        F = self.fonts
        header = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=0, height=64)
        header.grid(row=0, column=0, columnspan=2, sticky="ew")
        header.grid_propagate(False)
        header.grid_columnconfigure(1, weight=1)
        header.grid_rowconfigure(0, weight=1)
        tk.Frame(self, bg=BORDER, height=1).grid(row=0, column=0, columnspan=2, sticky="sew")

        brand = tk.Frame(header, bg=SURFACE)
        brand.grid(row=0, column=0, sticky="w", padx=(18, 0))
        ctk.CTkLabel(brand, text="", image=self.logo, width=34, height=34).grid(row=0, column=0, rowspan=2,
                                                                              padx=(0, 12))
        title = tk.Frame(brand, bg=SURFACE)
        title.grid(row=0, column=1, sticky="sw")
        ctk.CTkLabel(title, text="WILLY", font=F(17, "bold"), text_color=TEXT, height=22).pack(side="left")
        ctk.CTkLabel(title, text=" PC", font=F(17, "bold"), text_color=CYAN, height=22).pack(side="left")
        self.device_label = ctk.CTkLabel(brand, text=f"{self.node.device_name} · {self.node._platform_name()}",
                                         font=F(12), text_color=MUTED, height=16, anchor="w")
        self.device_label.grid(row=1, column=1, sticky="nw")

        right = tk.Frame(header, bg=SURFACE)
        right.grid(row=0, column=2, sticky="e", padx=(0, 16))
        status = tk.Frame(right, bg=SURFACE)
        status.pack(side="left", padx=(0, 14))
        self.badge = ctk.CTkLabel(status, text="●  Connecting…", font=F(12, "bold"), corner_radius=12, height=26,
                                  padx=12, text_color=AMBER, fg_color=tint(AMBER, 0.14, SURFACE))
        self.badge.pack(anchor="e")
        self.hub_label = ctk.CTkLabel(status, text=self._short_hub(), font=F(11), text_color=DIM, height=16)
        self.hub_label.pack(anchor="e", pady=(2, 0))
        self.button(right, "Open Dashboard", self.open_dashboard, icon="dashboard", kind="outline",
                    height=36).pack(side="left", padx=(0, 8))
        self.button(right, "Voice Call", self.open_voice_call, icon="call", kind="success", height=36).pack(
            side="left")

    def _short_hub(self) -> str:
        parsed = urllib.parse.urlparse(self.node.server_url)
        host = parsed.netloc or self.node.server_url
        lock = "🔒 " if parsed.scheme == "wss" else ""
        return f"{lock}hub · {ellipsize(host, 32)}"

    def _web_url(self, path: str) -> str:
        return f"{self.node.api_base_url}{path}#token={urllib.parse.quote(self.node.token, safe='')}"

    def open_dashboard(self) -> None:
        url = self._web_url("/dashboard")
        self.local_log("[UI] Opening the web dashboard")
        self.run_bg(webbrowser.open, url)

    def open_voice_call(self) -> None:
        url = self._web_url("/call")
        self.local_log("[UI] Opening Voice Call in the browser")
        self.run_bg(webbrowser.open, url)

    # ------------------------------------------------------------ sidebar

    def _build_sidebar(self) -> None:
        F = self.fonts
        side = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=0, width=208)
        side.grid(row=1, column=0, sticky="nsw")
        side.grid_propagate(False)
        side.grid_columnconfigure(0, weight=1)
        tk.Frame(self, bg=BORDER, width=1).grid(row=1, column=0, sticky="nse")
        ctk.CTkLabel(side, text="MENU", font=F.s(10), text_color=DIM, anchor="w", height=16).grid(
            row=0, column=0, sticky="w", padx=22, pady=(18, 6))
        self.nav: Dict[str, ctk.CTkButton] = {}
        for i, (key, label, _cls) in enumerate(self.PAGES, start=1):
            btn = ctk.CTkButton(side, text=f"  {label}", image=self.icons.get(key, 17, MUTED), compound="left",
                                anchor="w", height=40, corner_radius=10, font=F(13), fg_color="transparent",
                                hover_color=CARD, text_color=MUTED, command=lambda k=key: self.show_page(k))
            btn.grid(row=i, column=0, sticky="ew", padx=12, pady=2)
            self.nav[key] = btn
        self.activity_badge = ctk.CTkLabel(side, text="", font=F(10, "bold"), fg_color=PURPLE, text_color="#FFFFFF",
                                           corner_radius=9, height=18, width=24, bg_color=SURFACE)
        side.grid_rowconfigure(len(self.PAGES) + 1, weight=1)

        card = ctk.CTkFrame(side, fg_color=CARD, corner_radius=14, border_width=1, border_color=BORDER)
        card.grid(row=len(self.PAGES) + 2, column=0, sticky="ew", padx=12, pady=14)
        ctk.CTkLabel(card, text="HUB SESSION", font=F.s(10), text_color=DIM, anchor="w", height=14).pack(
            anchor="w", padx=14, pady=(12, 4))
        self.side_status = ctk.CTkLabel(card, text="Connecting…", font=F.s(13), text_color=AMBER, anchor="w",
                                        height=20)
        self.side_status.pack(anchor="w", padx=14)
        self.side_detail = ctk.CTkLabel(card, text="", font=F(11), text_color=MUTED, anchor="w", justify="left",
                                        height=16, wraplength=160)
        self.side_detail.pack(anchor="w", padx=14, pady=(2, 0))
        self.side_actions = ctk.CTkLabel(card, text="", font=F(11), text_color=MUTED, anchor="w", height=16)
        self.side_actions.pack(anchor="w", padx=14, pady=(2, 12))

    def _nav_style(self) -> None:
        for key, btn in self.nav.items():
            active = key == self.current
            btn.configure(fg_color=tint(CYAN, 0.10, SURFACE) if active else "transparent",
                          text_color=TEXT if active else MUTED,
                          hover_color=tint(CYAN, 0.14, SURFACE) if active else CARD,
                          image=self.icons.get(key, 17, CYAN if active else MUTED))
        self._update_activity_badge()

    def _update_activity_badge(self) -> None:
        if self.current == "activity":
            self._unseen_activity = 0
        if self._unseen_activity:
            self.activity_badge.configure(text=str(min(self._unseen_activity, 99)))
            self.activity_badge.place(in_=self.nav["activity"], relx=1.0, x=-10, rely=0.5, anchor="e")
            self.activity_badge.lift()
        else:
            self.activity_badge.place_forget()

    def show_page(self, key: str) -> None:
        if key == self.current or key not in self.pages or self.closing:
            return
        page = self.pages[key]
        page.ensure_built()
        if self.current:
            old = self.pages[self.current]
            old.visible = False
            old.grid_remove()
            old.on_hide()
        page.grid(row=0, column=0, sticky="nsew")
        page.visible = True
        self.current = key
        self._nav_style()
        page.on_show()

    def _prebuild_pages(self) -> None:
        """Builds hidden pages one per idle slice, so first visits are instant."""
        if self.closing:
            return
        for page in self.pages.values():
            if not page.built:
                page.ensure_built()
                self.after(120, self._prebuild_pages)
                return

    def _focus_assistant(self) -> None:
        self.show_page("assistant")
        page = self.pages["assistant"]
        if isinstance(page, AssistantPage):
            page.focus_input()

    def _on_mousewheel(self, event: Any) -> None:
        widget = event.widget
        if isinstance(widget, str):
            try:
                widget = self.nametowidget(widget)
            except (KeyError, tk.TclError):
                return
        while widget is not None:
            if getattr(widget, "is_scroll_area", False):
                widget.scroll_pixels(int(-event.delta * 0.4))
                return
            widget = getattr(widget, "master", None)

    # ------------------------------------------------------------ node

    def _start_node(self) -> None:
        def run() -> None:
            # The engine only returns when stopped or replaced; after an unexpected crash it is
            # started again, so a tray app left running for days never sits disconnected.
            while not self.closing and self.node.running:
                try:
                    asyncio.run(self.node.run())
                    return
                except Exception:
                    self.post(self._report, traceback.format_exc())
                    self.post(self._on_status, "offline", "Engine restarting")
                time.sleep(5)

        self._node_thread = threading.Thread(target=run, name="willy-node", daemon=True)
        self._node_thread.start()

    def _on_status(self, state: str, detail: str) -> None:
        self._status = (state, detail or "")
        self._status_at = time.monotonic()
        if state == "online":
            if self._hub_down_alerted:
                self.toast("Willy server", "Your Willy server is reachable again.", kind="success")
            self._hub_down_since, self._hub_down_alerted = None, False
        elif self._hub_down_since is None:
            self._hub_down_since = time.time()
        if state == "online":
            self._last_error = ""
        self._render_status()
        if state == "online":
            devices = self.pages["devices"]
            if isinstance(devices, DevicesPage):
                devices.sync()
        phone = self.pages["phone"]
        if isinstance(phone, PhonePage):
            phone.sync()  # phone actions need this PC's own hub connection too
        server = self.pages.get("server")
        if isinstance(server, ServerPage):
            server.sync_device()  # so do the server's files, terminal and controls

    def _render_status(self) -> None:
        state, detail = self._status
        node = self.node
        if state != "online" and not node.running and not self.closing:
            # Another Willy client for this PC took over the hub slot; this one waits for Reconnect.
            text, color = "●  Stopped", RED
            side, side_color = "Stopped", RED
            detail_text = "Another Willy client took over.\nUse Reconnect in the tray menu."
            state = "stopped"
        elif state == "online":
            latency = node.latency_ms
            text, color = (f"●  Online · {latency} ms" if latency is not None else "●  Online"), GREEN
            side, side_color = "Online", GREEN
            info = self.server_info
            llm = info.get("llm") or {}
            parts = [f"{latency} ms" if latency is not None else None,
                     f"hub v{info['version']}" if info.get("version") else None,
                     ("LLM ready" if llm.get("ready") else "LLM offline") if llm else None]
            since = node.connected_since
            detail_text = " · ".join(p for p in parts if p)
            if since:
                detail_text += f"\nConnected {fmt_duration(time.time() - since)}"
        elif state == "connecting":
            text, color = "●  Connecting…", AMBER
            side, side_color, detail_text = "Connecting…", AMBER, self._short_hub()
        else:
            match = re.search(r"in (\d+)s", detail)
            if match:
                remaining = max(0, int(match.group(1)) - int(time.monotonic() - self._status_at))
                text = f"●  Offline · retry in {remaining}s"
            else:
                text = "●  Offline · reconnecting"
            color = RED
            side, side_color = "Offline", RED
            if "rejected" in self._last_error:
                detail_text = "Token rejected — check WILLY_REMOTE_TOKEN."
            else:
                detail_text = "Hub unreachable — retrying automatically."
        self._text(self.badge, "badge", text, text_color=color, fg_color=tint(color, 0.14, SURFACE))
        self._text(self.side_status, "side_status", side, text_color=side_color)
        self._text(self.side_detail, "side_detail", detail_text)
        handled = node.actions_handled
        self._text(self.side_actions, "side_actions", f"{handled} action{'s' if handled != 1 else ''} handled")
        self.tray.set_status(state, node.latency_ms if state == "online" else None)

    def _text(self, widget: Any, key: str, text: str, **extra: Any) -> None:
        if self._texts.get(key) != text:
            self._texts[key] = text
            widget.configure(text=text, **extra)

    def _on_log(self, ts: str, text: str) -> None:
        text = str(text).rstrip()
        if not text:
            return
        if text.startswith("[-]"):
            self._last_error = text
        self.log_lines.append((ts, text))
        try:  # the console, or %LOCALAPPDATA%\WillyPC\willy-pc.log when started without one
            print(f"{ts} {text}")
        except Exception:
            pass
        page = self.pages.get("log") if hasattr(self, "pages") else None
        if isinstance(page, LogPage):
            page.append(ts, text)

    def _on_event(self, event: Dict[str, Any]) -> None:
        kind = event.get("type")
        overview = self.pages["overview"]
        if kind == "snapshot":
            self.server_info = event.get("server") or {}
            for page_key in ("devices", "activity", "phone"):
                page = self.pages[page_key]
                if isinstance(page, (DevicesPage, ActivityPage, PhonePage)):
                    page.sync()
            server = self.pages.get("server")
            if isinstance(server, ServerPage):
                server.sync_device()
            own = next((d for d in event.get("devices") or [] if d.get("device_id") == self.node.device_id), None)
            if own and isinstance(overview, OverviewPage) and not self._history_seeded:
                self._history_seeded = True
                overview.seed_history(own.get("history") or {}, self.server_info.get("server_time"))
            self._render_status()
        elif kind in ("device_discovered", "device_update", "device_offline"):
            dev = event.get("device")
            if not isinstance(dev, dict):
                dev = self.node.devices.get(event.get("device_id") or "")
            if isinstance(dev, dict):
                devices = self.pages["devices"]
                if isinstance(devices, DevicesPage):
                    devices.upsert(dev)
                phone = self.pages["phone"]
                if isinstance(phone, PhonePage):
                    phone.on_device(dev)
                server = self.pages.get("server")
                if isinstance(server, ServerPage):
                    server.on_device(dev)
        elif kind == "presence_alert":
            self._on_presence_alert(event)
        elif kind == "activity":
            entry = event.get("entry") or {}
            page = self.pages["activity"]
            if isinstance(page, ActivityPage):
                page.upsert(entry)
            if self.current != "activity" and entry.get("status") == "running":
                self._unseen_activity += 1
                self._update_activity_badge()
        elif kind == "reminder_due":
            self.local_log(f"[Reminder] {event.get('title') or 'Reminder'}: {event.get('message') or ''}")

    def _on_presence_alert(self, event: Dict[str, Any]) -> None:
        """Watchdog alert from the hub (a device went offline or came back): a pop-up (not for
        this PC's own alerts, which are only logged), and the Phone page re-reads its state."""
        back = event.get("event") == "online"
        name = event.get("device_name") or "A device"
        title = str(event.get("title") or f"{name} {'is back online' if back else 'went offline'}")
        message = str(event.get("message") or event.get("reason_text") or "")
        reply = " ".join(str(event.get("reply") or "").split())  # answer to a "... then tell me" follow-up
        self.local_log(f"[*] {title}" + (f" — {message}" if message else "") + (f" {reply}" if reply else ""))
        if event.get("device_id") != self.node.device_id:
            self.toast(title, f"{message} {reply}".strip(), kind="phone" if event.get("device_type") == "mobile"
                       else "info", duration=9.0 if reply else 6.0)
        phone = self.pages["phone"]
        if isinstance(phone, PhonePage):
            phone.sync()

    def _on_telemetry(self, telemetry: Dict[str, Any]) -> None:
        self.telemetry = telemetry
        self._telemetry_at = time.monotonic()
        overview = self.pages["overview"]
        if isinstance(overview, OverviewPage):
            overview.update_telemetry(telemetry)
        self._render_status()

    def _sample_offline(self) -> None:
        """The engine only samples inside its connected heartbeat loop; keep the dashboard
        live from the same collector while the hub is unreachable."""
        if self.node.connected or self._local_sampling or \
                time.monotonic() - self._telemetry_at < HEARTBEAT_INTERVAL_SEC + 1:
            return
        self._local_sampling = True

        def done(telemetry: Any) -> None:
            self._local_sampling = False
            if isinstance(telemetry, dict) and not self.node.connected:
                self._on_telemetry({**telemetry, "last_ping_ms": None})

        self.run_bg(self.node.collector.collect, on_done=done,
                    on_error=lambda _e: setattr(self, "_local_sampling", False))

    def _on_notify(self, title: str, message: str, query: Optional[str]) -> None:
        title = re.sub(r"^\s*⚡\s*", "", str(title or "Willy")).strip() or "Willy"
        lowered = title.lower()
        kind = "reminder" if ("⏰" in title or "reminder" in lowered) else \
            "phone" if "find my" in lowered else "success" if lowered.startswith(("process ended", "screenshot")) \
            else "info"
        self.toast(title, str(message or ""), query, kind=kind, duration=9.0 if kind == "reminder" else 4.5)

    def _got_specs(self, specs: Dict[str, Any]) -> None:
        if not isinstance(specs, dict):
            return
        self.specs = {**self.specs, **specs}
        build = str(self.specs.get("os_version") or "").split(".")[-1]
        os_name = self.specs.get("os") or self.node._platform_name()
        self.device_label.configure(text=f"{self.node.device_name} · {os_name}" + (f" · build {build}" if build
                                                                                   else ""))
        tools = self.pages["tools"]
        if isinstance(tools, ToolsPage):
            tools.update_specs()
        if self.specs.get("has_battery") and not self._brightness_checked:
            self._brightness_checked = True
            self.run_bg(self._read_brightness, on_done=self._got_brightness)

    @staticmethod
    def _read_brightness() -> Optional[int]:
        """Current panel brightness (read-only WMI query); None if not software-controllable."""
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                 "(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness -ErrorAction Stop)"
                 ".CurrentBrightness | Select-Object -First 1"],
                capture_output=True, text=True, timeout=10, creationflags=CREATE_NO_WINDOW,
            ).stdout.strip()
            return max(0, min(100, int(out))) if out.isdigit() else None
        except Exception:
            return None

    def _got_brightness(self, level: Optional[int]) -> None:
        overview = self.pages["overview"]
        if isinstance(overview, OverviewPage) and level is not None:
            overview.enable_brightness(level)

    # ------------------------------------------------------------ periodic

    def _tick(self) -> None:
        if self.closing:
            return
        state = self._status[0]
        if state != "online" or not self.telemetry:
            self._render_status()
        self._sample_offline()
        page = self.pages.get(self.current or "")
        if isinstance(page, (DevicesPage, ActivityPage, PhonePage, ServerPage)):
            page.tick()
        self._ticks += 1
        if self._ticks % 30 == 0:  # Start with Windows can also change in Task Manager
            self.tray.refresh_autostart()
            self._check_hub_down()
        self._jobs["tick"] = self.after(1000, self._tick)

    HUB_DOWN_ALERT_SEC = 180

    def _check_hub_down(self) -> None:
        """Warns once when the hub has been unreachable for 3 minutes while the internet works."""
        since = self._hub_down_since
        if since is None or self._hub_down_alerted or time.time() - since < self.HUB_DOWN_ALERT_SEC:
            return

        def internet_ok() -> bool:
            import socket

            for host, port in (("1.1.1.1", 443), ("8.8.8.8", 53)):
                try:
                    socket.create_connection((host, port), timeout=3).close()
                    return True
                except OSError:
                    continue
            return False

        def done(ok: bool) -> None:
            if ok and self._hub_down_since is not None and not self._hub_down_alerted:
                self._hub_down_alerted = True
                host = urllib.parse.urlparse(self.node.server_url).hostname or "the hub"
                minutes = max(3, round((time.time() - self._hub_down_since) / 60))
                self.toast("Willy server down?", f"Can't reach your Willy server ({host}) for {minutes} minutes, "
                           "though this PC's internet works. The server or its app may be down.", kind="error")
                self.local_log(f"[!] Hub unreachable for {minutes} min while the internet works")

        self.run_bg(internet_ok, on_done=done)

    # ------------------------------------------------------------ actions

    def run_action(self, action: str, payload: Dict[str, Any], label: str,
                   on_result: Optional[Callable[[Dict[str, Any]], None]] = None) -> None:
        """Runs an executor action locally in a worker; the executor shows its own pop-up."""
        self.local_log(f"[UI] {label}")

        def done(res: Any) -> None:
            res = res if isinstance(res, dict) else {"success": True}
            ok = res.get("success", True) is not False
            message = res.get("message") or res.get("reply") or res.get("error") or ("Done." if ok else "Failed.")
            self.local_log(f"[UI] {label}: {'ok' if ok else 'failed'} — {ellipsize(message, 120)}")
            if not ok:
                self.toast(label, str(message), kind="error")
            if on_result:
                on_result(res)

        self.run_bg(self.node.executor.execute_action, action, payload, on_done=done,
                    on_error=lambda e: done({"success": False, "error": str(e)}))

    def device_action(self, device_id: str, action: str, payload: Dict[str, Any], success_text: str,
                      label: str, on_result: Optional[Callable[[Dict[str, Any]], None]] = None) -> None:
        """Sends a direct action to another device through the hub (e.g. ring the phone) and pops
        up the device's answer. A phone that needs a tap to finish (Android blocks some starts
        from the background) answers needs_tap: that shows as a phone pop-up, not a success.
        on_result gets the result dict on the Tk thread."""
        fut = self.node.submit(self.node.send_action(device_id, action, payload))
        if fut is None:
            self.toast(label, "The Willy engine isn't running.", kind="error")
            if on_result:
                on_result({"success": False, "error": "The Willy engine isn't running."})
            return
        self.local_log(f"[UI] {label}")

        def done(f: Any) -> None:
            try:
                res = f.result()
            except Exception as e:
                res = {"success": False, "error": str(e)}
            if not isinstance(res, dict):
                res = {"success": False, "error": "The device sent an unexpected reply."}
            ok = res.get("success", True) is not False
            detail = str((res.get("message") or success_text) if ok else (res.get("reply") or res.get("error")
                                                                          or "The device didn't respond."))
            tap = ok and bool(res.get("needs_tap"))
            if not ok:
                self.local_log(f"[UI] {label}: failed — {ellipsize(detail, 120)}")
            self.toast(label, detail, kind="phone" if tap else "success" if ok else "error",
                       duration=7.0 if tap else 4.5)
            if on_result:
                on_result(res)

        fut.add_done_callback(lambda f: self.post(done, f))

    def ring_phone(self) -> None:
        """Tray menu: rings the phone (Find My Phone), the same phone the Phone page follows."""
        page = self.pages.get("phone")
        phone = pick_phone(dict(self.node.devices), page._phone_id if isinstance(page, PhonePage) and page.built
                           else None)
        if phone is None or not device_online(phone):
            self.toast("No phone connected", "Open Willy on your Android phone so this PC can ring it.",
                       kind="error")
            return
        name = phone.get("name") or "your phone"
        self.device_action(phone["device_id"], "ring_device", dict(PHONE_RING), f"Ringing {name}…", f"Ring {name}")

    # tools page actions ---------------------------------------------------

    def tool_admin_powershell(self) -> None:
        def launch() -> str:
            os.startfile("powershell.exe", "runas")  # type: ignore[attr-defined]
            return "Admin PowerShell launched."

        self.local_log("[UI] Launching elevated PowerShell (UAC prompt)…")
        self.run_bg(launch, on_done=lambda msg: self.local_log(f"[UI] {msg}"),
                    on_error=lambda e: self.local_log(f"[-] Admin PowerShell cancelled or failed: {e}"))

    def tool_task_manager(self) -> None:
        self.local_log("[UI] Opening Task Manager…")
        # ShellExecute handles Task Manager's elevation manifest (CreateProcess would fail with error 740).
        self.run_bg(os.startfile, "taskmgr.exe",  # type: ignore[attr-defined]
                    on_error=lambda e: self.toast("Task Manager", str(e), kind="error"))

    def tool_flush_dns(self) -> None:
        def flush() -> str:
            res = subprocess.run(["ipconfig", "/flushdns"], capture_output=True, text=True, timeout=20,
                                 creationflags=CREATE_NO_WINDOW, errors="replace")
            out = " ".join(line.strip() for line in (res.stdout or res.stderr).splitlines() if line.strip())
            return out or "DNS cache flushed."

        self.local_log("[UI] Flushing the DNS resolver cache…")
        self.run_bg(flush, on_done=lambda msg: (self.local_log(f"[UI] {msg}"),
                                                self.toast("Flush DNS", ellipsize(msg, 140), kind="success")),
                    on_error=lambda e: self.toast("Flush DNS", str(e), kind="error"))

    def tool_system32(self) -> None:
        path = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
        self.local_log(f"[UI] Opening {path}")
        self.run_bg(os.startfile, path,  # type: ignore[attr-defined]
                    on_error=lambda e: self.toast("System32", str(e), kind="error"))

    def tool_screenshot(self) -> None:
        self.run_action("take_screenshot", {}, "Screenshot")

    def tool_lock(self) -> None:
        self.run_action("power_action", {"action": "lock"}, "Lock PC")

    def tool_toggle_mute(self) -> None:
        muted = self.telemetry.get("is_muted")
        action = "unmute" if muted else "mute" if muted is False else "toggle_mute"
        self.run_action("volume_control", {"action": action}, "Mute / Unmute")

    def tool_specs_summary(self) -> None:
        t, s = self.telemetry, self.specs
        bat = _num(t.get("battery_pct"))
        parts = [f"Battery {bat:.0f}%{' (charging)' if t.get('is_charging') else ''}" if bat is not None
                 else "AC power (no battery)"]
        if t.get("cpu_pct") is not None:
            parts.append(f"CPU {t['cpu_pct']:.0f}%")
        if t.get("ram_pct") is not None:
            parts.append(f"RAM {t['ram_pct']:.0f}% of {t.get('ram_total_gb')} GB")
        if t.get("disk_free_gb") is not None:
            parts.append(f"{t['disk_free_gb']:.0f} GB free")
        summary = " · ".join(parts)
        model = s.get("cpu_model") or ""
        self.local_log(f"[UI] {summary}" + (f" · {model}" if model else ""))
        self.toast("Battery & specs", summary + (f"\n{model}" if model else ""), kind="info", duration=6)

    # ------------------------------------------------------------ audio

    def play_audio(self, audio_b64: str) -> None:
        """Plays a reply's MP3 with pygame when available; silently skipped otherwise."""

        def play() -> None:
            with self._audio_lock:
                state = self._audio_state
                if state["ready"] is None:
                    try:
                        import pygame
                        pygame.mixer.init()
                        state["ready"] = True
                    except Exception:
                        state["ready"] = False
                if not state["ready"]:
                    return
                import pygame
                buffer = io.BytesIO(base64.b64decode(audio_b64))
                pygame.mixer.music.load(buffer, "mp3")
                pygame.mixer.music.play()
                state["buffer"] = buffer  # keep alive while playing

        self.run_bg(play, on_error=lambda e: self.local_log(f"[-] Audio playback failed: {e}"))

    def _stop_audio(self) -> None:
        if not self._audio_state.get("ready"):
            return
        try:
            import pygame
            pygame.mixer.music.stop()
            pygame.mixer.quit()
        except Exception:
            pass

    # ------------------------------------------------------------ tray

    def _on_window_close(self) -> None:
        """The title-bar X keeps Willy running in the tray, so the phone can still reach this
        PC; Quit is in the tray menu. Without a tray icon, closing quits."""
        if self.tray.running and not self.closing:
            self.hide_window()
        else:
            self.quit_app()

    def hide_window(self, hint: bool = True) -> None:
        if self.closing or self.window_hidden:
            return
        self.window_hidden = True
        page = self.pages.get(self.current or "")
        if page is not None and page.visible:
            page.visible = False  # no drawing work while nobody can see it
            page.on_hide()
        self.withdraw()
        if hint and not self._tray_hint_shown:
            self._tray_hint_shown = True
            self.toast("Willy is still running",
                       "It's in the hidden icons (^) on the taskbar, so your phone can still reach this PC. "
                       "Right-click the icon to quit.", kind="info", duration=7)

    def show_window(self) -> None:
        if self.closing:
            return
        was_hidden, self.window_hidden = self.window_hidden, False
        self.deiconify()
        if self.state() == "iconic":
            self.state("normal")
        self.lift()
        try:  # topmost for a moment: reliably in front of whatever had the focus
            self.attributes("-topmost", True)
            self.after(150, lambda: self.closing or self.attributes("-topmost", False))
        except tk.TclError:
            pass
        self.focus_force()
        page = self.pages.get(self.current or "")
        if was_hidden and page is not None and not page.visible:
            page.visible = True
            page.on_show()

    def reconnect_hub(self) -> None:
        if self.closing:
            return
        if not self.node.reconnect():
            if self._node_thread is not None and self._node_thread.is_alive():
                return  # still winding down; the next click restarts it
            self.node.running = True  # stopped after another client took over: start again
            self._start_node()
        host = urllib.parse.urlparse(self.node.server_url).netloc or self.node.server_url
        self.local_log(f"[UI] Reconnecting to {host}…")
        self.toast("Reconnecting", f"Connecting to {host}…", kind="info", duration=3)

    def _autostart_changed(self, result: Dict[str, Any]) -> None:
        if not result.get("success"):
            self.toast("Start with Windows", str(result.get("error") or "Couldn't change the startup setting."),
                       kind="error")
            return
        if result.get("enabled"):
            message = "Willy will start in the tray when you sign in to Windows."
            if result.get("replaced_legacy"):
                message += " It replaces the old Willy assistant's startup entry."
            self.local_log("[UI] Start with Windows: on" + (" (replaced the old assistant's entry)"
                                                            if result.get("replaced_legacy") else ""))
        else:
            message = "Willy won't start automatically any more."
            self.local_log("[UI] Start with Windows: off")
        self.toast("Start with Windows", message, kind="success")

    # ------------------------------------------------------------ shutdown

    # ------------------------------------------------------------ Hey Willy
    def start_hey_willy(self) -> None:
        if self.voice is not None or self.closing:
            return
        self.voice = HeyWilly(on_state=lambda st, detail: self.post(self._voice_state, st, detail),
                              on_reply=lambda reply: self.post(self._voice_reply, reply))
        self.voice.muted = bool(prefs.get("mic_muted"))
        self.voice.profile = prefs.get("wake_profile")
        self.voice.on_trained = lambda outcome: self.post(self._voice_trained, outcome)
        self.voice.start()

    def toggle_hey_willy(self) -> None:
        if self.voice is not None:
            self.voice.stop()
            self.voice = None
            prefs.set("hey_willy", False)
            self._voice_state("off", "")
            self.toast("Hey Willy", "Off. The microphone is closed.")
        else:
            prefs.set("hey_willy", True)
            self.start_hey_willy()
            self.toast("Hey Willy", 'On. Say "Hey Willy" any time.', kind="success")
        self.tray.refresh()

    def toggle_mic_mute(self) -> None:
        if self.voice is None:
            self.toast("Hey Willy", "Hey Willy is off; turn it on from the tray menu first.")
            return
        self.voice.set_muted(not self.voice.muted)
        prefs.set("mic_muted", self.voice.muted)
        self.toast("Microphone", "Muted. Willy won't listen until you unmute." if self.voice.muted
                   else 'Listening for "Hey Willy".')
        self.tray.refresh()

    def pair_with_account(self) -> None:
        """Opens the hub's sign-in page for this PC; once approved, this PC gets its own key."""
        if getattr(self, "_pairing", False):
            self.toast("Sign in", "Pairing is already open in your browser. Approve it there.")
            return
        self._pairing = True
        self.toast("Sign in", "Opening your browser. Sign in with Google and approve this PC.")

        def work() -> None:
            from pc_client import pairing

            res = pairing.pair_device(
                self.node.api_base_url, self.node.device_id, "pc", self.node.device_name,
                on_code=lambda code, _url: self.post(self.toast, "Pairing code", f"{code}: check it matches in the browser."),
                should_stop=lambda: self.closing)
            self.post(self._paired, res)

        threading.Thread(target=work, name="willy-pair", daemon=True).start()

    def _paired(self, res: Dict[str, Any]) -> None:
        self._pairing = False
        if res.get("success"):
            self.node._save_new_token(res["device_key"], "This PC is now paired with your account")
            self.toast("Signed in", f"This PC belongs to {res.get('email') or 'your account'} now.", kind="success")
        else:
            self.toast("Sign in", res.get("error") or "Pairing didn't finish.", kind="error")

    def train_hey_willy(self) -> None:
        if self.voice is None:
            prefs.set("hey_willy", True)
            self.start_hey_willy()
            self.tray.refresh()
            self.after(3000, lambda: self.voice and self.voice.train())
        else:
            self.voice.train()
        self.toast("Train Hey Willy", "After each beep, say \u201cHey Willy\u201d normally, as you usually would. 5 times.")

    def _voice_trained(self, outcome: Dict[str, Any]) -> None:
        if outcome.get("success"):
            prefs.set("wake_profile", outcome["profile"])
            self.toast("Hey Willy", outcome["message"], kind="success")
        else:
            self.toast("Hey Willy", outcome.get("error") or "Training didn't work.", kind="error")

    def talk_now(self) -> None:
        if self.voice is None:
            self.start_hey_willy()
            self.after(1500, lambda: self.voice and self.voice.trigger())
        else:
            self.voice.trigger()

    def _voice_state(self, state: str, detail: str) -> None:
        if self.closing:
            return
        if state == "error":
            self.toast("Hey Willy", detail or "The microphone isn't available.", kind="error")
        elif state == "loading":
            self.toast("Hey Willy", detail)
        if self._voice_pill is None:
            self._voice_pill = VoicePill(self)
        self._voice_pill.show(state, detail)

    def _voice_reply(self, reply: Dict[str, Any]) -> None:
        if self.closing:
            return
        text = str(reply.get("reply") or reply.get("error") or "")
        heard = str(reply.get("query") or reply.get("transcript") or "")
        if self._voice_pill is not None and heard:
            self._voice_pill.heard(heard)
        if not reply.get("audio_base64") and text:
            self.toast("Willy", text, kind="error" if reply.get("success") is False else "info")
        ui = reply.get("ui") or {}
        panel = ui.get("panel")
        if panel == "files":  # on the PC itself: Explorer at that folder
            target = ui.get("path") or str(Path.home())
            try:
                os.startfile(target)  # noqa: S606 - the user's own folder
            except OSError:
                pass
        elif panel == "camera":
            try:
                os.startfile("microsoft.windows.camera:")  # noqa: S606
            except OSError:
                pass

    def quit_app(self) -> None:
        if self.closing:
            return
        self.closing = True
        try:
            self.withdraw()  # disappear at once; the rest of the teardown is invisible
        except tk.TclError:
            pass
        try:
            self.node.stop()
        except Exception:
            pass
        self.tray.stop()
        if self.instance is not None:
            self.instance.release()
        for job in self._jobs.values():
            if job:
                try:
                    self.after_cancel(job)
                except tk.TclError:
                    pass
        self.toasts.close_all()
        if self.voice is not None:
            self.voice.stop()
        self._stop_audio()
        self.pool.shutdown(wait=False, cancel_futures=True)
        # CTk unregisters every widget from flat callback lists on destroy (quadratic for a
        # large tree); both removals tolerate missing entries, so clear the lists first.
        try:
            ctk.AppearanceModeTracker.callback_list.clear()
            for window in list(ctk.ScalingTracker.window_widgets_dict):
                ctk.ScalingTracker.window_widgets_dict[window] = []
        except Exception:
            pass
        self.destroy()

    def join_node(self, timeout: float = 2.0) -> None:
        """Waits briefly for the socket to close cleanly (the hub then marks the PC offline at once)."""
        thread = self._node_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout)


def main(start_hidden: bool = False, instance: Optional[Any] = None):
    """Runs the desktop app. start_hidden: begin in the tray (used at Windows sign-in)."""
    app = WillyDesktopApp(start_hidden=start_hidden, instance=instance)
    try:
        app.mainloop()
    except KeyboardInterrupt:
        app.quit_app()
    finally:
        if not app.closing:
            app.closing = True
            try:
                app.node.stop()
            except Exception:
                pass
        app.tray.stop()  # no ghost icon left behind in the tray
        app.join_node(2.0)


if __name__ == "__main__":
    main()
