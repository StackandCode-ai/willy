"""
PC Tools Function Calling Schemas for Server LLM.
Defines all physical Windows PC capabilities exposed to the Server Brain.
"""

import re
from typing import List, Dict, Any, Iterable

PC_TOOLS_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "launch_application",
            "description": "Launch or open a Windows desktop application or game on the user's PC (e.g. chrome, vs code, notepad, calculator, spotify, discord, steam).",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Name of the application or shortcut to launch (e.g. 'chrome', 'code', 'notepad', 'calc', 'spotify').",
                    },
                    "args": {
                        "type": "string",
                        "description": "Optional command line arguments to pass to the application.",
                    },
                },
                "required": ["target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "close_application",
            "description": "Close or terminate a running application on the user's PC.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Name of the application to close (e.g. 'chrome', 'notepad', 'spotify').",
                    },
                },
                "required": ["target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_powershell",
            "description": "Execute a Windows PowerShell command on the user's PC. Installing/uninstalling software, deleting files, changing system settings or the registry needs the user's explicit yes first.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "PowerShell command or script string to run.",
                    },
                    "confirmed": {
                        "type": "boolean",
                        "description": "True only if the user explicitly approved this exact command in their latest message.",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_url",
            "description": "Open a website URL in the user's default browser on their PC (e.g. YouTube, GitHub, Google).",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The complete URL to open (e.g. 'https://youtube.com', 'https://github.com').",
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "power_action",
            "description": "Control Windows system power and session state.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["lock", "sleep", "shutdown", "restart", "cancel_shutdown"],
                        "description": "Power action to execute.",
                    },
                    "delay_sec": {
                        "type": "integer",
                        "description": "Delay in seconds for shutdown or restart (default 30).",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "volume_control",
            "description": "Adjust or mute system audio volume on the user's PC.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["mute", "unmute", "up", "down", "set"],
                        "description": "Volume action to perform.",
                    },
                    "level": {
                        "type": "integer",
                        "description": "Desired volume level percentage (0 to 100) when action is 'set'.",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "window_action",
            "description": "Manage desktop windows on the user's PC (minimize, maximize, focus, show desktop).",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["minimize_all", "minimize", "maximize", "focus", "close", "move_to_monitor"],
                        "description": "Window management action.",
                    },
                    "target": {
                        "type": "string",
                        "description": "Application or window title to focus/minimize/move (optional if action is minimize_all).",
                    },
                    "monitor": {"type": "integer", "description": "move_to_monitor: screen number (1 = main)."},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "take_screenshot",
            "description": "Capture a screenshot of the user's PC desktop screen and save it locally.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_pc_status",
            "description": "Retrieve live hardware and battery status from the user's PC (battery %, charging state, CPU load, RAM usage, active window). When the PC is offline it returns the last-known state and why it is offline.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "schedule_meeting",
            "description": "Schedule a meeting, calendar event, or video call (Google Meet, Zoom, Teams, Calendar).",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Meeting subject or title (e.g. 'Project Sync with Hari', 'Sprint Review').",
                    },
                    "time": {
                        "type": "string",
                        "description": "Time or date description (e.g. 'tomorrow at 3 PM', '10:00 AM').",
                    },
                    "duration_mins": {
                        "type": "integer",
                        "description": "Duration in minutes (default 30).",
                    },
                    "platform": {
                        "type": "string",
                        "enum": ["google_calendar", "teams", "zoom", "meet"],
                        "description": "Meeting or calendar platform to open.",
                    },
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_message",
            "description": "Send an email, or a WhatsApp message through WhatsApp on the PC. When the phone is online prefer send_whatsapp / send_sms (they use the phone's contacts).",
            "parameters": {
                "type": "object",
                "properties": {
                    "recipient": {
                        "type": "string",
                        "description": "Contact name, phone number, or email address.",
                    },
                    "message": {
                        "type": "string",
                        "description": "Text message content to send.",
                    },
                    "channel": {
                        "type": "string",
                        "enum": ["whatsapp", "email"],
                        "description": "Communication channel (default whatsapp).",
                    },
                },
                "required": ["message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "schedule_reminder",
            "description": "Schedule a reminder. It pops up on the user's phone and PC at that time (works even when the PC is off). Use in_minutes for relative times ('in 30 minutes').",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "The reminder task description or note (e.g. 'Review API documentation', 'Call mom').",
                    },
                    "time": {
                        "type": "string",
                        "description": "Clock time for the reminder (e.g. '11:00 AM', '2:30 PM', '17:00').",
                    },
                    "date": {
                        "type": "string",
                        "description": "Optional date string (e.g. '2026-09-25' or 'today' or 'tomorrow').",
                    },
                    "in_minutes": {
                        "type": "number",
                        "description": "Instead of time/date: remind after this many minutes from now.",
                    },
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_alarm",
            "description": "Set a Willy alarm that repeats every day at that time (Willy alerts the phone and PC). To set an alarm in the phone's own Clock app use set_phone_alarm.",
            "parameters": {
                "type": "object",
                "properties": {
                    "time": {
                        "type": "string",
                        "description": "Alarm time (e.g. '07:00 AM', '14:00', '08:30').",
                    },
                    "label": {
                        "type": "string",
                        "description": "Alarm label or reason (e.g. 'Morning Wake Up', 'Meeting preparation').",
                    },
                },
                "required": ["time"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_morning_briefing",
            "description": "Retrieve the current morning briefing containing ChatGPT thread tasks, mobile notification counts (emails, WhatsApp, missed calls), and PC status.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "media_control",
            "description": "Control media playback on the user's PC (Spotify, YouTube, any player): play/pause, next or previous track, stop.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["play_pause", "next", "prev", "stop"],
                        "description": "Media key to press.",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_brightness",
            "description": "Set the laptop screen brightness on the user's PC. Use level for an exact value, or change for brighter (+) / dimmer (-).",
            "parameters": {
                "type": "object",
                "properties": {
                    "level": {
                        "type": "integer",
                        "description": "Brightness percentage from 0 to 100.",
                    },
                    "change": {
                        "type": "integer",
                        "description": "Relative change in percent, e.g. 20 for 'brighter', -20 for 'dimmer'. Use instead of level.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_processes",
            "description": "List the programs using the most CPU or memory on the user's PC.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sort_by": {
                        "type": "string",
                        "enum": ["cpu", "memory"],
                        "description": "Sort order (default cpu).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "How many processes to return (default 10).",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "notify",
            "description": "Show a pop-up notification message on the user's PC screen.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Short heading."},
                    "message": {"type": "string", "description": "Text to display."},
                },
                "required": ["message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_folder",
            "description": "Open a folder in File Explorer on the user's PC (e.g. downloads, documents, desktop, pictures, or a full path).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Folder name like 'downloads' or an absolute path.",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_clipboard",
            "description": "Copy text to the clipboard on the user's PC.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to place on the clipboard."},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_clipboard",
            "description": "Read the current text on the user's PC clipboard.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ring_phone",
            "description": "Make the user's phone ring loudly so they can find it.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_to_phone",
            "description": "Send a link or short note from the PC to the user's phone, where it opens or pops up instantly.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Link to open on the phone."},
                    "text": {"type": "string", "description": "Note text to show on the phone."},
                },
            },
        },
    },
]


def _fn(name: str, description: str, properties: Dict[str, Any], required: List[str] = ()) -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": list(required)},
        },
    }


_DEVICE = {"type": "string", "enum": ["pc", "phone"], "description": "Which device."}

PC_TOOLS_DEFINITIONS += [
    # --- answered by the hub (work while devices are offline) ---------------------------
    _fn("get_device_status",
        "Live or last-known status of the PC or phone: online/offline (since when and why), battery, "
        "CPU/RAM (PC), notifications (phone). Works even when the device is offline.",
        {"device": _DEVICE}, ["device"]),
    _fn("watch_device",
        "Watchdog: alert the user's phone when the PC or phone comes online and/or goes offline. "
        "Optionally run a follow-up request once it is back online.",
        {"device": _DEVICE,
         "event": {"type": "string", "enum": ["online", "offline", "both"], "description": "Default online."},
         "repeat": {"type": "boolean", "description": "Keep watching (watchdog) instead of alerting once."},
         "then": {"type": "string", "description": "Request to run when it comes online, e.g. 'what is using my CPU'."}},
        ["device"]),
    _fn("list_watches", "List the active watchdog alerts.", {}),
    _fn("cancel_watch", "Cancel watchdog alerts: one by id, or all of a device's.",
        {"watch_id": {"type": "string"},
         "device": {"type": "string", "enum": ["pc", "phone", "all"]}}),
    _fn("list_reminders", "List the user's upcoming reminders, timers and alarms.", {}),
    _fn("cancel_reminder", "Cancel a reminder by id, or by words from its text.",
        {"reminder_id": {"type": "string"}, "text": {"type": "string"}}),
    _fn("set_timer",
        "Start a countdown timer. Rings in the phone's Clock app when the phone is online, "
        "otherwise Willy alerts the user when it ends.",
        {"minutes": {"type": "number", "description": "Length in minutes (0.5 = 30 seconds)."},
         "label": {"type": "string"}},
        ["minutes"]),
    _fn("send_to_pc",
        "Show a note or text on the user's PC screen (also copied to the PC clipboard; long text opens in Notepad).",
        {"text": {"type": "string"}, "title": {"type": "string"}}, ["text"]),
    _fn("share_conversation",
        "Send the recent conversation between the user and Willy to their PC or phone as a note.",
        {"device": _DEVICE, "messages": {"type": "integer", "description": "How many recent exchanges (default 6)."}},
        ["device"]),
    # --- PC: work inside apps -------------------------------------------------------------
    _fn("type_text",
        "Draft text into an app on the PC (focuses or opens it first), e.g. a message in WhatsApp Desktop, "
        "a question in Claude, text in Notepad or Word. It pastes the text but never presses Enter/Send: "
        "the user reviews and sends it.",
        {"text": {"type": "string"}, "app": {"type": "string", "description": "App or window title, e.g. 'whatsapp', 'claude'. Empty = the window in front."}},
        ["text"]),
    _fn("read_window_text",
        "Read the visible text of an app window on the PC (chat messages, a reply in Claude, a document, a dialog).",
        {"app": {"type": "string", "description": "Part of the window title; empty = the window in front."}}),
    # --- run on the Android phone ---------------------------------------------------------
    _fn("phone_call", "Call a contact (by name) or a phone number from the user's phone.",
        {"to": {"type": "string", "description": "Contact name or number."}}, ["to"]),
    _fn("send_sms", "Send a text message (SMS) from the user's phone.",
        {"to": {"type": "string", "description": "Contact name or number."}, "message": {"type": "string"}},
        ["to", "message"]),
    _fn("send_whatsapp",
        "Open WhatsApp on the user's phone with a message ready for a contact or number (the user taps send).",
        {"to": {"type": "string", "description": "Contact name or number."}, "message": {"type": "string"}},
        ["message"]),
    _fn("open_phone_app", "Open an app on the user's phone (e.g. Spotify, Camera, YouTube, WhatsApp).",
        {"name": {"type": "string"}}, ["name"]),
    _fn("set_phone_alarm", "Set an alarm in the phone's Clock app.",
        {"time": {"type": "string", "description": "e.g. '6:30 AM', '18:00'."}, "label": {"type": "string"}},
        ["time"]),
    _fn("phone_volume", "Set or change the phone's media volume.",
        {"level": {"type": "integer", "description": "0-100."},
         "action": {"type": "string", "enum": ["mute", "unmute", "up", "down"]}}),
    _fn("phone_media", "Control music or video playing on the phone.",
        {"action": {"type": "string", "enum": ["play_pause", "next", "prev", "stop"]}}, ["action"]),
    _fn("navigate", "Start navigation (or show a place) in Google Maps on the user's phone.",
        {"destination": {"type": "string"},
         "mode": {"type": "string", "enum": ["driving", "walking", "transit", "bicycling"]}},
        ["destination"]),
    _fn("read_phone_notifications",
        "Read the latest notifications on the user's phone (WhatsApp messages, SMS, email, calls...).",
        {"limit": {"type": "integer", "description": "Default 10."}}),
    _fn("phone_flashlight", "Turn the phone's flashlight on or off.", {"on": {"type": "boolean"}}, ["on"]),
    _fn("find_contact",
        "Look up contacts in the phone's address book by name or by (part of a) phone number.",
        {"query": {"type": "string", "description": "A name, or digits of a number."},
         "limit": {"type": "integer", "description": "Default 5."}}, ["query"]),
    _fn("open_on_phone", "Open a web page (URL) in the phone's browser.",
        {"url": {"type": "string"}}, ["url"]),
    _fn("install_phone_app",
        "Open an app's Play Store page on the phone so the user can tap Install.",
        {"name": {"type": "string", "description": "App name, e.g. 'Zomato'."},
         "package": {"type": "string", "description": "Android package id if known."}}, ["name"]),
    _fn("uninstall_phone_app",
        "Show the phone's uninstall confirmation for an installed app (the user confirms on the phone).",
        {"name": {"type": "string"}}, ["name"]),
    _fn("phone_camera", "Open the phone's camera ready to take a photo, video or selfie (the user takes it).",
        {"mode": {"type": "string", "enum": ["photo", "video", "selfie"]}}),
    _fn("open_file",
        "Open or play a file on the PC, optionally in a given app and on a given screen. Give the path, or just "
        "(part of) its name plus the folder hint the user said - it is searched for (fuzzy, e.g. 'sarvamaya'). "
        "If the result is AMBIGUOUS, ask which of the listed files.",
        {"path": {"type": "string"}, "name": {"type": "string", "description": "File name or part of it."},
         "folder": {"type": "string", "description": "Where the user said it is, e.g. 'Downloads/Telegram Desktop'."},
         "app": {"type": "string", "description": "e.g. 'vlc', 'word', 'notepad++'; default app if omitted."},
         "monitor": {"type": "integer", "description": "Screen number to show it on (1 = main, 2 = second)."},
         "fullscreen": {"type": "boolean"},
         "kind": {"type": "string", "enum": ["video", "audio", "image", "document"]}}),
    _fn("find_files", "Search the PC (or the server) for files by (part of) their name, optionally in a folder or of a kind.",
        {"name": {"type": "string"}, "folder": {"type": "string"},
         "kind": {"type": "string", "enum": ["video", "audio", "image", "document"]}, "device": {"type": "string", "enum": ["pc", "server"], "description": "Which machine (default pc)."}}, ["name"]),
    _fn("save_feedback",
        "Save a note about something Willy couldn't do or did wrong, or a feature the user wants, for the developer "
        "to improve later. Use when the user says to note/remember/mark something to improve.",
        {"note": {"type": "string", "description": "What the user wanted and what went wrong, in one or two sentences."}},
        ["note"]),
    _fn("get_server_status",
        "Health of the user's Willy server (the cloud machine running the hub): CPU, memory, disk, pm2 apps, "
        "services (nginx, mariadb, docker), Docker containers, every website and its HTTPS certificate.",
        {"refresh": {"type": "boolean", "description": "Re-check the websites now (slower)."}}),
    _fn("server_shell",
        "Run a bash command on the user's Linux server (as the server agent's user, in the home folder by default). Read-only "
        "commands (ls, cat, df, ps, tail, grep, docker ps, pm2 list, systemctl status, git status...) run directly; "
        "anything that changes the server needs the user's yes first: show the command, then call with confirmed=true.",
        {"command": {"type": "string"}, "cwd": {"type": "string"}, "timeout": {"type": "integer", "description": "Seconds, default 60."},
         "confirmed": {"type": "boolean"}}, ["command"]),
    _fn("git",
        "Git on the PC or the server: status, log, branches, diff, fetch, pull, switch (branch), create_branch, stash, "
        "stash_pop, restore (discard changes in files), commit, push, clone, list (all projects). repo = project name "
        "from GIT PROJECTS. push/commit/restore need the user's yes first (then confirmed=true). If pull or switch "
        "fails because of uncommitted changes, offer stash=true.",
        {"device": {"type": "string", "enum": ["pc", "server"]},
         "action": {"type": "string", "enum": ["list", "status", "log", "branches", "diff", "fetch", "pull", "switch",
                                               "create_branch", "stash", "stash_pop", "restore", "commit", "push", "clone"]},
         "repo": {"type": "string"}, "branch": {"type": "string"},
         "files": {"type": "array", "items": {"type": "string"}},
         "message": {"type": "string", "description": "Commit message."}, "url": {"type": "string", "description": "clone URL"},
         "stash": {"type": "boolean"}, "confirmed": {"type": "boolean"}}, ["action"]),
    _fn("server_admin",
        "Server administration (like a control panel). Views: topic='updates' (OS package updates, newer release, "
        "reboot needed), 'storage' (disks, biggest folders, what can be cleaned), 'network' (IPs, open ports, traffic), "
        "'security' (logins, failed SSH attempts, attackers), 'services' (all services and boot settings), 'timers' "
        "(cron + scheduled jobs), 'journal' (system logs; unit/priority/since/grep), 'history' (CPU/RAM/disk over "
        "the last hours/days). Changes - need the user's yes first, then confirmed=true: topic='apply_updates' "
        "(security_only), 'cleanup' (what: journal|docker|pm2_logs|dnf_cache|trash), 'service_boot' (name, enable), "
        "'reboot', 'cancel_reboot'.",
        {"topic": {"type": "string", "enum": ["updates", "storage", "network", "security", "services", "timers", "journal",
                                              "history", "apply_updates", "cleanup", "service_boot", "reboot", "cancel_reboot"]},
         "what": {"type": "string"}, "name": {"type": "string"}, "enable": {"type": "boolean"},
         "security_only": {"type": "boolean"}, "unit": {"type": "string"}, "priority": {"type": "string"},
         "since": {"type": "string"}, "grep": {"type": "string"}, "hours": {"type": "number"},
         "confirmed": {"type": "boolean"}}, ["topic"]),
    _fn("fix_site",
        "For a website on the user's server (e.g. blog.example.com): action='diagnose' explains why it's down "
        "(nginx target, port, whether its app runs, where its files are). action='start' starts its app again with "
        "pm2 - only after the user said yes (confirmed=true). Use this for any 'why is <site> down' / 'turn it on'.",
        {"domain": {"type": "string"}, "action": {"type": "string", "enum": ["diagnose", "start"]},
         "confirmed": {"type": "boolean"}}, ["domain"]),
    _fn("server_control",
        "Status, logs, restart, stop or start of a pm2 app, systemd service or Docker container on the server. "
        "restart/stop/start need the user's yes first (then confirmed=true).",
        {"kind": {"type": "string", "enum": ["app", "service", "container"]}, "name": {"type": "string"},
         "action": {"type": "string", "enum": ["status", "logs", "restart", "stop", "start"]},
         "lines": {"type": "integer"}, "confirmed": {"type": "boolean"}}, ["kind", "name"]),
    _fn("show_panel",
        "Pop open a panel on the user's call screen (phone app or web call): 'files' = browse the PC's drives and "
        "folders (optionally at a path, e.g. 'D:\\Projects'), 'screenshot' = show the PC screen, 'camera' = the "
        "device's camera for the user to take a photo. Use when they want to see, pick or manage files visually.",
        {"panel": {"type": "string", "enum": ["files", "screenshot", "camera"]},
         "path": {"type": "string", "description": "Folder to open in the files panel."},
         "device": {"type": "string", "enum": ["pc", "server"], "description": "Which machine (default pc)."}}, ["panel"]),
    _fn("get_weather", "Current weather and today's/tomorrow's forecast for a place (live data).",
        {"place": {"type": "string", "description": "City or place; use the user's city if they don't say."}}, ["place"]),
    _fn("place_time", "The current local time in a city or country (live).",
        {"place": {"type": "string"}}, ["place"]),
    _fn("web_lookup", "Look up a fact online (Wikipedia / DuckDuckGo instant answers): people, places, "
        "definitions, history. Not for news or prices.",
        {"query": {"type": "string"}}, ["query"]),
    _fn("manage_file",
        "Files and folders on the PC: read a file (text, Word, PDF), write/create a file (.txt, .md, .docx...), "
        "list a folder, move, copy, rename, zip, unzip, make a folder, or delete (to the Recycle Bin; needs the "
        "user's yes, then confirmed=true). Paths may start with Downloads/Documents/Desktop/Pictures or be a file name.",
        {"op": {"type": "string", "enum": ["read", "write", "list", "move", "copy", "rename", "zip", "unzip",
                                            "mkdir", "delete"]},
         "path": {"type": "string", "description": "The file or folder, e.g. 'Downloads/report.pdf' or 'notes.txt'."},
         "destination": {"type": "string", "description": "Target folder/path (move, copy, zip, unzip) or the new name (rename)."},
         "content": {"type": "string", "description": "Text to write (write)."},
         "overwrite": {"type": "boolean"},
         "confirmed": {"type": "boolean", "description": "Only for delete, after the user said yes."},
         "device": {"type": "string", "enum": ["pc", "server"], "description": "Which machine (default pc)."}}, ["op"]),
    _fn("set_wallpaper", "Set the PC's desktop wallpaper to a picture file.",
        {"path": {"type": "string", "description": "Picture path or name, e.g. 'Pictures/beach.jpg'."}}, ["path"]),
    _fn("app_volume", "Mute/unmute or set the volume of ONE app on the PC (e.g. only the browser or Spotify).",
        {"app": {"type": "string", "description": "e.g. 'browser', 'chrome', 'spotify', 'zoom'."},
         "mute": {"type": "boolean"}, "level": {"type": "integer", "description": "0-100."}}, ["app"]),
    _fn("send_file_to_phone",
        "Send a file from the PC to the phone (saved in the phone's Downloads/Willy). Give a path, or "
        "which='latest_screenshot' / 'latest_download' / 'clipboard' (a file copied in Explorer).",
        {"path": {"type": "string", "description": "Full path, or a file name to look for in Downloads/Desktop/Documents."},
         "which": {"type": "string", "enum": ["latest_screenshot", "latest_download", "clipboard"]}}),
]

# Tools the hub answers itself (they work while the PC and phone are offline).
SERVER_SIDE_TOOLS = {
    "schedule_reminder", "set_alarm", "get_morning_briefing", "get_device_status", "watch_device",
    "list_watches", "cancel_watch", "list_reminders", "cancel_reminder", "set_timer", "share_conversation",
    "get_weather", "place_time", "web_lookup", "show_panel", "save_feedback",
    "get_server_status", "fix_site", "git", "server_admin",
}
# Tools that run on the phone.
PHONE_TOOLS = {
    "ring_phone", "send_to_phone", "phone_call", "send_sms", "send_whatsapp", "open_phone_app",
    "set_phone_alarm", "phone_volume", "phone_media", "navigate", "read_phone_notifications",
    "phone_flashlight", "find_contact", "open_on_phone", "install_phone_app", "uninstall_phone_app",
    "phone_camera",
}
# Tools that also work on the Willy server ("which processes use the most memory on the server").
for _tool in PC_TOOLS_DEFINITIONS:
    if _tool["function"]["name"] in ("list_processes", "get_pc_status"):
        _tool["function"]["parameters"].setdefault("properties", {})["device"] = {
            "type": "string", "enum": ["pc", "server"], "description": "Which machine (default pc)."}

# With two phones signed in, the user can name one ("call Mom from my A16").
for _tool in PC_TOOLS_DEFINITIONS:
    if _tool["function"]["name"] in PHONE_TOOLS:
        _tool["function"]["parameters"].setdefault("properties", {})["phone"] = {
            "type": "string", "description": "Only when the user names a phone, e.g. 'A16'."}
# Actions whose successful result carries a user-facing `message`: the brain speaks it
# directly instead of spending a second LLM round on a confirmation.
DIRECT_REPLY_TOOLS = {
    "schedule_reminder", "set_alarm", "set_timer", "watch_device", "cancel_watch", "cancel_reminder",
    "send_to_pc", "share_conversation", "ring_phone", "send_to_phone", "phone_call", "send_sms",
    "send_whatsapp", "open_phone_app", "set_phone_alarm", "phone_volume", "phone_media", "navigate",
    "phone_flashlight", "type_text", "open_on_phone", "install_phone_app", "uninstall_phone_app",
    "phone_camera", "send_file_to_phone", "show_panel",
}
# Phone tool -> action name the Android app understands (payloads are mapped in the hub).
PHONE_TOOL_ACTIONS = {
    "ring_phone": "ring_device", "send_to_phone": "quickdrop", "phone_call": "phone_call",
    "send_sms": "phone_sms", "send_whatsapp": "phone_whatsapp", "open_phone_app": "phone_open_app",
    "set_phone_alarm": "phone_alarm", "phone_volume": "phone_volume", "phone_media": "phone_media",
    "navigate": "phone_navigate", "read_phone_notifications": "phone_notifications",
    "phone_flashlight": "flashlight", "find_contact": "phone_contacts", "open_on_phone": "phone_open_url",
    "install_phone_app": "phone_install_app", "uninstall_phone_app": "phone_uninstall_app",
    "phone_camera": "phone_camera",
}


# ------------------------------------------------------------------ tool selection
# Every tool definition costs prompt tokens on every LLM call (~4k for all of them), and the
# free Groq tier allows 8k tokens per minute per model. So each request carries the core
# tools plus only the groups its words (or the last exchange) point at.
CORE_TOOLS = {
    "launch_application", "close_application", "open_url", "volume_control", "media_control", "app_volume",
    "get_weather", "place_time", "web_lookup", "show_panel", "open_file", "find_files", "save_feedback",
    "power_action", "window_action", "get_pc_status", "get_device_status", "execute_powershell",
    "list_processes", "schedule_reminder", "notify", "type_text", "send_to_phone", "send_to_pc",
}
# What stays when a request must be trimmed hard: the everyday basics.
ESSENTIAL_TOOLS = {"launch_application", "close_application", "open_url", "volume_control", "media_control",
                   "power_action", "schedule_reminder", "web_lookup"}

TOOL_GROUPS = [
    ({"git"}, r"\bgit\b|github|repo|branch|pull|push|commit|clone|stash|checkout|merge|restore|revert|version control"),
    ({"get_server_status", "server_shell", "server_control", "fix_site", "server_admin"},
     r"server|pm2|nginx|website|site|domain|n8n|docker|container|certificate|ssl|mariadb|database|uptime|"
     r"true ?willy|stack ?and ?code|pm-tool|hosting|cloud|ec2|aws|crash|logs?\b|\bdown\b|not (?:working|loading|opening)|"
     r"\.com\b|portfolio|athulya|zaffar|hestia|p2t"),
    # (tools, trigger words)
    ({"phone_call", "send_sms", "send_whatsapp", "open_phone_app", "set_phone_alarm", "phone_volume",
      "phone_media", "navigate", "read_phone_notifications", "phone_flashlight", "ring_phone",
      "find_contact", "open_on_phone", "install_phone_app", "uninstall_phone_app", "phone_camera"},
     r"phone|mobile|oppo|android|call|dial|ring|text|sms|messag|msg|whats ?app|tell \w+|notification|"
     r"alarm|navigat|direction|route|take me|how do i get|maps?\b|flash ?light|torch|contact|spotify|"
     r"instagram|youtube music|missed|number|install|uninstall|play store|camera|photo|selfie|video"),
    ({"set_alarm", "set_timer", "list_reminders", "cancel_reminder", "schedule_meeting", "get_morning_briefing"},
     r"remind|alarm|timer|meeting|schedule|briefing|wake|agenda|calendar|minute|hour|tomorrow|tonight|"
     r"morning|evening|o'?clock|\d\s*(?:am|pm)|cancel|delete|upcoming"),
    ({"watch_device", "list_watches", "cancel_watch"},
     r"watch|when .*\b(?:online|offline|back|comes|goes|on|off)|alert|let me know|tell me when|notify me"),
    ({"share_conversation", "set_clipboard", "get_clipboard", "send_message", "send_file_to_phone"},
     r"share|clipboard|copy|paste|conversation|chat|send|messag|note|file|photo|picture|image|pdf|"
     r"document|screenshot|download|transfer"),
    ({"take_screenshot", "read_window_text", "set_brightness", "open_folder", "manage_file", "set_wallpaper"},
     r"screen|screenshot|bright|dim|read|window|what does|what is on|showing|folder|file|explorer|"
     r"download|document|desktop|move|copy|rename|zip|unzip|extract|delete|create|write|save|"
     r"wallpaper|background|\.[a-z0-9]{2,4}\b|notes?"),
]
_TOOL_GROUP_RES = [(tools, re.compile(pattern, re.I)) for tools, pattern in TOOL_GROUPS]


def select_tools(text: str, context: str = "", source: str = "", extra: Iterable[str] = ()) -> List[Dict[str, Any]]:
    """The tool definitions to send for this request. `context` is the previous exchange (so a
    follow-up like "yes" or "make it 5 minutes" keeps the tools it needs), `source` the device
    that asked (phone requests always get the phone tools) and `extra` tool names that must
    stay declared (tools already called in the conversation)."""
    wanted = set(CORE_TOOLS) | set(extra)
    haystack = f"{text}\n{context}"
    for tools, pattern in _TOOL_GROUP_RES:
        if pattern.search(haystack):
            wanted |= tools
    if source == "mobile":
        wanted |= next(tools for tools, _ in TOOL_GROUPS if "phone_call" in tools)
    return [t for t in PC_TOOLS_DEFINITIONS if t["function"]["name"] in wanted]
