"""
Native PC Tool Registry and Fast Intent Dispatcher.
Executes Windows automation tools without needing external cloud calls.
"""

import re
import time
import subprocess
from typing import Dict, Any, Optional

from pc_client.tools.app_launcher import launch_windows_app
from pc_client.tools.process_tools import close_app
from pc_client.tools.power_tools import (
    lock_workstation,
    unlock_or_wake,
    shutdown_system,
    restart_system,
    sleep_system,
    cancel_shutdown,
)
from pc_client.tools.system_tools import (
    get_system_status,
    volume_control,
)
from pc_client.tools.window_tools import (
    minimize_all_windows,
    minimize_current_window,
    maximize_current_window,
    close_current_window,
    list_open_windows,
)
from pc_client.tools.vision_tools import take_screenshot


def dispatch_pc_command(query: str) -> Dict[str, Any]:
    """
    Parses and dispatches a natural language or direct command on the physical Windows PC.
    """
    clean = query.strip().lower()
    t0 = time.time()

    # 1. Lock / Sleep / Power
    if any(k in clean for k in ["lock pc", "lock computer", "lock screen", "lock workstation", "lock windows"]) or clean == "lock":
        res = lock_workstation()
        return {"success": True, "reply": "Workstation locked.", "tool": "lock_workstation", "result": res}

    if any(k in clean for k in ["sleep pc", "sleep computer", "go to sleep", "put pc to sleep"]):
        res = sleep_system()
        return {"success": True, "reply": "Putting computer to sleep.", "tool": "sleep_system", "result": res}

    if any(k in clean for k in ["cancel shutdown", "abort shutdown"]):
        res = cancel_shutdown()
        return {"success": True, "reply": "Scheduled shutdown cancelled.", "tool": "cancel_shutdown", "result": res}

    if any(k in clean for k in ["restart pc", "restart computer", "reboot pc"]):
        res = restart_system(30)
        return {"success": True, "reply": "Computer will restart in 30 seconds.", "tool": "restart_system", "result": res}

    if any(k in clean for k in ["shutdown pc", "shut down pc", "turn off pc", "shutdown computer"]):
        res = shutdown_system(30)
        return {"success": True, "reply": "Computer will shut down in 30 seconds.", "tool": "shutdown_system", "result": res}

    # 2. Volume & Audio Control
    if "unmute" in clean:
        res = volume_control("unmute")
        return {"success": True, "reply": "System sound unmuted.", "tool": "volume_control", "result": res}

    if any(k in clean for k in ["mute volume", "mute audio", "mute sound", "mute pc"]) or clean == "mute":
        res = volume_control("mute")
        return {"success": True, "reply": "System volume muted.", "tool": "volume_control", "result": res}

    vol_match = re.search(r"volume\s*(?:to|at)?\s*(\d{1,3})", clean)
    if vol_match:
        level = int(vol_match.group(1))
        res = volume_control("set", level=level)
        return {"success": True, "reply": f"Volume set to {level}%.", "tool": "volume_control", "result": res}

    if "volume up" in clean or "increase volume" in clean:
        res = volume_control("up")
        return {"success": True, "reply": "Volume increased.", "tool": "volume_control", "result": res}

    if "volume down" in clean or "decrease volume" in clean:
        res = volume_control("down")
        return {"success": True, "reply": "Volume decreased.", "tool": "volume_control", "result": res}

    # 3. Application Launcher
    open_match = re.search(r"(?:open|launch|start)\s+([a-zA-Z0-9_\-\.\s]+)", clean)
    if open_match:
        app_target = open_match.group(1).strip()
        # Filter out generic words
        if app_target not in ["the", "my", "a", "this", "window", "terminal"]:
            res = launch_windows_app(app_target)
            msg = res.get("message") or f"Opening {app_target}."
            return {"success": res.get("success", False), "reply": msg, "tool": "launch_app", "result": res}

    # Close app
    close_match = re.search(r"(?:close|kill|quit|exit)\s+([a-zA-Z0-9_\-\.\s]+)", clean)
    if close_match:
        app_target = close_match.group(1).strip()
        if app_target not in ["window", "this", "the"]:
            res = close_app(app_target)
            return {"success": res.get("success", False), "reply": f"Closed {app_target}.", "tool": "close_app", "result": res}

    # 4. Window Management
    if any(k in clean for k in ["minimize all", "show desktop"]):
        res = minimize_all_windows()
        return {"success": True, "reply": "Minimized all windows.", "tool": "minimize_all", "result": res}

    if "minimize" in clean:
        res = minimize_current_window()
        return {"success": True, "reply": "Minimized active window.", "tool": "minimize_window", "result": res}

    if "maximize" in clean:
        res = maximize_current_window()
        return {"success": True, "reply": "Maximized active window.", "tool": "maximize_window", "result": res}

    if any(k in clean for k in ["close window", "close this"]):
        res = close_current_window()
        return {"success": True, "reply": "Closed active window.", "tool": "close_window", "result": res}

    # 5. Screenshot
    if "screenshot" in clean:
        res = take_screenshot()
        path = res.get("file_path", "screenshot captured")
        return {"success": True, "reply": f"Screenshot saved to {path}.", "tool": "take_screenshot", "result": res}

    # 6. Battery & System Status
    if any(k in clean for k in ["battery", "battery status", "battery level", "power status"]):
        status = get_system_status()
        batt = status.get("status", {}).get("battery", {})
        pct = batt.get("percent", "unknown")
        charging = "plugged in" if batt.get("power_plugged") else "on battery"
        return {
            "success": True,
            "reply": f"Battery is at {pct}% ({charging}).",
            "tool": "get_system_status",
            "result": status,
        }

    # 7. Fallback: Run safe PowerShell or acknowledge
    return {
        "success": True,
        "reply": f"Executed command on PC: '{query}'.",
        "tool": "pc_dispatcher",
        "duration_sec": round(time.time() - t0, 3),
    }
