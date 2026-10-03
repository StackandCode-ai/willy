"""
Window Management Tools for Willy.
Provides reliable foreground switching, window restoration, and window enumeration.
Works 100% locally using Win32 API, PyGetWindow, and WScript.Shell fallbacks.
"""

import time
import subprocess
from typing import Dict, Any, List, Optional
import ctypes

try:
    import win32gui
    import win32process
    import win32api
    import win32con
except ImportError:
    win32gui = None
    win32process = None
    win32api = None
    win32con = None

try:
    import pygetwindow as gw
except ImportError:
    gw = None

try:
    import psutil
except ImportError:
    psutil = None


# Common application process and title mappings
APP_PROCESS_ALIASES = {
    "chrome": ["chrome.exe", "chrome", "google chrome"],
    "google chrome": ["chrome.exe", "chrome", "google chrome"],
    "edge": ["msedge.exe", "msedge", "edge", "microsoft edge"],
    "microsoft edge": ["msedge.exe", "msedge", "edge", "microsoft edge"],
    "browser": ["chrome.exe", "msedge.exe", "chrome", "edge"],
    "code": ["code.exe", "code", "visual studio code"],
    "vscode": ["code.exe", "code", "visual studio code"],
    "camera": ["windowscamera.exe", "camera"],
    "notepad": ["notepad.exe", "notepad"],
    "spotify": ["spotify.exe", "spotify"],
    "calculator": ["calculatorapp.exe", "calculator.exe", "calc.exe", "calculator"],
    "calc": ["calculatorapp.exe", "calculator.exe", "calc.exe", "calculator"],
    "terminal": ["windowsterminal.exe", "wt.exe", "cmd.exe", "powershell.exe", "terminal"],
    "cmd": ["cmd.exe", "command prompt"],
    "powershell": ["powershell.exe", "powershell"],
    "explorer": ["explorer.exe", "file explorer"],
    "files": ["explorer.exe", "file explorer"],
    "discord": ["discord.exe", "discord"],
    "telegram": ["telegram.exe", "telegram"],
}


def _activate_via_wscript(target_title_or_pid) -> bool:
    """Activates window via WScript.Shell COM object."""
    try:
        import win32com.client
        shell = win32com.client.Dispatch("WScript.Shell")
        return bool(shell.AppActivate(target_title_or_pid))
    except Exception:
        return False


def _bring_hwnd_to_foreground(hwnd: int) -> bool:
    """
    Brings a window to the active foreground on Windows.
    Bypasses Windows foreground lock restrictions using thread input attachment
    and SwitchToThisWindow.
    """
    if not win32gui:
        return False

    try:
        # 1. Unminimize / restore window if minimized
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        else:
            win32gui.ShowWindow(hwnd, win32con.SW_SHOW)

        # 2. SwitchToThisWindow directly prompts OS to switch focus
        ctypes.windll.user32.SwitchToThisWindow(hwnd, True)

        # 3. Bypass foreground lock via thread input attachment
        fg_hwnd = win32gui.GetForegroundWindow()
        if fg_hwnd != hwnd:
            fg_tid, _ = win32process.GetWindowThreadProcessId(fg_hwnd)
            cur_tid = win32api.GetCurrentThreadId()
            target_tid, _ = win32process.GetWindowThreadProcessId(hwnd)

            if fg_tid != cur_tid:
                win32process.AttachThreadInput(cur_tid, fg_tid, True)
            if target_tid != cur_tid:
                win32process.AttachThreadInput(cur_tid, target_tid, True)

            win32gui.SetForegroundWindow(hwnd)
            win32gui.BringWindowToTop(hwnd)

            if target_tid != cur_tid:
                win32process.AttachThreadInput(cur_tid, target_tid, False)
            if fg_tid != cur_tid:
                win32process.AttachThreadInput(cur_tid, fg_tid, False)

        return True
    except Exception:
        # Fallback: Alt key press to gain foreground activation rights
        try:
            ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)  # Alt key down
            win32gui.SetForegroundWindow(hwnd)
            win32gui.BringWindowToTop(hwnd)
            ctypes.windll.user32.keybd_event(0x12, 0, 2, 0)  # Alt key up
            return True
        except Exception:
            return False


def list_open_windows() -> Dict[str, Any]:
    """
    Returns all visible top-level application windows with titles, process names, and states.
    Uses Win32 EnumWindows with PyGetWindow and PowerShell fallbacks.
    """
    windows: List[Dict[str, Any]] = []

    # 1. Try Win32 EnumWindows
    if win32gui:
        def enum_windows_proc(hwnd, _):
            try:
                if not win32gui.IsWindowVisible(hwnd):
                    return True
                title = win32gui.GetWindowText(hwnd).strip()
                if not title:
                    return True
                if title in ("Program Manager", "Default IME", "MSCTFIME UI"):
                    return True

                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                p_name = ""
                if psutil and pid:
                    try:
                        p_name = psutil.Process(pid).name()
                    except Exception:
                        p_name = ""

                is_minimized = bool(win32gui.IsIconic(hwnd))
                fg_hwnd = win32gui.GetForegroundWindow()
                is_active = (hwnd == fg_hwnd)

                windows.append({
                    "hwnd": hwnd,
                    "title": title,
                    "process": p_name,
                    "pid": pid,
                    "is_minimized": is_minimized,
                    "is_active": is_active,
                })
            except Exception:
                pass
            return True

        try:
            win32gui.EnumWindows(enum_windows_proc, None)
        except Exception:
            pass

    # 2. PyGetWindow fallback if EnumWindows didn't return any
    if not windows and gw:
        try:
            for w in gw.getAllWindows():
                title = w.title.strip()
                if title and title not in ("Program Manager", "Default IME", "MSCTFIME UI"):
                    windows.append({
                        "hwnd": getattr(w, "_hWnd", 0),
                        "title": title,
                        "process": "",
                        "pid": 0,
                        "is_minimized": getattr(w, "isMinimized", False),
                        "is_active": getattr(w, "isActive", False),
                    })
        except Exception:
            pass

    # 3. psutil fallback to discover running UI processes
    if not windows and psutil:
        try:
            for p in psutil.process_iter(['pid', 'name']):
                p_name = p.info['name']
                for alias, procs in APP_PROCESS_ALIASES.items():
                    if any(proc.lower() == p_name.lower() for proc in procs):
                        windows.append({
                            "hwnd": 0,
                            "title": p_name,
                            "process": p_name,
                            "pid": p.info['pid'],
                            "is_minimized": False,
                            "is_active": False,
                        })
                        break
        except Exception:
            pass

    return {
        "success": True,
        "count": len(windows),
        "windows": windows,
    }


def focus_window(target: str) -> Dict[str, Any]:
    """
    Finds a running window by application name (e.g. 'chrome', 'edge', 'code', 'camera')
    or partial window title (e.g. 'StackandCode', 'YouTube'), restores it if minimized,
    and brings it to the active foreground.
    """
    clean_target = target.lower().strip()
    if not clean_target:
        return {"success": False, "error": "Target window name or title cannot be empty"}

    # 1. Check open windows list
    win_data = list_open_windows()
    windows = win_data.get("windows", [])

    matched_win = None
    target_processes = APP_PROCESS_ALIASES.get(clean_target, [f"{clean_target}.exe", clean_target])

    # Search for match in process name or title
    for w in windows:
        p_name = w.get("process", "").lower()
        title = w.get("title", "").lower()
        if any(tp.lower() in p_name for tp in target_processes) or clean_target in title:
            matched_win = w
            break

    if not matched_win:
        for w in windows:
            title = w.get("title", "").lower()
            if any(part in title for part in clean_target.split()):
                matched_win = w
                break

    # If matched with a valid HWND, use Win32 activation
    if matched_win and matched_win.get("hwnd"):
        hwnd = matched_win["hwnd"]
        if _bring_hwnd_to_foreground(hwnd):
            time.sleep(0.15)
            return {
                "success": True,
                "target": target,
                "title": matched_win["title"],
                "process": matched_win["process"],
                "message": f"Brought '{matched_win['title']}' to the foreground.",
            }

    # 2. Try WScript.Shell AppActivate (works by title or process name)
    if _activate_via_wscript(clean_target):
        return {
            "success": True,
            "target": target,
            "message": f"Activated window for '{target}' via WScript.Shell.",
        }

    # Also try alias names via WScript.Shell
    for alias in target_processes:
        if _activate_via_wscript(alias):
            return {
                "success": True,
                "target": target,
                "message": f"Activated window for '{target}' via WScript.Shell.",
            }

    # 3. Try PyGetWindow activation
    if gw:
        try:
            gw_matches = gw.getWindowsWithTitle(clean_target)
            if not gw_matches:
                # Try partial words
                for part in clean_target.split():
                    gw_matches = gw.getWindowsWithTitle(part)
                    if gw_matches:
                        break
            if gw_matches:
                target_win = gw_matches[0]
                if getattr(target_win, "isMinimized", False):
                    target_win.restore()
                target_win.activate()
                return {
                    "success": True,
                    "target": target,
                    "title": target_win.title,
                    "message": f"Brought '{target_win.title}' to the foreground.",
                }
        except Exception:
            pass

    # 4. PowerShell AppActivate fallback
    try:
        ps_cmd = (
            f"$w = New-Object -ComObject WScript.Shell; "
            f"if ($w.AppActivate('{clean_target}')) {{ exit 0 }} else {{ exit 1 }}"
        )
        res = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", ps_cmd],
            capture_output=True,
            timeout=4,
        )
        if res.returncode == 0:
            return {
                "success": True,
                "target": target,
                "message": f"Activated '{target}' via PowerShell.",
            }
    except Exception:
        pass

    # 5. If process is running but has no active window, try launching/opening it
    sample_titles = [w.get("title", "") for w in windows[:6] if w.get("title")]
    return {
        "success": False,
        "target": target,
        "error": f"Could not bring '{target}' to the foreground. Is the application running?",
        "open_windows_sample": sample_titles,
    }


def minimize_window(target: Optional[str] = None) -> Dict[str, Any]:
    """
    Minimizes a specific window by name/title, or minimizes the currently active window if target is None.
    """
    if not target:
        if win32gui:
            hwnd = win32gui.GetForegroundWindow()
            if hwnd:
                title = win32gui.GetWindowText(hwnd).strip()
                win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
                return {"success": True, "message": f"Minimized active window '{title}'."}
        return {"success": False, "error": "No active window found to minimize"}

    # Find matching window
    clean_target = target.lower().strip()
    win_data = list_open_windows()
    windows = win_data.get("windows", [])

    matched_win = None
    target_processes = APP_PROCESS_ALIASES.get(clean_target, [f"{clean_target}.exe", clean_target])

    for w in windows:
        p_name = w.get("process", "").lower()
        title = w.get("title", "").lower()
        if any(tp.lower() in p_name for tp in target_processes) or clean_target in title:
            matched_win = w
            break

    if matched_win and matched_win.get("hwnd") and win32gui:
        win32gui.ShowWindow(matched_win["hwnd"], win32con.SW_MINIMIZE)
        return {"success": True, "message": f"Minimized window '{matched_win['title']}'."}

    return {"success": False, "error": f"Could not find open window for '{target}'."}
