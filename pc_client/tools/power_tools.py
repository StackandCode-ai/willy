"""
Windows Power, Session, and Lock/Unlock Control Tools for Willy.
Works 100% locally without internet.
"""

import os
import sys
import time
import ctypes
import subprocess
from typing import Dict, Any, Optional

try:
    import pyautogui
except ImportError:
    pyautogui = None


def lock_workstation() -> Dict[str, Any]:
    """
    Locks the Windows desktop workstation immediately.
    """
    try:
        ctypes.windll.user32.LockWorkStation()
        return {
            "success": True,
            "action": "lock",
            "message": "Workstation locked successfully.",
        }
    except Exception as e:
        return {"success": False, "error": f"Failed to lock workstation: {str(e)}"}


def unlock_or_wake(pin: Optional[str] = None) -> Dict[str, Any]:
    """
    Wakes up the screen from sleep, dismisses lock screen, and optionally enters PIN.
    Uses native Windows Win32 API to avoid any PyAutoGUI corner fail-safe crashes.
    """
    try:
        user32 = ctypes.windll.user32

        # 1. Wake display via native Win32 mouse relative movement (MOUSEEVENTF_MOVE = 0x0001)
        user32.mouse_event(0x0001, 2, 2, 0, 0)
        time.sleep(0.05)
        user32.mouse_event(0x0001, -2, -2, 0, 0)
        time.sleep(0.15)

        # 2. Dismiss lock screen wallpaper / bring up PIN prompt with Space key
        # VK_SPACE = 0x20, KEYEVENTF_KEYUP = 0x0002
        user32.keybd_event(0x20, 0, 0, 0)
        time.sleep(0.05)
        user32.keybd_event(0x20, 0, 0x0002, 0)
        time.sleep(0.2)

        # 3. Enter key to focus PIN / password box
        # VK_RETURN = 0x0D
        user32.keybd_event(0x0D, 0, 0, 0)
        time.sleep(0.05)
        user32.keybd_event(0x0D, 0, 0x0002, 0)
        time.sleep(0.2)

        # 4. Optional PIN entry
        if pin:
            time.sleep(0.2)
            if pyautogui:
                old_fs = getattr(pyautogui, "FAILSAFE", True)
                try:
                    pyautogui.FAILSAFE = False
                    pyautogui.typewrite(str(pin).strip(), interval=0.04)
                    pyautogui.press("enter")
                finally:
                    pyautogui.FAILSAFE = old_fs
            return {
                "success": True,
                "action": "unlock",
                "message": f"Woke screen and entered PIN {pin}.",
            }

        return {
            "success": True,
            "action": "wake",
            "message": "Screen awakened and lock screen dismissed.",
        }
    except Exception as e:
        return {"success": False, "error": f"Failed to wake/unlock: {str(e)}"}


def shutdown_system(delay_sec: int = 30) -> Dict[str, Any]:
    """
    Initiates a scheduled Windows system shutdown.
    Gives user time to cancel if needed.
    """
    try:
        cmd = f'shutdown /s /t {delay_sec} /c "Shutdown initiated by Willy"'
        subprocess.run(cmd, shell=True, check=True)
        return {
            "success": True,
            "action": "shutdown",
            "delay_sec": delay_sec,
            "message": f"Computer will shut down in {delay_sec} seconds. Say 'cancel shutdown' to abort.",
        }
    except Exception as e:
        return {"success": False, "error": f"Failed to initiate shutdown: {str(e)}"}


def restart_system(delay_sec: int = 30) -> Dict[str, Any]:
    """
    Initiates a scheduled Windows system reboot.
    """
    try:
        cmd = f'shutdown /r /t {delay_sec} /c "Restart initiated by Willy"'
        subprocess.run(cmd, shell=True, check=True)
        return {
            "success": True,
            "action": "restart",
            "delay_sec": delay_sec,
            "message": f"Computer will restart in {delay_sec} seconds.",
        }
    except Exception as e:
        return {"success": False, "error": f"Failed to initiate restart: {str(e)}"}


def sleep_system() -> Dict[str, Any]:
    """
    Puts the computer into sleep/suspend mode.
    """
    try:
        cmd = "rundll32.exe powrprof.dll,SetSuspendState 0,1,0"
        subprocess.Popen(cmd, shell=True)
        return {
            "success": True,
            "action": "sleep",
            "message": "Putting computer to sleep.",
        }
    except Exception as e:
        return {"success": False, "error": f"Failed to sleep computer: {str(e)}"}


def cancel_shutdown() -> Dict[str, Any]:
    """
    Aborts a pending scheduled shutdown or restart.
    """
    try:
        res = subprocess.run("shutdown /a", shell=True, capture_output=True, text=True)
        if res.returncode == 0:
            return {
                "success": True,
                "action": "cancel_shutdown",
                "message": "Scheduled shutdown or restart has been cancelled.",
            }
        else:
            return {
                "success": False,
                "message": "No pending shutdown was scheduled to cancel.",
            }
    except Exception as e:
        return {"success": False, "error": f"Failed to cancel shutdown: {str(e)}"}
