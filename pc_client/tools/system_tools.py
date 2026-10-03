"""
Deterministic Windows system execution tools for Willy.
"""

import os
import subprocess
import webbrowser
import time
from typing import Optional, List, Dict, Any


# Optional dependencies for UI & telemetry
try:
    import pyautogui
    pyautogui.FAILSAFE = True
except ImportError:
    pyautogui = None

try:
    import psutil
except ImportError:
    psutil = None

try:
    import pygetwindow as gw
except ImportError:
    gw = None


# Common Windows application aliases
APP_ALIASES = {
    "calc": "calc.exe",
    "calculator": "calc.exe",
    "notepad": "notepad.exe",
    "chrome": "chrome",
    "google chrome": "chrome",
    "edge": "msedge",
    "browser": "msedge",
    "explorer": "explorer.exe",
    "file explorer": "explorer.exe",
    "files": "explorer.exe",
    "task manager": "taskmgr.exe",
    "taskmgr": "taskmgr.exe",
    "cmd": "cmd.exe",
    "command prompt": "cmd.exe",
    "powershell": "powershell.exe",
    "terminal": "wt.exe",
    "windows terminal": "wt.exe",
    "vscode": "code",
    "code": "code",
    "spotify": "spotify",
    "settings": "ms-settings:",
    "control panel": "control.exe",
}


def execute_powershell(command: str, timeout: int = None) -> Dict[str, Any]:
    """
    Executes a command via PowerShell, capturing stdout/stderr and exit code safely.
    """
    timeout = timeout or 15
    start_time = time.time()

    cmd = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy", "Bypass",
        "-Command", command,
    ]

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            shell=False,
            encoding="utf-8",
            errors="replace",
        )
        duration = round(time.time() - start_time, 2)
        stdout = proc.stdout.strip()
        stderr = proc.stderr.strip()

        # Truncate giant outputs to protect LLM context length
        if len(stdout) > 3000:
            stdout = stdout[:3000] + "\n... [Output truncated]"
        if len(stderr) > 1000:
            stderr = stderr[:1000] + "\n... [Stderr truncated]"

        return {
            "success": proc.returncode == 0,
            "exit_code": proc.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "duration_sec": duration,
        }

    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Error: Command timed out after {timeout} seconds.",
            "duration_sec": timeout,
        }
    except Exception as e:
        return {
            "success": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Execution error: {str(e)}",
            "duration_sec": round(time.time() - start_time, 2),
        }


def launch_application(target: str, args: str = "") -> Dict[str, Any]:
    """
    Launches applications, URIs, or files using Windows universal app resolution.
    Guarantees discovering and opening ANY app installed on the user's system.
    """
    from pc_client.tools.app_launcher import launch_windows_app
    return launch_windows_app(target=target, args=args)


def open_url(url: str) -> Dict[str, Any]:
    """
    Opens a target URL in the user's default browser.
    """
    url_clean = url.strip()
    if not url_clean.startswith(("http://", "https://")):
        url_clean = "https://" + url_clean

    try:
        webbrowser.open(url_clean)
        # Ensure browser window is brought to foreground
        time.sleep(0.3)
        try:
            from pc_client.tools.window_tools import focus_window
            focus_window("browser")
        except Exception:
            pass

        return {
            "success": True,
            "url": url_clean,
            "message": f"Opened {url_clean} in browser",
        }
    except Exception as e:
        return {
            "success": False,
            "url": url,
            "error": f"Failed to open URL '{url}': {str(e)}",
        }


def interact_ui(
    action: str,
    keys: Optional[List[str]] = None,
    text: Optional[str] = None,
    coordinates: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """
    Automates keyboard and mouse actions via PyAutoGUI.
    Supported actions:
      - 'hotkey': presses a combination of keys (e.g. ['win', 'd'], ['ctrl', 'c'])
      - 'type': types text into active field
      - 'press': presses a single key (e.g. 'enter', 'space', 'esc')
      - 'click': clicks mouse at optional coordinates [x, y]
      - 'scroll': scrolls up (positive) or down (negative)
    """
    if pyautogui is None:
        return {"success": False, "error": "pyautogui is not installed"}

    action = action.lower().strip()
    try:
        if action == "hotkey":
            if not keys:
                return {"success": False, "error": "Missing 'keys' parameter for hotkey action"}
            pyautogui.hotkey(*[k.lower() for k in keys])
            return {"success": True, "action": "hotkey", "keys": keys}

        elif action == "type":
            if text is None:
                return {"success": False, "error": "Missing 'text' parameter for type action"}
            # Type text
            pyautogui.write(text, interval=0.02)
            return {"success": True, "action": "type", "text": text}

        elif action == "press":
            if not keys or len(keys) == 0:
                key = "enter"
            else:
                key = keys[0]
            pyautogui.press(key.lower())
            return {"success": True, "action": "press", "key": key}

        elif action == "click":
            if coordinates and len(coordinates) >= 2:
                pyautogui.click(x=coordinates[0], y=coordinates[1])
            else:
                pyautogui.click()
            return {"success": True, "action": "click", "coordinates": coordinates}

        elif action == "scroll":
            dir_str = "up" if text and not text.startswith("-") and text.isdigit() else "down"
            return scroll_screen(direction=dir_str, amount=5)

        else:
            return {"success": False, "error": f"Unknown action: {action}"}

    except Exception as e:
        return {"success": False, "error": f"UI interaction failed: {str(e)}"}


def scroll_screen(direction: str = "down", amount: int = 5) -> Dict[str, Any]:
    """
    Scrolls the currently active window, browser tab, or document.
    Directions: 'down', 'up', 'page_down', 'page_up', 'top', 'bottom'.
    Amount: number of wheel clicks (default 5, ~600px).
    """
    import ctypes
    user32 = ctypes.windll.user32
    d = direction.lower().strip().replace(" ", "_")

    try:
        if d in ("top", "home", "start"):
            VK_HOME = 0x24
            KEYEVENTF_KEYUP = 0x0002
            user32.keybd_event(VK_HOME, 0, 0, 0)
            user32.keybd_event(VK_HOME, 0, KEYEVENTF_KEYUP, 0)
            return {"success": True, "action": "top", "message": "Scrolled to top"}

        elif d in ("bottom", "end"):
            VK_END = 0x23
            KEYEVENTF_KEYUP = 0x0002
            user32.keybd_event(VK_END, 0, 0, 0)
            user32.keybd_event(VK_END, 0, KEYEVENTF_KEYUP, 0)
            return {"success": True, "action": "bottom", "message": "Scrolled to bottom"}

        elif d in ("page_down", "pagedown", "next_page"):
            VK_NEXT = 0x22
            KEYEVENTF_KEYUP = 0x0002
            user32.keybd_event(VK_NEXT, 0, 0, 0)
            user32.keybd_event(VK_NEXT, 0, KEYEVENTF_KEYUP, 0)
            return {"success": True, "action": "page_down", "message": "Scrolled one page down"}

        elif d in ("page_up", "pageup", "prev_page"):
            VK_PRIOR = 0x21
            KEYEVENTF_KEYUP = 0x0002
            user32.keybd_event(VK_PRIOR, 0, 0, 0)
            user32.keybd_event(VK_PRIOR, 0, KEYEVENTF_KEYUP, 0)
            return {"success": True, "action": "page_up", "message": "Scrolled one page up"}

        elif d in ("up", "scroll_up"):
            MOUSEEVENTF_WHEEL = 0x0800
            delta = 120 * max(1, amount)
            user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, delta, 0)
            return {"success": True, "action": "up", "amount": amount, "message": "Scrolled up"}

        else:  # default "down"
            MOUSEEVENTF_WHEEL = 0x0800
            delta = -120 * max(1, amount)
            user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, delta, 0)
            return {"success": True, "action": "down", "amount": amount, "message": "Scrolled down"}

    except Exception as e:
        return {"success": False, "error": f"Scroll failed: {str(e)}"}


def get_system_status() -> Dict[str, Any]:
    """
    Gathers real-time telemetry on CPU, RAM, battery, active window, and uptime.
    """
    status: Dict[str, Any] = {}

    if psutil:
        try:
            status["cpu_percent"] = psutil.cpu_percent(interval=0.1)
            mem = psutil.virtual_memory()
            status["memory"] = {
                "total_gb": round(mem.total / (1024**3), 1),
                "used_gb": round(mem.used / (1024**3), 1),
                "percent": mem.percent,
            }
            battery = psutil.sensors_battery()
            if battery:
                status["battery"] = {
                    "percent": battery.percent,
                    "power_plugged": battery.power_plugged,
                }
            disk = psutil.disk_usage("C:\\")
            status["disk_c"] = {
                "free_gb": round(disk.free / (1024**3), 1),
                "percent_used": disk.percent,
            }
        except Exception as e:
            status["psutil_error"] = str(e)

    if gw:
        try:
            active_window = gw.getActiveWindow()
            if active_window:
                status["active_window"] = active_window.title
        except Exception:
            pass

    return {"success": True, "status": status}


def volume_control(action: str, level: Optional[int] = None) -> Dict[str, Any]:
    """
    Adjusts or toggles Windows volume.
    Actions: 'mute', 'unmute', 'up', 'down', 'set' (0-100)
    """
    from pc_client.tools.audio_tools import set_volume

    action = (action or "").lower().strip()
    if action in ("volume_up", "increase"):
        action = "up"
    elif action in ("volume_down", "decrease"):
        action = "down"
    try:
        return set_volume(action, level)
    except Exception as e:
        return {"success": False, "error": str(e)}


def file_operations(action: str, path: str, content: Optional[str] = None) -> Dict[str, Any]:
    """
    Performs safe local filesystem inspection:
      - 'read': reads text file
      - 'write': writes or creates text file
      - 'list': lists directory contents
      - 'exists': checks if file/directory exists
    """
    action = action.lower().strip()
    target_path = os.path.expandvars(os.path.expanduser(path))

    try:
        if action == "exists":
            return {"success": True, "path": target_path, "exists": os.path.exists(target_path)}

        elif action == "read":
            if not os.path.isfile(target_path):
                return {"success": False, "error": f"File does not exist: {target_path}"}
            with open(target_path, "r", encoding="utf-8", errors="replace") as f:
                data = f.read(4000)
            return {"success": True, "path": target_path, "content": data}

        elif action == "write":
            if content is None:
                return {"success": False, "error": "Content is required for write action"}
            os.makedirs(os.path.dirname(os.path.abspath(target_path)), exist_ok=True)
            with open(target_path, "w", encoding="utf-8") as f:
                f.write(content)
            return {"success": True, "path": target_path, "message": "File written successfully"}

        elif action == "list":
            if not os.path.isdir(target_path):
                return {"success": False, "error": f"Directory does not exist: {target_path}"}
            items = os.listdir(target_path)[:50]
            return {"success": True, "path": target_path, "items": items}

        else:
            return {"success": False, "error": f"Unknown file action: {action}"}

    except Exception as e:
        return {"success": False, "error": str(e)}
