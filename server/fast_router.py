"""
Willy Fast-Path Intent Router.

Recognises short, unambiguous commands ("lock my pc", "volume 40", "next song",
"what's my battery") and maps them straight to a PC tool or a live-telemetry answer,
skipping both LLM round-trips (typically 2-5 s -> 50-300 ms).

Patterns are anchored to the whole utterance, so anything longer or fuzzier
("open chrome and search for flights") falls through to the LLM brain.
"""

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

_LEADING_FILLER = re.compile(
    r"^(?:(?:hey|hi|hello|ok|okay)\s+)?(?:willy|willie)\b\s*"
    r"|^(?:please|kindly|just|now|go ahead and|can you|could you|would you|will you)\s+"
)
_TRAILING_FILLER = re.compile(
    r"\s+(?:please|for me|now|right now|thanks|thank you|willy|asap)$"
)
_DEVICE = r"(?:pc|computer|laptop|workstation|system|machine|desktop|windows)"

KNOWN_APPS: Dict[str, str] = {
    "chrome": "chrome",
    "google chrome": "chrome",
    "edge": "edge",
    "microsoft edge": "edge",
    "firefox": "firefox",
    "brave": "brave",
    "notepad": "notepad",
    "calculator": "calculator",
    "calc": "calculator",
    "spotify": "spotify",
    "vs code": "code",
    "vscode": "code",
    "visual studio code": "code",
    "file explorer": "explorer",
    "explorer": "explorer",
    "files": "explorer",
    "my files": "explorer",
    "task manager": "taskmgr",
    "settings": "ms-settings:",
    "windows settings": "ms-settings:",
    "control panel": "control",
    "paint": "mspaint",
    "command prompt": "cmd",
    "cmd": "cmd",
    "terminal": "wt",
    "windows terminal": "wt",
    "powershell": "powershell",
    "word": "winword",
    "microsoft word": "winword",
    "excel": "excel",
    "microsoft excel": "excel",
    "powerpoint": "powerpnt",
    "outlook": "outlook",
    "teams": "teams",
    "microsoft teams": "teams",
    "discord": "discord",
    "telegram": "telegram",
    "whatsapp": "whatsapp",
    "steam": "steam",
    "vlc": "vlc",
    "zoom": "zoom",
    "snipping tool": "snippingtool",
    "obs": "obs",
}

# Closing these would take down the Windows shell or a system page: left to the LLM.
_NOT_CLOSABLE = {"explorer", "ms-settings:", "control", "cmd", "wt"}

KNOWN_SITES: Dict[str, str] = {
    "youtube": "https://www.youtube.com",
    "google": "https://www.google.com",
    "gmail": "https://mail.google.com",
    "google mail": "https://mail.google.com",
    "github": "https://github.com",
    "chatgpt": "https://chatgpt.com",
    "chat gpt": "https://chatgpt.com",
    "netflix": "https://www.netflix.com",
    "instagram": "https://www.instagram.com",
    "facebook": "https://www.facebook.com",
    "linkedin": "https://www.linkedin.com",
    "reddit": "https://www.reddit.com",
    "amazon": "https://www.amazon.com",
    "wikipedia": "https://www.wikipedia.org",
    "google maps": "https://maps.google.com",
    "maps": "https://maps.google.com",
    "google drive": "https://drive.google.com",
    "drive": "https://drive.google.com",
    "google calendar": "https://calendar.google.com",
    "calendar": "https://calendar.google.com",
    "whatsapp web": "https://web.whatsapp.com",
    "stack overflow": "https://stackoverflow.com",
    "stackoverflow": "https://stackoverflow.com",
    "twitter": "https://x.com",
    "claude": "https://claude.ai",
}


@dataclass
class FastIntent:
    name: str                               # label for the activity log
    tool: str                               # PC action, or 'telemetry' / 'clock' for local answers
    args: Dict[str, Any] = field(default_factory=dict)
    reply: str = "Done."                    # spoken confirmation on success
    target: str = "pc"                      # 'pc', 'phone' (runs on the phone) or 'hub' (answered by the hub)
    ui: Optional[Dict[str, Any]] = None     # panel the call screens pop open (files / screenshot / camera)


_NUMBER_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40,
    "forty five": 45, "fifty": 50, "sixty": 60, "ninety": 90,
}
_AMOUNT = r"(\d+(?:\.\d+)?|half an|half a|" + "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True)) + r")"
_UNIT = r"(seconds?|secs?|minutes?|mins?|hours?|hrs?)"
_PHONE = r"(?:phone|mobile|cell ?phone|android)"
_ANY_DEVICE = rf"(?:{_DEVICE}|{_PHONE})"


def _minutes(amount: str, unit: str) -> Optional[float]:
    """'30', 'minutes' -> 30.0; 'half an', 'hour' -> 30.0; 'two', 'hours' -> 120.0."""
    amount = amount.strip()
    if amount.startswith("half"):
        value = 0.5
    elif amount in _NUMBER_WORDS:
        value = float(_NUMBER_WORDS[amount])
    else:
        try:
            value = float(amount)
        except ValueError:
            return None
    if unit.startswith(("hour", "hr")):
        value *= 60
    elif unit.startswith(("sec",)):
        value /= 60
    return value if 0 < value <= 7 * 24 * 60 else None


def _device_kind(word: str) -> str:
    return "mobile" if re.search(_PHONE, word) else "pc"


def _unambiguous_clock(clock: str) -> bool:
    """'5pm', '7:45 a.m.' or 24-hour '17:30' are clear; a bare '5' or '5:30' (AM or PM?) is
    left to the LLM, which can ask or use context."""
    if re.search(r"[ap]\.?m", clock):
        return True
    m = re.fullmatch(r"(\d{1,2})[:.](\d{2})", clock.strip())
    return bool(m) and (int(m.group(1)) >= 13 or int(m.group(1)) == 0)


def _match_hub_intent(s: str) -> Optional[FastIntent]:
    """Requests the hub answers itself: reminders, timers, presence, watchdog, find my phone."""
    if re.fullmatch(r"(?:how(?:'s| is) (?:my |the )?(?:willy )?server(?: doing)?|(?:my |the )?(?:willy )?server status"
                    r"|(?:check|show)(?: me)? (?:my |the )?(?:willy )?server(?: status| health)?"
                    r"|is (?:my |the )?(?:willy )?server (?:ok(?:ay)?|up|down|running|fine|healthy)"
                    r"|(?:are )?(?:my |all )?(?:sites|websites) (?:up|ok(?:ay)?|working))", s):
        return FastIntent("server_status", "server_status", {}, target="hub")
    # --- reminders: "remind me to stretch in 30 minutes", "in 10 min remind me to call mom" -----
    m = (re.fullmatch(rf"(?:set a reminder to |remind me to |remind me )(.+?) in {_AMOUNT} {_UNIT}", s)
         or re.fullmatch(rf"in {_AMOUNT} {_UNIT},? remind me (?:to )?(.+)", s))
    if m:
        groups = m.groups()
        text, amount, unit = (groups[0], groups[1], groups[2]) if not s.startswith("in ") else (groups[2], groups[0], groups[1])
        minutes = _minutes(amount, unit)
        text = text.strip()
        if minutes and text:
            return FastIntent("reminder", "schedule_reminder", {"text": text[:1].upper() + text[1:], "in_minutes": minutes},
                              target="hub")
    clock_re = r"(\d{1,2}(?:[:.]\d{2})?(?: ?[ap]\.?m\.?)?)"
    m = (re.fullmatch(rf"(?:set a reminder to |remind me to |remind me )(.+?) at {clock_re}", s)
         or re.fullmatch(rf"(?:at {clock_re},? remind me (?:to )?|remind me at {clock_re} to )(.+)", s))
    if m:
        if s.startswith(("at ", "remind me at ")):
            clock, text = (m.group(1) or m.group(2)), m.group(3)
        else:
            text, clock = m.group(1), m.group(2)
        text = (text or "").strip()
        if text and clock and _unambiguous_clock(clock):
            return FastIntent("reminder", "schedule_reminder", {"text": text[:1].upper() + text[1:], "time": clock},
                              target="hub")

    # --- timers: "set a timer for 10 minutes", "5 minute timer" ----------------------------------
    m = (re.fullmatch(rf"(?:set |start )?(?:a |the )?timer (?:for )?{_AMOUNT} {_UNIT}", s)
         or re.fullmatch(rf"(?:set |start )?(?:a |an )?{_AMOUNT}[ -]{_UNIT.replace('s?', '')}s? timer", s))
    if m:
        minutes = _minutes(m.group(1), m.group(2))
        if minutes:
            return FastIntent("timer", "set_timer", {"minutes": minutes}, target="hub")

    # --- presence: "is my pc online", "why is my laptop offline", "when was my phone last online" -
    m = re.fullmatch(rf"is (?:my |the )?({_ANY_DEVICE}) (?:really )?(?:online|on|connected|awake|up|offline|off)(?: now| right now)?", s)
    if m:
        return FastIntent("presence", "presence", {"device": _device_kind(m.group(1))}, target="hub")
    m = re.fullmatch(
        rf"why (?:is|was|did) (?:my |the )?({_ANY_DEVICE}) (?:go |went )?(?:offline|disconnected|not online|not connected|down|off)"
        rf"|when (?:was|did) (?:my |the )?({_ANY_DEVICE}) (?:last (?:online|seen|connected)|go offline|disconnect)", s)
    if m:
        return FastIntent("presence", "presence", {"device": _device_kind(m.group(1) or m.group(2))}, target="hub")

    # --- watchdog: "tell me when my pc is online", "turn on the watchdog" ----------------------
    m = re.fullmatch(
        rf"(?:tell|notify|alert|ping|message|let) me(?: know)? (?:when|once|if|as soon as) (?:my |the )?({_ANY_DEVICE})"
        r" (?:is|comes|gets|goes|turns|is back|comes back|gets back)(?: back)? (online|on|up|offline|off|down)", s)
    if m:
        event = "online" if m.group(2) in ("online", "on", "up") else "offline"
        device = "phone" if _device_kind(m.group(1)) == "mobile" else "pc"
        return FastIntent("watch", "watch", {"device": device, "event": event, "repeat": False}, target="hub")
    m = re.fullmatch(r"(?:(turn on|enable|start|switch on|activate|i need|i want|set up)(?: a| the| my)?(?: pc| laptop)? ?watch ?dog"
                     r"|(?:pc |the )?watch ?dog (on|off)"
                     r"|(turn off|disable|stop|switch off|cancel|deactivate)(?: the| my)?(?: pc| laptop)? ?watch ?dog)", s)
    if m:
        off = bool(m.group(3)) or m.group(2) == "off"
        if off:
            return FastIntent("watch_off", "unwatch", {"device": "pc"}, target="hub")
        return FastIntent("watch", "watch", {"device": "pc", "event": "both", "repeat": True}, target="hub")

    # --- find my phone -------------------------------------------------------------------------
    if re.fullmatch(rf"(?:ring|find|locate) (?:my |the )?{_PHONE}|where(?:'s| is) (?:my |the )?{_PHONE}"
                    rf"|make (?:my |the )?{_PHONE} ring", s):
        return FastIntent("ring_phone", "ring_device", {"message": "Find My Phone", "duration_sec": 20},
                          "Your phone is ringing.", target="phone")
    if re.fullmatch(rf"stop (?:the )?ring(?:ing)?(?: (?:my |the )?{_PHONE})?|stop (?:my |the )?{_PHONE}(?: from)? ringing", s):
        return FastIntent("stop_ring", "stop_ring", {}, "Stopped the ringing.", target="phone")
    return None


def normalize(text: str) -> str:
    s = (text or "").lower().strip()
    s = s.replace("’", "'")
    s = re.sub(r"[!?,;]+", " ", s)
    s = re.sub(r"\.+(?=\s|$)", " ", s)      # drop sentence dots but keep 'github.com'
    s = re.sub(r"\s+", " ", s).strip()
    for _ in range(3):
        stripped = _LEADING_FILLER.sub("", s).strip()
        stripped = _TRAILING_FILLER.sub("", stripped).strip()
        if stripped == s:
            break
        s = stripped
    return s


def _volume_level(raw: str) -> Optional[int]:
    level = int(raw)
    return level if 0 <= level <= 100 else None


def match_fast_intent(text: str) -> Optional[FastIntent]:
    s = normalize(text)
    if not s or len(s) > 160:
        return None
    hub = _match_hub_intent(s)
    if hub is not None:
        return hub
    if len(s) > 60:
        return None

    # --- power / session -------------------------------------------------
    if re.fullmatch(rf"lock(?: (?:the|my))?(?: {_DEVICE}| screen)?", s):
        return FastIntent("lock", "power_action", {"action": "lock"}, "Locked your PC.")
    if re.fullmatch(rf"(?:put )?(?:the |my )?{_DEVICE} (?:to )?sleep|sleep (?:the |my )?{_DEVICE}", s):
        return FastIntent("sleep", "power_action", {"action": "sleep"}, "Putting your PC to sleep.")

    # --- volume -------------------------------------------------------------
    m = re.fullmatch(
        r"(?:set |change |turn |make |put )?(?:the )?(?:volume|sound)(?: level)?(?: to| at)? (\d{1,3})(?: ?%| percent)?"
        r"|(\d{1,3})(?: ?%| percent)? volume",
        s,
    )
    if m:
        level = _volume_level(m.group(1) or m.group(2))
        if level is not None:
            return FastIntent("volume_set", "volume_control", {"action": "set", "level": level},
                              f"Volume set to {level} percent.")
    if re.fullmatch(r"mute(?: (?:the|my))?(?: (?:volume|sound|audio|speakers|pc|computer|laptop))?", s):
        return FastIntent("mute", "volume_control", {"action": "mute"}, "Muted.")
    if re.fullmatch(r"unmute(?: (?:the|my))?(?: (?:volume|sound|audio|speakers|pc|computer|laptop))?", s):
        return FastIntent("unmute", "volume_control", {"action": "unmute"}, "Unmuted.")
    if re.fullmatch(
        r"(?:turn |crank )?(?:the )?(?:volume|sound) up|turn (?:it )?up(?: the (?:volume|sound))?"
        r"|(?:increase|raise) (?:the )?(?:volume|sound)|louder", s):
        return FastIntent("volume_up", "volume_control", {"action": "up"}, "Turned the volume up.")
    if re.fullmatch(
        r"(?:turn )?(?:the )?(?:volume|sound) down|turn (?:it )?down(?: the (?:volume|sound))?"
        r"|(?:decrease|lower|reduce) (?:the )?(?:volume|sound)|quieter|softer", s):
        return FastIntent("volume_down", "volume_control", {"action": "down"}, "Turned the volume down.")

    # --- screen brightness ----------------------------------------------------
    m = re.fullmatch(
        r"(?:set |change |turn |make |put )?(?:the )?(?:screen )?brightness(?: level)?(?: to| at)? (\d{1,3})(?: ?%| percent)?"
        r"|(\d{1,3})(?: ?%| percent)? brightness",
        s,
    )
    if m:
        level = _volume_level(m.group(1) or m.group(2))
        if level is not None:
            return FastIntent("brightness_set", "set_brightness", {"level": level},
                              f"Brightness set to {level} percent.")
    if re.fullmatch(r"(?:turn |make )?(?:the )?(?:screen )?brightness up|(?:increase|raise) (?:the )?(?:screen )?brightness"
                    r"|(?:make (?:the |my )?screen )?brighter|full brightness|max(?:imum)? brightness", s):
        full = "full" in s or "max" in s
        return FastIntent("brightness_up", "set_brightness", {"level": 100} if full else {"change": 20},
                          "Brightness set to 100 percent." if full else "Turned the brightness up.")
    if re.fullmatch(r"(?:turn )?(?:the )?(?:screen )?brightness down|(?:decrease|lower|reduce|dim) (?:the |my )?(?:screen brightness|brightness|screen)"
                    r"|(?:make (?:the |my )?screen )?dimmer", s):
        return FastIntent("brightness_down", "set_brightness", {"change": -20}, "Turned the brightness down.")

    # --- media ----------------------------------------------------------------
    if re.fullmatch(r"(?:play|pause|resume|unpause)(?: (?:the )?(?:music|song|video|media|track|playback))?", s):
        return FastIntent("play_pause", "media_control", {"action": "play_pause"}, "Done.")
    if re.fullmatch(r"(?:next|skip)(?: (?:song|track|video))?|skip (?:this|the) (?:song|track|video)|play (?:the )?next (?:song|track)", s):
        return FastIntent("next_track", "media_control", {"action": "next"}, "Skipped to the next track.")
    if re.fullmatch(r"(?:previous|prev|last)(?: (?:song|track|video))?|play (?:the )?previous (?:song|track)", s):
        return FastIntent("prev_track", "media_control", {"action": "prev"}, "Playing the previous track.")

    # --- screen / windows -----------------------------------------------------
    if re.fullmatch(
        r"(?:take|grab|capture)(?: a| the)? (?:screenshot|screen shot|screen capture|screengrab)"
        r"(?: of (?:the|my) (?:screen|desktop))?|screenshot", s):
        return FastIntent("screenshot", "take_screenshot", {}, "Screenshot saved; here it is.",
                          ui={"panel": "screenshot"})
    if re.fullmatch(
        r"show (?:the |my )?desktop|go to (?:the )?desktop|minimi[sz]e (?:all|everything)(?: (?:the )?windows)?"
        r"|hide (?:all )?(?:the )?windows", s):
        return FastIntent("show_desktop", "window_action", {"action": "minimize_all"}, "Showing your desktop.")

    # --- open apps / websites -------------------------------------------------
    m = re.fullmatch(r"(?:open|launch|start|run|fire up|bring up)(?: up)? (?:the |my )?(.+?)(?: app| application| website| site)?", s)
    if m:
        target = m.group(1).strip()
        if target in KNOWN_SITES:
            url = KNOWN_SITES[target]
            return FastIntent("open_site", "open_url", {"url": url}, f"Opening {target.title()}.")
        if target in KNOWN_APPS:
            return FastIntent("open_app", "launch_application", {"target": KNOWN_APPS[target]},
                              f"Opening {target.title() if len(target) > 3 else target.upper()}.")
        if re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+(?:/\S*)?", target):
            return FastIntent("open_site", "open_url", {"url": f"https://{target}"}, f"Opening {target}.")

    # --- panels the call screens open (files, the PC screen, the camera) -----------
    m = re.fullmatch(r"(?:open|show|browse|go to)(?: me)?(?: the| my)? ([a-z]) (?:drive|disk)", s)
    if m:
        drive = m.group(1).upper()
        return FastIntent("panel_files", "show_panel", {"panel": "files", "path": f"{drive}:\\"},
                          f"Here's your {drive} drive.", target="hub")
    if re.fullmatch(r"(?:show|open|browse)(?: me)?(?: all)?(?: my| the)? (?:files|file browser|file manager|drives|folders)"
                    r"(?: on (?:my |the )?(?:pc|computer|laptop))?", s):
        return FastIntent("panel_files", "show_panel", {"panel": "files", "path": ""},
                          "Here are your PC's files.", target="hub")
    if re.fullmatch(r"(?:show|let me see)(?: me)?(?: my| the)? (?:pc |computer |laptop )?screen|what(?:'s| is) on my "
                    r"(?:pc |computer |laptop )?screen", s):
        return FastIntent("panel_screen", "show_panel", {"panel": "screenshot"}, "Here's your PC screen.", target="hub")
    if re.fullmatch(r"(?:open|start|show)(?: the| my)? camera|take a (?:photo|picture|pic|selfie)", s):
        return FastIntent("panel_camera", "show_panel", {"panel": "camera"},
                          "Camera's open. Tap the shutter when you're ready.", target="hub")

    # --- close apps -------------------------------------------------------------
    m = re.fullmatch(r"(?:close|quit|exit|kill|shut down|shut) (?:the |my |all )?(.+?)(?: app| application| browser| windows?)?", s)
    if m:
        target = m.group(1).strip()
        # Never the Windows shell (explorer) or system pages: those go through the LLM.
        if target in KNOWN_APPS and KNOWN_APPS[target] not in _NOT_CLOSABLE:
            name = target.title() if len(target) > 3 else target.upper()
            return FastIntent("close_app", "close_application", {"target": KNOWN_APPS[target]}, f"Closed {name}.")

    # --- live answers (no PC round-trip) -----------------------------------------
    device = "mobile" if re.search(r"\b(?:phone|mobile)\b", s) else "pc"
    dev_words = r"(?:(?:pc|laptop|computer|phone|mobile)(?:'s)? )?"
    if re.fullmatch(
        rf"(?:what(?:'s| is) |how much |check |show |tell me )?(?:the |my )?{dev_words}battery"
        r"(?: level| percentage| percent| status| life| charge)?(?: (?:is )?(?:left|remaining))?"
        r"(?: (?:do i have|is left))?(?: (?:on|of|in) (?:the |my )?(?:pc|laptop|computer|phone|mobile))?", s):
        return FastIntent("battery", "telemetry", {"metric": "battery", "device": device})
    if re.fullmatch(
        rf"(?:what(?:'s| is) |check |show )?(?:the |my )?{dev_words}(?:cpu|processor)(?: usage| load| use)?"
        r"(?: (?:on|of) (?:the |my )?(?:pc|laptop|computer))?", s):
        return FastIntent("cpu", "telemetry", {"metric": "cpu", "device": "pc"})
    if re.fullmatch(
        rf"(?:what(?:'s| is) |check |show )?(?:the |my )?{dev_words}(?:ram|memory)(?: usage| load| use)?"
        r"(?: (?:on|of) (?:the |my )?(?:pc|laptop|computer))?", s):
        return FastIntent("ram", "telemetry", {"metric": "ram", "device": "pc"})
    if re.fullmatch(
        rf"(?:what(?:'s| is) |check |show |give me )?(?:the |my )?(?:{_DEVICE}|system|device)(?:'s)? (?:status|stats|health)"
        rf"|how(?:'s| is) my {_DEVICE}(?: doing)?", s):
        return FastIntent("status", "telemetry", {"metric": "status", "device": "pc"})

    if re.fullmatch(r"what(?:'s| is) the time|what time is it(?: now)?|time", s):
        return FastIntent("time", "clock", {"what": "time"})
    if re.fullmatch(r"what(?:'s| is) (?:the |today's )?date(?: today)?|what day is (?:it|today)", s):
        return FastIntent("date", "clock", {"what": "date"})

    return None


def telemetry_reply(metric: str, device: Optional[Dict[str, Any]], last_known: bool = False) -> Optional[str]:
    """Builds a spoken answer from a device's live telemetry, or None if data is missing.
    last_known: the device is offline, so report its last reading in the past tense."""
    if not device:
        return None
    t = device.get("telemetry") or {}
    name = device.get("name") or "your device"
    is_ = "was" if last_known else "is"
    when = " when it was last online" if last_known else ""

    if metric == "battery":
        pct = t.get("battery_pct")
        if pct is None:
            return f"{name} {is_} on AC power and doesn't report a battery."
        state = "charging" if t.get("is_charging") else "on battery"
        extra = ""
        secs = t.get("battery_secs_left")
        if not last_known and not t.get("is_charging") and isinstance(secs, (int, float)) and 0 < secs < 48 * 3600:
            hours, mins = int(secs // 3600), int(secs % 3600 // 60)
            extra = f", about {hours} hour{'s' if hours != 1 else ''} {mins} minutes left" if hours else f", about {mins} minutes left"
        return f"{name} {is_} at {round(pct)} percent, {state}{extra}{when}."
    if metric == "cpu":
        cpu = t.get("cpu_pct")
        if cpu is None:
            return None
        top = t.get("top_processes") or []
        busiest = ""
        if top and isinstance(top[0], dict) and top[0].get("name"):
            busiest = f" The busiest app {is_} {top[0].get('name')}."
        return f"CPU usage on {name} {is_} {round(cpu)} percent{when}.{busiest}"
    if metric == "ram":
        ram = t.get("ram_pct")
        if ram is None:
            return None
        used, total = t.get("ram_used_gb"), t.get("ram_total_gb")
        detail = f", {used} of {total} gigabytes" if used is not None and total else ""
        return f"Memory usage on {name} {is_} {round(ram)} percent{detail}{when}."
    if metric == "status":
        if last_known:
            return None  # the presence sentence covers it
        parts = []
        if t.get("battery_pct") is not None:
            parts.append(f"battery {round(t['battery_pct'])} percent{' and charging' if t.get('is_charging') else ''}")
        if t.get("cpu_pct") is not None:
            parts.append(f"CPU {round(t['cpu_pct'])} percent")
        if t.get("ram_pct") is not None:
            parts.append(f"memory {round(t['ram_pct'])} percent")
        if t.get("disk_free_gb") is not None:
            parts.append(f"{t['disk_free_gb']} gigabytes free on disk")
        if not parts:
            return None
        return f"{name} is online: " + ", ".join(parts) + "."
    return None
