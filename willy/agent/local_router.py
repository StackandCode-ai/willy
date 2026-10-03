"""
Deterministic Fast Local Intent Router for Willy.
Executes desktop automation, window management, process control, app launching,
typing, keypresses, power, settings, volume, telemetry, and queries
100% LOCALLY with ZERO latency and ZERO internet requirement.
"""

import os
import re
import time
import datetime
import subprocess
import urllib.parse
from typing import Optional, Dict, Any, List

import win32gui
import win32process
import win32api
import ctypes

try:
    import pyautogui
    pyautogui.FAILSAFE = False
except ImportError:
    pyautogui = None

try:
    import psutil
except ImportError:
    psutil = None

from willy.tools.power_tools import (
    lock_workstation,
    unlock_or_wake,
    shutdown_system,
    restart_system,
    sleep_system,
    cancel_shutdown,
)
from willy.tools.process_tools import (
    check_app_running,
    close_app,
)
from willy.tools.app_launcher import launch_windows_app
from willy.tools.system_tools import volume_control, get_system_status, scroll_screen, open_url
from willy.tools.settings_tools import set_windows_theme, open_settings_page
from willy.tools.network_tools import is_internet_available, check_network_status


def _strip_wake_and_fillers(text: str) -> str:
    """
    Strips wake words, STT misrecognitions (like 'eivilly', 'heavily'),
    and conversational filler prefixes so local intents match cleanly.
    """
    cleaned = text.strip()
    # Normalize common STT misrecognitions of 'Hey Willy' / 'Willy'
    cleaned = re.sub(r"^(?:hey\s+willy|willy|willie|eivilly|ey\s+willy|hey\s+willie|baba\s+willy|oh\s+willy|come\s+on\s+willy|the\s+willy|willy\s+wake\s+up)[\s,\.]+", "", cleaned, flags=re.IGNORECASE)
    # Strip polite fillers, questions, and inquiry preambles
    cleaned = re.sub(r"^(?:please|can\s+you\s+please|could\s+you\s+please|may\s+you\s+know|do\s+you\s+know|can\s+you\s+tell\s+me|tell\s+me|can\s+you|could\s+you|would\s+you|will\s+you|may\s+you|do\s+you|just|now)\s+", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def _get_active_window_info() -> Dict[str, Any]:
    """Retrieves active foreground window title and process name locally."""
    hwnd = win32gui.GetForegroundWindow()
    if not hwnd:
        return {"title": "Desktop", "process": "explorer.exe", "reply": "You are currently viewing the desktop."}
    title = win32gui.GetWindowText(hwnd).strip() or "Untitled Window"
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    proc_name = "unknown"
    if psutil:
        try:
            proc_name = psutil.Process(pid).name()
        except Exception:
            pass
    return {
        "title": title,
        "process": proc_name,
        "pid": pid,
        "reply": f"You are currently working in {proc_name} ({title})."
    }


def _get_ram_diagnostics() -> Dict[str, Any]:
    """Inspects RAM and CPU usage, and finds top memory hogging apps."""
    if not psutil:
        return {"reply": "Unable to check memory metrics because psutil is unavailable."}
    vm = psutil.virtual_memory()
    cpu = psutil.cpu_percent(interval=0.1)

    proc_map = {}
    for p in psutil.process_iter(['name', 'memory_info']):
        try:
            name = p.info['name']
            mem_mb = p.info['memory_info'].rss / (1024 * 1024)
            proc_map[name] = proc_map.get(name, 0.0) + mem_mb
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    top_sorted = sorted(proc_map.items(), key=lambda x: x[1], reverse=True)[:3]
    top_str = ", ".join(f"{name} ({round(mem)} MB)" for name, mem in top_sorted)

    status_str = "normal"
    if vm.percent > 90:
        status_str = "very high"
    elif vm.percent > 80:
        status_str = "elevated"

    reply = (
        f"Your RAM usage is {status_str} at {vm.percent}% ({round(vm.used / (1024**3), 1)} GB of {round(vm.total / (1024**3), 1)} GB used), "
        f"with CPU at {cpu}%. Top memory processes are {top_str}."
    )
    return {
        "cpu_percent": cpu,
        "ram_percent": vm.percent,
        "used_gb": round(vm.used / (1024**3), 1),
        "total_gb": round(vm.total / (1024**3), 1),
        "top_processes": top_sorted,
        "reply": reply,
    }


def _boost_system_memory() -> Dict[str, Any]:
    """Frees RAM across active non-system processes using Windows EmptyWorkingSet API."""
    if not psutil:
        return {"reply": "Unable to boost memory because psutil is not available."}
    p_before = psutil.virtual_memory().percent
    psapi = ctypes.windll.psapi
    kernel32 = ctypes.windll.kernel32
    freed_count = 0
    for p in psutil.process_iter(['pid', 'name']):
        try:
            h = kernel32.OpenProcess(0x001F0FFF, False, p.info['pid'])
            if h:
                if psapi.EmptyWorkingSet(h):
                    freed_count += 1
                kernel32.CloseHandle(h)
        except Exception:
            pass
    time.sleep(0.1)
    p_after = psutil.virtual_memory().percent
    reply = f"Boosted! Cleaned memory across {freed_count} processes. RAM dropped from {p_before}% to {p_after}%."
    return {"success": True, "ram_before": p_before, "ram_after": p_after, "processes_trimmed": freed_count, "reply": reply}


def _get_wifi_status() -> Dict[str, Any]:
    """Inspects WiFi connection status, SSID, and signal strength via netsh."""
    try:
        proc = subprocess.run(
            ["netsh", "wlan", "show", "interfaces"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=3
        )
        out = proc.stdout
        ssid_match = re.search(r"^\s*SSID\s*:\s*(.+)$", out, re.MULTILINE)
        state_match = re.search(r"^\s*State\s*:\s*(.+)$", out, re.MULTILINE)
        signal_match = re.search(r"^\s*Signal\s*:\s*(.+)$", out, re.MULTILINE)

        if state_match and "connected" in state_match.group(1).lower() and ssid_match:
            ssid = ssid_match.group(1).strip()
            sig = signal_match.group(1).strip() if signal_match else "good"
            return {"connected": True, "ssid": ssid, "signal": sig, "reply": f"Connected to WiFi network {ssid} with {sig} signal."}
        elif state_match:
            return {"connected": False, "reply": f"WiFi status is currently {state_match.group(1).strip()}."}
        return {"connected": False, "reply": "WiFi is disconnected."}
    except Exception as e:
        return {"connected": False, "reply": f"Could not check WiFi status: {e}"}


def _reboot_wifi() -> Dict[str, Any]:
    """Reboots / reconnects WiFi connection."""
    try:
        subprocess.run(["netsh", "wlan", "disconnect"], capture_output=True, timeout=3)
        time.sleep(1.0)
        p_res = subprocess.run(["netsh", "wlan", "show", "profiles"], capture_output=True, text=True, timeout=3)
        prof_match = re.search(r"All User Profile\s*:\s*(.+)", p_res.stdout)
        if prof_match:
            prof_name = prof_match.group(1).strip()
            subprocess.run(["netsh", "wlan", "connect", f"name={prof_name}"], capture_output=True, timeout=4)
            return {"success": True, "reply": f"Rebooted WiFi and reconnected to {prof_name}."}
        return {"success": True, "reply": "Rebooted WiFi interface."}
    except Exception as e:
        return {"success": False, "reply": f"Failed to restart WiFi: {e}"}


def _open_chrome_profile(query: str) -> Dict[str, Any]:
    """Directly launches Chrome with a specific user profile."""
    prof_data = _get_chrome_profiles()
    q_clean = query.lower().strip()
    target_profile = None
    for p in prof_data.get("profiles", []):
        if q_clean in p["name"].lower() or q_clean in p["dir"].lower() or (p["email"] and q_clean in p["email"].lower()):
            target_profile = p
            break

    if target_profile:
        dir_name = target_profile["dir"]
        disp_name = target_profile["name"]
        cmd = f'start chrome --profile-directory="{dir_name}"'
        subprocess.Popen(cmd, shell=True)
        return {"success": True, "profile": disp_name, "reply": f"Opening Chrome on {disp_name} profile."}
    return {"success": False, "reply": f"I couldn't find a Chrome profile matching '{query}'."}


def _get_chrome_profiles() -> Dict[str, Any]:
    """Inspects Chrome's User Data/Local State file directly with 0ms latency."""
    local_state_path = os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data\Local State")
    if not os.path.isfile(local_state_path):
        return {"profiles": [], "reply": "I couldn't locate Chrome profiles on this computer."}
    try:
        import json
        with open(local_state_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        info_cache = data.get("profile", {}).get("info_cache", {})
        profiles = []
        for dir_name, info in info_cache.items():
            name = info.get("name") or dir_name
            email = info.get("user_name") or ""
            profiles.append({"dir": dir_name, "name": name, "email": email})

        names_list = [f"{p['name']}" for p in profiles]
        names_str = ", ".join(names_list)
        reply = f"You have {len(profiles)} Chrome profiles: {names_str}."
        return {"profiles": profiles, "reply": reply}
    except Exception as e:
        return {"profiles": [], "reply": f"Could not read Chrome profiles: {e}"}


def route_local_intent(text: str) -> Optional[Dict[str, Any]]:
    """
    Evaluates whether a user utterance can be handled completely locally.
    Returns None if the query requires LLM reasoning/cloud intelligence.
    Returns Dict with 'tool_name', 'tool_result', and 'reply' if handled locally.
    """
    if not text:
        return None

    raw = text.strip().lower()
    # Normalize punctuation while preserving alphanumeric characters and whitespace
    cleaned = re.sub(r"[^\w\s]", " ", raw)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    # Pre-clean wake words and conversational fillers
    stripped = _strip_wake_and_fillers(cleaned)
    # If stripped became empty (e.g. user just said "hey willy"), keep original
    target_text = stripped if stripped else cleaned

    # -------------------------------------------------------------
    # 0. Instant Local Greetings & Conversational Acknowledgements
    # -------------------------------------------------------------
    if target_text in ("hi", "hello", "hey", "good morning", "good afternoon", "good evening", "howdy"):
        return {"tool_name": "instant_greet", "tool_result": {"status": "ok"}, "reply": "Hey! How can I help you?"}

    if target_text in ("how are you", "how are you doing", "how r u", "whats up", "what's up"):
        return {"tool_name": "instant_status", "tool_result": {"status": "ok"}, "reply": "I'm doing great and ready for your commands!"}

    if target_text in ("who are you", "what are you", "what is your name", "whats your name"):
        return {"tool_name": "instant_identity", "tool_result": {"status": "ok"}, "reply": "I'm Willy, your Windows voice assistant."}

    if target_text in ("what are you doing", "what r u doing", "what are u doing", "what you doing"):
        return {"tool_name": "instant_activity", "tool_result": {"status": "ok"}, "reply": "I'm standing by, ready for your commands!"}

    if target_text in ("cancel", "never mind", "nevermind", "stop", "nothing", "forget it", "shut up"):
        return {"tool_name": "instant_cancel", "tool_result": {"status": "ok"}, "reply": "Standing by."}

    if target_text in ("thank you", "thanks", "thanks willy", "thank you willy"):
        return {"tool_name": "instant_thanks", "tool_result": {"status": "ok"}, "reply": "You're welcome! Let me know if you need anything else."}

    # -------------------------------------------------------------
    # 1. Direct Typing & Entering Text / Numbers / PIN (Zero Latency)
    # -------------------------------------------------------------
    # Matches "type 4524", "enter 4524", "write hello world", "type now", "input 1234"
    type_match = re.match(
        r"^(?:type|enter|input|write)\s+(?:in\s+)?(?:the\s+)?(?:pin\s+|code\s+|password\s+|number\s+)?(.+)$",
        target_text
    )
    if type_match:
        to_type = type_match.group(1).strip()
        # Filter out false triggers like "type of" or meta questions
        if to_type and not to_type.startswith(("of ", "about ", "question")):
            if to_type == "now":
                # User says "type now" -> dismiss lock/confirm ready or press enter
                if pyautogui:
                    pyautogui.press("enter")
                return {"tool_name": "type_text", "tool_result": {"success": True, "action": "ready"}, "reply": "Ready! What would you like me to type?"}

            if pyautogui:
                old_fs = getattr(pyautogui, "FAILSAFE", True)
                try:
                    pyautogui.FAILSAFE = False
                    pyautogui.write(to_type, interval=0.01)
                finally:
                    pyautogui.FAILSAFE = old_fs
                return {
                    "tool_name": "type_text",
                    "tool_result": {"success": True, "typed": to_type},
                    "reply": f"Typed {to_type}."
                }

    # Direct PIN / Password entry (e.g. "password 1234", "pin 4524", "password mypwd")
    pwd_match = re.match(r"^(?:password|pin|passcode)\s+(?:is\s+)?([a-zA-Z0-9_\-@#\$!\*\.]+?)$", target_text)
    if pwd_match:
        pwd = pwd_match.group(1).strip()
        if pyautogui:
            old_fs = getattr(pyautogui, "FAILSAFE", True)
            try:
                pyautogui.FAILSAFE = False
                pyautogui.write(pwd, interval=0.02)
                pyautogui.press("enter")
            finally:
                pyautogui.FAILSAFE = old_fs
        return {"tool_name": "type_text", "tool_result": {"success": True, "pin": pwd}, "reply": f"Entered {pwd} and pressed Enter."}

    # If user says literally just "type" or "type now"
    if target_text in ("type", "type now", "type here", "start typing"):
        return {"tool_name": "type_text", "tool_result": {"success": True}, "reply": "Ready to type! Tell me what to type."}

    # -------------------------------------------------------------
    # 2. Key Presses & Keyboard Shortcuts (Zero Latency)
    # -------------------------------------------------------------
    # Matches "press space", "hit enter", "press tab", "press escape", "press backspace"
    key_match = re.match(
        r"^(?:press|hit|tap)\s+(?:the\s+)?(enter|return|space|spacebar|esc|escape|tab|backspace|delete|win|windows|up|down|left|right|alt|ctrl)$",
        target_text
    )
    if key_match:
        k = key_match.group(1).strip()
        if k in ("spacebar",):
            k = "space"
        elif k in ("return",):
            k = "enter"
        elif k in ("esc",):
            k = "escape"
        elif k in ("windows",):
            k = "win"

        if pyautogui:
            old_fs = getattr(pyautogui, "FAILSAFE", True)
            try:
                pyautogui.FAILSAFE = False
                pyautogui.press(k)
            finally:
                pyautogui.FAILSAFE = old_fs
            return {"tool_name": "press_key", "tool_result": {"success": True, "key": k}, "reply": f"Pressed {k}."}

    # Common Hotkeys: copy, paste, cut, select all, undo, redo, save, new tab, close tab
    if target_text in ("copy", "copy that", "copy this"):
        if pyautogui:
            pyautogui.hotkey("ctrl", "c")
        return {"tool_name": "hotkey", "tool_result": {"success": True, "keys": ["ctrl", "c"]}, "reply": "Copied to clipboard."}

    if target_text in ("paste", "paste that", "paste here"):
        if pyautogui:
            pyautogui.hotkey("ctrl", "v")
        return {"tool_name": "hotkey", "tool_result": {"success": True, "keys": ["ctrl", "v"]}, "reply": "Pasted."}

    if target_text in ("cut", "cut that"):
        if pyautogui:
            pyautogui.hotkey("ctrl", "x")
        return {"tool_name": "hotkey", "tool_result": {"success": True, "keys": ["ctrl", "x"]}, "reply": "Cut."}

    if target_text in ("select all", "select everything"):
        if pyautogui:
            pyautogui.hotkey("ctrl", "a")
        return {"tool_name": "hotkey", "tool_result": {"success": True, "keys": ["ctrl", "a"]}, "reply": "Selected all."}

    if target_text in ("undo", "undo that"):
        if pyautogui:
            pyautogui.hotkey("ctrl", "z")
        return {"tool_name": "hotkey", "tool_result": {"success": True, "keys": ["ctrl", "z"]}, "reply": "Undone."}

    if target_text in ("redo", "redo that"):
        if pyautogui:
            pyautogui.hotkey("ctrl", "y")
        return {"tool_name": "hotkey", "tool_result": {"success": True, "keys": ["ctrl", "y"]}, "reply": "Redone."}

    if target_text in ("save", "save file", "save document"):
        if pyautogui:
            pyautogui.hotkey("ctrl", "s")
        return {"tool_name": "hotkey", "tool_result": {"success": True, "keys": ["ctrl", "s"]}, "reply": "Saved."}

    # Tab Navigation (Ctrl+Tab, Ctrl+Shift+Tab, Ctrl+W, Ctrl+T, Ctrl+Shift+T)
    if target_text in ("switch tab", "next tab", "switch tabs", "cycle tab", "cycle tabs", "go to next tab", "tab switch"):
        if pyautogui:
            pyautogui.hotkey("ctrl", "tab")
        return {"tool_name": "hotkey", "tool_result": {"success": True, "keys": ["ctrl", "tab"]}, "reply": "Switched to next tab."}

    if target_text in ("previous tab", "prev tab", "last tab", "back tab", "go to previous tab"):
        if pyautogui:
            pyautogui.hotkey("ctrl", "shift", "tab")
        return {"tool_name": "hotkey", "tool_result": {"success": True, "keys": ["ctrl", "shift", "tab"]}, "reply": "Switched to previous tab."}

    if target_text in ("new tab", "open new tab", "open a new tab", "create tab", "open tab"):
        if pyautogui:
            pyautogui.hotkey("ctrl", "t")
        return {"tool_name": "hotkey", "tool_result": {"success": True, "keys": ["ctrl", "t"]}, "reply": "Opened new tab."}

    if target_text in ("close tab", "close current tab", "exit tab"):
        if pyautogui:
            pyautogui.hotkey("ctrl", "w")
        return {"tool_name": "hotkey", "tool_result": {"success": True, "keys": ["ctrl", "w"]}, "reply": "Closed tab."}

    if target_text in ("reopen tab", "restore tab", "undo close tab"):
        if pyautogui:
            pyautogui.hotkey("ctrl", "shift", "t")
        return {"tool_name": "hotkey", "tool_result": {"success": True, "keys": ["ctrl", "shift", "t"]}, "reply": "Reopened closed tab."}

    # Window Management (Alt+Tab, Alt+F4, Win+Down, Win+Up, Win+D)
    if target_text in ("switch window", "alt tab", "switch app", "switch application", "next window", "change window"):
        if pyautogui:
            pyautogui.hotkey("alt", "tab")
        return {"tool_name": "hotkey", "tool_result": {"success": True, "keys": ["alt", "tab"]}, "reply": "Switched window."}

    if target_text in ("close window", "close this window", "exit window", "quit window"):
        if pyautogui:
            pyautogui.hotkey("alt", "f4")
        return {"tool_name": "hotkey", "tool_result": {"success": True, "keys": ["alt", "f4"]}, "reply": "Closed window."}

    if target_text in ("minimize", "minimize window", "minimize this window"):
        if pyautogui:
            pyautogui.hotkey("win", "down")
        return {"tool_name": "hotkey", "tool_result": {"success": True, "keys": ["win", "down"]}, "reply": "Minimized window."}

    if target_text in ("maximize", "maximize window", "maximize this window", "fullscreen", "full screen"):
        if pyautogui:
            pyautogui.hotkey("win", "up")
        return {"tool_name": "hotkey", "tool_result": {"success": True, "keys": ["win", "up"]}, "reply": "Maximized window."}

    if target_text in ("show desktop", "minimize all", "go to desktop"):
        if pyautogui:
            pyautogui.hotkey("win", "d")
        return {"tool_name": "hotkey", "tool_result": {"success": True, "keys": ["win", "d"]}, "reply": "Showing desktop."}

    # Directional Movement
    if target_text in ("moving right", "move right", "scroll right", "go right", "right"):
        if pyautogui:
            pyautogui.press("right", presses=6)
        return {"tool_name": "press_key", "tool_result": {"success": True, "direction": "right"}, "reply": "Moved right."}

    if target_text in ("moving left", "move left", "scroll left", "go left", "left"):
        if pyautogui:
            pyautogui.press("left", presses=6)
        return {"tool_name": "press_key", "tool_result": {"success": True, "direction": "left"}, "reply": "Moved left."}

    if target_text in ("move up", "go up"):
        if pyautogui:
            pyautogui.press("up", presses=6)
        return {"tool_name": "press_key", "tool_result": {"success": True, "direction": "up"}, "reply": "Moved up."}

    if target_text in ("move down", "go down"):
        if pyautogui:
            pyautogui.press("down", presses=6)
        return {"tool_name": "press_key", "tool_result": {"success": True, "direction": "down"}, "reply": "Moved down."}

    # -------------------------------------------------------------
    # 3. Screen Scrolling (Robust Fuzzy & Direction Matching)
    # -------------------------------------------------------------
    # Matches: "scroll the screen", "scroll down", "scroll up", "scroll screen", "scroll page", "page down", "page up", "scroll to top", "scroll to bottom"
    if any(p in target_text for p in ("scroll lock screen", "scroll the lock screen", "swipe up lock screen", "swipe up", "dismiss lock screen", "lift lock screen")):
        res = unlock_or_wake()
        return {"tool_name": "unlock_or_wake", "tool_result": res, "reply": "Dismissed lock screen wallpaper."}

    if "scroll" in target_text or "page down" in target_text or "page up" in target_text:
        if any(w in target_text for w in ("top", "home", "start")):
            res = scroll_screen(direction="top")
            return {"tool_name": "scroll_screen", "tool_result": res, "reply": "Scrolled to the top."}
        elif any(w in target_text for w in ("bottom", "end")):
            res = scroll_screen(direction="bottom")
            return {"tool_name": "scroll_screen", "tool_result": res, "reply": "Scrolled to the bottom."}
        elif any(w in target_text for w in ("page up", "prev page", "one page up")):
            res = scroll_screen(direction="page_up")
            return {"tool_name": "scroll_screen", "tool_result": res, "reply": "Scrolled one page up."}
        elif any(w in target_text for w in ("page down", "next page", "one page down")):
            res = scroll_screen(direction="page_down")
            return {"tool_name": "scroll_screen", "tool_result": res, "reply": "Scrolled one page down."}
        elif "up" in target_text:
            res = scroll_screen(direction="up", amount=6)
            return {"tool_name": "scroll_screen", "tool_result": res, "reply": "Scrolled up."}
        else:
            # Default for "scroll", "scroll screen", "scroll the screen", "scroll down"
            res = scroll_screen(direction="down", amount=6)
            return {"tool_name": "scroll_screen", "tool_result": res, "reply": "Scrolled down."}

    # -------------------------------------------------------------
    # 4. Power & Lock / Unlock / Wake Commands
    # -------------------------------------------------------------
    # Wake / Unlock
    if target_text.startswith("unlock") or target_text.startswith("unblock") or any(p in target_text for p in (
        "unlock pc", "unlock the pc", "unlock computer", "unblock pc", "unblock the pc", "unlock screen",
        "wake up screen", "wake up display", "wake screen", "wake display", "wake pc", "wake up", "willy wake up"
    )):
        # Extract potential PIN or password from command (e.g. "unlock with 4524", "unlock pc with pin 1234", "unlock with password secret")
        pin_match = re.search(r"(?:pin|password|code|with)\s+([a-zA-Z0-9_\-@#\$!\*\.]+)", target_text)
        if not pin_match:
            pin_match = re.search(r"\b(\d{4,8})\b", target_text)
        pin_val = pin_match.group(1) if pin_match else None
        res = unlock_or_wake(pin=pin_val)
        reply = "Screen awakened and lock screen dismissed." if not pin_val else f"Screen awakened and entered PIN {pin_val}."
        return {"tool_name": "unlock_or_wake", "tool_result": res, "reply": reply}

    # Lock
    if any(p in target_text for p in (
        "lock pc", "lock the pc", "lock computer", "lock the computer", "lock screen", "lock workstation"
    )):
        res = lock_workstation()
        return {"tool_name": "lock_workstation", "tool_result": res, "reply": "Workstation locked."}

    if any(p in target_text for p in ("shutdown pc", "shut down pc", "shutdown the pc", "turn off the pc", "turn off computer", "shutdown computer", "shutdown")):
        res = shutdown_system(delay_sec=30)
        return {"tool_name": "shutdown_system", "tool_result": res, "reply": "Shutting down the computer in 30 seconds. Say 'cancel shutdown' to abort."}

    if ("wifi" not in target_text and "network" not in target_text) and any(p in target_text for p in ("restart pc", "reboot pc", "restart computer", "reboot computer", "restart", "reboot")):
        res = restart_system(delay_sec=30)
        return {"tool_name": "restart_system", "tool_result": res, "reply": "Restarting the computer in 30 seconds."}

    if any(p in target_text for p in ("cancel shutdown", "abort shutdown", "stop shutdown", "don't shutdown")):
        res = cancel_shutdown()
        return {"tool_name": "cancel_shutdown", "tool_result": res, "reply": res.get("message", "Shutdown cancelled.")}

    if any(p in target_text for p in ("sleep pc", "put pc to sleep", "sleep computer", "go to sleep")):
        res = sleep_system()
        return {"tool_name": "sleep_system", "tool_result": res, "reply": "Putting the computer to sleep."}

    # -------------------------------------------------------------
    # 5. Window & Workspace Awareness (What's open / active)
    # -------------------------------------------------------------
    if "which window" in target_text or "what window" in target_text or any(p in target_text for p in (
        "which window am i using", "what window am i using", "which window is active", "what window is active",
        "what window am i on", "which window is open", "what app is open", "which app is open",
        "what am i looking at", "what screen is active", "what monitor is active"
    )):
        res = _get_active_window_info()
        return {"tool_name": "get_active_window_info", "tool_result": res, "reply": res["reply"]}

    if any(p in target_text for p in (
        "where is my cursor", "where is my mouse", "find my cursor", "find my mouse",
        "which screen am i on", "what screen am i on", "which monitor am i on", "what monitor am i on",
        "how many screens are connected", "how many monitors do i have", "how many screens do i have",
        "list my monitors", "show my screens", "check my screens"
    )):
        from willy.tools.vision_tools import get_workspace_focus_info
        res = get_workspace_focus_info()
        return {"tool_name": "get_workspace_focus_info", "tool_result": res, "reply": res.get("reply", "Checked your screen layout.")}

    # -------------------------------------------------------------
    # 6. System Diagnostics & Performance (Heavy Laptop / Memory)
    # -------------------------------------------------------------
    # Matches "heavily locked laptop", "laptop is heavy", "why is laptop slow", "check ram", "check cpu"
    if any(p in target_text for p in (
        "heavily locked laptop", "laptop is heavy", "laptop heavy", "why is laptop slow", "laptop slow",
        "computer slow", "pc slow", "laptop hanging", "check ram", "check memory", "ram usage", "memory usage",
        "what is eating ram", "what is using memory", "ram status"
    )):
        res = _get_ram_diagnostics()
        return {"tool_name": "get_ram_diagnostics", "tool_result": res, "reply": res["reply"]}

    # Computer / RAM Booster (Zero-latency offline memory trim)
    if any(p in target_text for p in (
        "boost computer", "boost the computer", "boost window", "boost the window", "boost pc", "boost the pc",
        "boost laptop", "boost my laptop", "boost system", "boost performance", "clean ram", "clear ram",
        "free ram", "free memory", "boost"
    )):
        res = _boost_system_memory()
        return {"tool_name": "boost_system_memory", "tool_result": res, "reply": res["reply"]}

    if any(p in target_text for p in ("cpu usage", "check cpu", "check system status", "system metrics")):
        res = get_system_status()
        st = res.get("status", {})
        cpu = st.get("cpu_percent", 0)
        mem = st.get("memory", {}).get("percent", 0)
        return {"tool_name": "get_system_status", "tool_result": res, "reply": f"CPU utilization is at {cpu}%, and RAM usage is at {mem}%."}

    if any(p in target_text for p in ("battery status", "what is my battery", "check battery", "how much battery", "battery percentage")):
        if psutil:
            battery = psutil.sensors_battery()
            if battery:
                plugged = "plugged in" if battery.power_plugged else "running on battery"
                reply = f"Your battery is at {battery.percent}% and {plugged}."
                return {"tool_name": "get_battery", "tool_result": {"percent": battery.percent, "plugged": battery.power_plugged}, "reply": reply}

    # -------------------------------------------------------------
    # 7. Time & Date
    # -------------------------------------------------------------
    if any(p in target_text for p in ("what time is it", "what is the time", "current time", "tell me the time")):
        now_str = datetime.datetime.now().strftime("%I:%M %p")
        return {"tool_name": "get_local_time", "tool_result": {"time": now_str}, "reply": f"It is currently {now_str}."}

    if any(p in target_text for p in ("what date is it", "what is the date", "today's date", "tell me the date", "what is today")):
        date_str = datetime.datetime.now().strftime("%A, %B %d, %Y")
        return {"tool_name": "get_local_date", "tool_result": {"date": date_str}, "reply": f"Today is {date_str}."}

    # -------------------------------------------------------------
    # 8. Volume & Media Control
    # -------------------------------------------------------------
    if any(p in target_text for p in ("mute", "mute audio", "mute sound", "mute volume")):
        res = volume_control("mute")
        return {"tool_name": "volume_control", "tool_result": res, "reply": "Muted system audio."}

    if any(p in target_text for p in ("unmute", "unmute audio", "unmute sound")):
        res = volume_control("unmute")
        return {"tool_name": "volume_control", "tool_result": res, "reply": "Unmuted system audio."}

    if any(p in target_text for p in ("volume up", "louder", "turn it up", "increase volume")):
        res = volume_control("up")
        return {"tool_name": "volume_control", "tool_result": res, "reply": "Turned volume up."}

    if any(p in target_text for p in ("volume down", "quieter", "turn it down", "lower volume", "decrease volume")):
        res = volume_control("down")
        return {"tool_name": "volume_control", "tool_result": res, "reply": "Turned volume down."}

    # -------------------------------------------------------------
    # 9. Windows Theme (Dark / Light)
    # -------------------------------------------------------------
    if any(p in target_text for p in ("dark mode", "enable dark mode", "switch to dark mode", "turn on dark mode", "set dark theme")):
        res = set_windows_theme("dark")
        return {"tool_name": "set_windows_theme", "tool_result": res, "reply": "Switched Windows to Dark Mode."}

    if any(p in target_text for p in ("light mode", "enable light mode", "switch to light mode", "turn on light mode", "set light theme", "swiss 2 light mode", "switch 2 light mode")):
        res = set_windows_theme("light")
        return {"tool_name": "set_windows_theme", "tool_result": res, "reply": "Switched Windows to Light Mode."}

    # Chrome Profiles (Listing or Direct Switching / Opening)
    if "chrome profile" in target_text or "chrome profiles" in target_text or "profile of that chrome" in target_text or "profile name of that chrome" in target_text or ("profile" in target_text and "chrome" in target_text) or re.match(r"^(?:switch\s+to|switch)\s+chrome\s+(.+)$", target_text):
        if any(p in target_text for p in ("how many", "list", "which", "show", "what", "name")):
            res = _get_chrome_profiles()
            return {"tool_name": "get_chrome_profiles", "tool_result": res, "reply": res["reply"]}
        # Direct switch or open (e.g. "open chrome profile work", "switch to chrome personal", "go to work chrome profile")
        prof_query = re.sub(r"^(?:open|switch\s+to|switch|go\s+to|launch)\s+", "", target_text)
        prof_query = re.sub(r"\bchrome\b|\bprofile\b|\bfor\b|\bin\b|\bthe\b|\bto\b", "", prof_query).strip()
        if prof_query:
            res = _open_chrome_profile(prof_query)
            return {"tool_name": "open_chrome_profile", "tool_result": res, "reply": res["reply"]}
        else:
            res = _get_chrome_profiles()
            return {"tool_name": "get_chrome_profiles", "tool_result": res, "reply": res["reply"]}

    # -------------------------------------------------------------
    # 10. Direct Web Search & Navigation ("search google for X", "open youtube")
    # -------------------------------------------------------------
    google_search_match = re.match(r"^(?:search\s+google\s+for|google|search\s+for)\s+(.+)$", target_text)
    if google_search_match:
        q = google_search_match.group(1).strip()
        encoded = urllib.parse.quote_plus(q)
        res = open_url(f"https://www.google.com/search?q={encoded}")
        return {"tool_name": "open_url", "tool_result": res, "reply": f"Searching Google for {q}."}

    youtube_search_match = re.match(r"^(?:search\s+youtube\s+for|youtube)\s+(.+)$", target_text)
    if youtube_search_match:
        q = youtube_search_match.group(1).strip()
        encoded = urllib.parse.quote_plus(q)
        res = open_url(f"https://www.youtube.com/results?search_query={encoded}")
        return {"tool_name": "open_url", "tool_result": res, "reply": f"Searching YouTube for {q}."}

    # Direct Web Destinations
    web_dest_match = re.match(r"^(?:open|go\s+to)\s+(youtube|google|gmail|github|chatgpt|reddit|twitter|facebook|instagram|spotify|netflix|amazon)$", target_text)
    if web_dest_match:
        site = web_dest_match.group(1).strip()
        site_urls = {
            "youtube": "https://www.youtube.com",
            "google": "https://www.google.com",
            "gmail": "https://mail.google.com",
            "github": "https://github.com",
            "chatgpt": "https://chatgpt.com",
            "reddit": "https://www.reddit.com",
            "twitter": "https://x.com",
            "facebook": "https://www.facebook.com",
            "instagram": "https://www.instagram.com",
            "spotify": "https://open.spotify.com",
            "netflix": "https://www.netflix.com",
            "amazon": "https://www.amazon.com",
        }
        target_url = site_urls.get(site, f"https://www.{site}.com")
        res = open_url(target_url)
        return {"tool_name": "open_url", "tool_result": res, "reply": f"Opening {site} in your browser."}

    # -------------------------------------------------------------
    # 11. Direct Local File Search ("find file relay", "search files for invoice")
    # -------------------------------------------------------------
    file_search_match = re.match(
        r"^(?:search|find|locate)\s+(?:for\s+)?(?:the\s+)?(?:files?\s+(?:for\s+|named\s+)?|documents?\s+(?:for\s+|named\s+)?)([a-zA-Z0-9_\-\.\s]+?)$",
        target_text
    )
    if file_search_match:
        search_target = file_search_match.group(1).strip()
        if search_target and not search_target.startswith(("weather", "internet", "google", "youtube", "how", "what", "who", "where", "my ")):
            from willy.tools.file_search_tools import search_files
            res = search_files(query=search_target)
            return {"tool_name": "search_files", "tool_result": res, "reply": res.get("reply", f"Searched for '{search_target}'.")}

    # -------------------------------------------------------------
    # 12. Close / Kill Application ("close chrome", "kill genshin", "exit notepad")
    # -------------------------------------------------------------
    close_match = re.match(
        r"^(?:close|kill|exit|terminate|quit)\s+(?:the\s+)?([a-zA-Z0-9\s]+?)$",
        target_text
    )
    if close_match:
        app_target = close_match.group(1).strip()
        if app_target not in ("willy", "system", "windows", "desktop"):
            res = close_app(app_target)
            return {"tool_name": "close_app", "tool_result": res, "reply": res.get("message", f"Closed {app_target}.")}

    # -------------------------------------------------------------
    # 13. Application Status ("is Chrome open", "check if Genshin is running")
    # -------------------------------------------------------------
    is_running_match = re.match(
        r"^(?:is|check if|see if)\s+(?:the\s+)?([a-zA-Z0-9\s]+?)\s+(?:open|running|active)$",
        target_text
    )
    if is_running_match:
        app_target = is_running_match.group(1).strip()
        res = check_app_running(app_target)
        return {"tool_name": "check_app_running", "tool_result": res, "reply": res.get("message", f"Checked status of {app_target}.")}

    # -------------------------------------------------------------
    # 14. Application Launching (Phrased or Standalone App Names)
    # -------------------------------------------------------------
    # Known standalone app keywords
    standalone_apps = {
        "calculator": "calc",
        "calc": "calc",
        "notepad": "notepad",
        "chrome": "chrome",
        "google chrome": "chrome",
        "edge": "msedge",
        "microsoft edge": "msedge",
        "browser": "msedge",
        "spotify": "spotify",
        "genshin": "Genshin Impact",
        "genshin impact": "Genshin Impact",
        "task manager": "taskmgr",
        "taskmgr": "taskmgr",
        "settings": "ms-settings:",
        "control panel": "control",
        "terminal": "wt",
        "powershell": "powershell",
        "cmd": "cmd",
        "command prompt": "cmd",
        "explorer": "explorer",
        "file explorer": "explorer",
        "files": "explorer",
        "paint": "mspaint",
        "mspaint": "mspaint",
        "word": "winword",
        "excel": "excel",
    }

    # If the user said JUST the name of the app (e.g. "calculator", "chrome", "notepad")
    if target_text in standalone_apps:
        target_app = standalone_apps[target_text]
        res = launch_windows_app(target_app)
        display_name = res.get("target", target_text.title())
        return {
            "tool_name": "launch_windows_app",
            "tool_result": res,
            "reply": f"Opening {display_name}."
        }

    # Camera & Photos (Windows Camera & Photo Capture)
    if any(p in target_text for p in ("open camera", "turn on camera", "start camera", "camera app")):
        from willy.tools.camera_tools import open_camera_app
        res = open_camera_app()
        return {"tool_name": "launch_windows_app", "tool_result": res, "reply": "Opening Windows Camera."}

    if any(p in target_text for p in ("take photo", "take a photo", "click photo", "click a photo", "take picture", "take a picture", "click picture", "click a picture", "snap photo", "capture photo", "snap picture")):
        from willy.tools.camera_tools import take_photo
        res = take_photo()
        return {"tool_name": "take_photo", "tool_result": res, "reply": res.get("message", "Photo captured.")}

    # Window Switching & Foreground Management
    focus_match = re.match(
        r"^(?:switch\s+to|bring\s+up|bring|focus|show|activate|go\s+to)\s+(?:the\s+)?([a-zA-Z0-9\s]+?)(?:\s+to\s+(?:the\s+)?(?:front|foreground))?$",
        target_text
    )
    if focus_match:
        target_window = focus_match.group(1).strip()
        if target_window not in ("me", "my", "it", "this", "that") and len(target_window.split()) <= 4:
            from willy.tools.window_tools import focus_window
            res = focus_window(target_window)
            if res.get("success"):
                return {
                    "tool_name": "focus_window",
                    "tool_result": res,
                    "reply": f"Brought {res.get('title', target_window)} to the front."
                }

    if any(p in target_text for p in ("take screenshot", "take a screenshot", "capture screen", "snip screen", "screenshot")):
        if pyautogui:
            pyautogui.hotkey("win", "shift", "s")
        return {"tool_name": "hotkey", "tool_result": {"keys": ["win", "shift", "s"]}, "reply": "Snip tool activated for screenshot."}

    # Matches "open chrome", "launch notepad", "start genshin", "open my calculator", "now open genshin impact"
    open_match = re.match(
        r"^(?:open|launch|start|run)\s+(?:my\s+)?(?:the\s+)?([a-zA-Z0-9\s]+?)$",
        target_text
    )
    if open_match:
        app_target = open_match.group(1).strip()
        # Ensure it's not a complex sentence or internet query
        if len(app_target.split()) <= 4 and " and " not in app_target and " with " not in app_target:
            res = launch_windows_app(app_target)
            if res.get("success"):
                display_name = res.get("target", app_target.title())
                return {
                    "tool_name": "launch_windows_app",
                    "tool_result": res,
                    "reply": f"Opening {display_name}."
                }

    # -------------------------------------------------------------
    # 15. Network, WiFi & Bluetooth Control (Zero Latency)
    # -------------------------------------------------------------
    # WiFi Reboot / Restart / Reconnect
    if any(p in target_text for p in ("reboot wifi", "restart wifi", "reset wifi", "wifi reboot", "wifi restart")):
        res = _reboot_wifi()
        return {"tool_name": "reboot_wifi", "tool_result": res, "reply": res["reply"]}

    # WiFi Disconnect
    if any(p in target_text for p in ("disconnect wifi", "turn off wifi", "disable wifi", "stop wifi", "wifi disconnect")):
        subprocess.run(["netsh", "wlan", "disconnect"], capture_output=True, timeout=3)
        return {"tool_name": "disconnect_wifi", "tool_result": {"success": True}, "reply": "Disconnected from WiFi."}

    # WiFi Connect / Reconnect
    if any(p in target_text for p in ("connect wifi", "reconnect wifi", "turn on wifi", "enable wifi", "wifi connect")):
        res = _reboot_wifi()
        return {"tool_name": "connect_wifi", "tool_result": res, "reply": res["reply"]}

    # WiFi Status / Details
    if any(p in target_text for p in ("check wifi", "wifi status", "is wifi connected", "what wifi", "which wifi", "wifi name")):
        res = _get_wifi_status()
        return {"tool_name": "get_wifi_status", "tool_result": res, "reply": res["reply"]}

    # Bluetooth Management
    if any(p in target_text for p in ("bluetooth", "open bluetooth", "bluetooth settings", "bluetooth devices", "connect bluetooth", "disconnect bluetooth", "check bluetooth")):
        subprocess.Popen("start ms-settings:bluetooth", shell=True)
        return {"tool_name": "open_settings", "tool_result": {"page": "bluetooth"}, "reply": "Opening Bluetooth settings."}

    if any(p in target_text for p in ("check internet", "is internet working", "do we have internet", "internet status", "check network", "test internet")):
        res = check_network_status()
        return {"tool_name": "check_network_status", "tool_result": res, "reply": res.get("message", "Checked internet status.")}

    # Not a simple deterministic local intent -> proceed to cloud intelligence
    return None
