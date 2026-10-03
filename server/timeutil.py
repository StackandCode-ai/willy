"""
User-local time helpers for reminders, alarms and briefings.

The server may run in a different timezone from the user (e.g. an EC2 box on UTC),
so user-facing times resolve against USER_TIMEZONE, else the UTC offset most recently
reported by one of the user's devices, else the server's local zone.
"""

import re
import datetime as dt
from typing import Optional, Tuple

from server.config import settings

_reported_offset_min: Optional[int] = None

_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_CLOCK_RE = re.compile(r"(\d{1,2})(?:[:.h]?(\d{2}))?(am|pm|a|p)?")


def set_reported_offset(minutes) -> None:
    """Records the UTC offset (in minutes) reported by a user device."""
    global _reported_offset_min
    try:
        m = int(minutes)
    except (TypeError, ValueError):
        return
    if -14 * 60 <= m <= 14 * 60:
        _reported_offset_min = m


def user_tz() -> dt.tzinfo:
    if settings.USER_TIMEZONE:
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(settings.USER_TIMEZONE)
        except Exception:
            pass
    if _reported_offset_min is not None:
        return dt.timezone(dt.timedelta(minutes=_reported_offset_min))
    return dt.datetime.now().astimezone().tzinfo


def user_now() -> dt.datetime:
    return dt.datetime.now(user_tz())


def parse_clock(text) -> Optional[Tuple[int, int]]:
    """'11:00 AM' / '2:30pm' / '17:00' / '7am' / 'noon' -> (hour, minute)."""
    if text is None:
        return None
    s = re.sub(r"\s+", "", str(text).lower())
    s = re.sub(r"([ap])\.?m\.?$", r"\1m", s).rstrip(".")   # 'p.m.' -> 'pm', keep '7.45'
    if not s:
        return None
    if s in ("noon", "midday"):
        return (12, 0)
    if s == "midnight":
        return (0, 0)
    m = _CLOCK_RE.fullmatch(s)
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2) or 0)
    meridiem = m.group(3)
    if minute > 59:
        return None
    if meridiem:
        if not 1 <= hour <= 12:
            return None
        if meridiem.startswith("p") and hour != 12:
            hour += 12
        elif meridiem.startswith("a") and hour == 12:
            hour = 0
    elif hour > 23:
        return None
    return (hour, minute)


def parse_date(text, today: dt.date) -> dt.date:
    """'today' / 'tomorrow' / '2026-09-25' / 'friday' -> date (defaults to today)."""
    if not text:
        return today
    s = str(text).strip().lower()
    if s in ("today", "tonight"):
        return today
    if s == "tomorrow":
        return today + dt.timedelta(days=1)
    try:
        return dt.date.fromisoformat(s[:10])
    except ValueError:
        pass
    for idx, name in enumerate(_WEEKDAYS):
        if s.startswith(name[:3]):
            delta = (idx - today.weekday()) % 7
            return today + dt.timedelta(days=delta or 7)
    return today


def due_datetime(date_text, time_text, now: Optional[dt.datetime] = None) -> Optional[dt.datetime]:
    """Resolves a reminder's date/time strings into an aware datetime in the user's zone."""
    now = now or user_now()
    clock = parse_clock(time_text)
    if clock is None:
        return None
    day = parse_date(date_text, now.date())
    return dt.datetime(day.year, day.month, day.day, clock[0], clock[1], tzinfo=now.tzinfo)


def format_clock(hour: int, minute: int) -> str:
    """(17, 5) -> '05:05 PM' (the zero-padded style the apps already use)."""
    suffix = "AM" if hour < 12 else "PM"
    h12 = hour % 12 or 12
    return f"{h12:02d}:{minute:02d} {suffix}"
