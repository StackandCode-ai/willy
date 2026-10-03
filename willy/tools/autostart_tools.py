"""
Windows Auto-Start Manager for Willy.
Enables, disables, and checks automatic startup with Windows (on boot/logon)
via the Windows Registry (HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run).
"""

import os
import sys
import winreg
from pathlib import Path
from typing import Dict, Any, Optional

REG_SUBKEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_REG_NAME = "WillyVoiceAssistant"


def get_project_root() -> Path:
    """Returns the absolute path to the project root directory."""
    return Path(__file__).resolve().parent.parent.parent


def get_pythonw_executable() -> str:
    """
    Returns path to pythonw.exe (windowless python runner) if available,
    otherwise falls back to sys.executable.
    """
    python_dir = Path(sys.executable).parent
    pythonw = python_dir / "pythonw.exe"
    if pythonw.exists():
        return str(pythonw)
    return sys.executable


def get_autostart_command(mode: str = "ui") -> str:
    """Constructs the launch command string for Windows Startup."""
    python_exe = get_pythonw_executable()
    project_root = get_project_root()
    main_script = project_root / "main.py"
    return f'"{python_exe}" "{main_script}" --mode {mode}'


def get_autostart_status() -> Dict[str, Any]:
    """
    Checks if Willy is configured to start automatically on Windows logon.
    """
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_SUBKEY, 0, winreg.KEY_READ) as key:
            try:
                cmd_value, _ = winreg.QueryValueEx(key, APP_REG_NAME)
                mode = "ui" if "--mode ui" in cmd_value else ("voice" if "--mode voice" in cmd_value else "unknown")
                return {
                    "enabled": True,
                    "command": cmd_value,
                    "mode": mode,
                    "registry_key": rf"HKCU\{REG_SUBKEY}\{APP_REG_NAME}",
                    "message": f"Willy is configured to start automatically with Windows in '{mode}' mode.",
                }
            except FileNotFoundError:
                return {
                    "enabled": False,
                    "command": None,
                    "mode": None,
                    "registry_key": rf"HKCU\{REG_SUBKEY}\{APP_REG_NAME}",
                    "message": "Willy is not configured to start automatically.",
                }
    except Exception as e:
        return {
            "enabled": False,
            "error": f"Failed to check autostart status: {str(e)}",
            "message": f"Could not inspect registry: {e}",
        }


def enable_autostart(mode: str = "ui") -> Dict[str, Any]:
    """
    Registers Willy in HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run.
    Uses pythonw.exe to run smoothly without leaving a blank terminal window open.
    """
    if mode not in ("ui", "voice", "text", "push-to-talk"):
        mode = "ui"

    command = get_autostart_command(mode=mode)

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            REG_SUBKEY,
            0,
            winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE
        ) as key:
            winreg.SetValueEx(key, APP_REG_NAME, 0, winreg.REG_SZ, command)

        return {
            "success": True,
            "enabled": True,
            "mode": mode,
            "command": command,
            "registry_key": rf"HKCU\{REG_SUBKEY}\{APP_REG_NAME}",
            "message": f"Successfully enabled Willy auto-start on Windows boot (mode: {mode}). Willy will now start on its own whenever you log in.",
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to enable auto-start in Windows registry: {str(e)}",
        }


def disable_autostart() -> Dict[str, Any]:
    """
    Removes Willy from HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run.
    """
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            REG_SUBKEY,
            0,
            winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE
        ) as key:
            try:
                winreg.DeleteValue(key, APP_REG_NAME)
                deleted = True
            except FileNotFoundError:
                deleted = False

        # Also check and clean up any legacy shortcut in Startup folder if it exists
        startup_dir = Path(os.environ.get("APPDATA", "")) / r"Microsoft\Windows\Start Menu\Programs\Startup"
        shortcut = startup_dir / "WillyVoiceAssistant.lnk"
        if shortcut.exists():
            try:
                shortcut.unlink()
            except Exception:
                pass

        return {
            "success": True,
            "enabled": False,
            "message": "Willy auto-start has been disabled." if deleted else "Willy auto-start was already disabled.",
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to disable auto-start: {str(e)}",
        }


def manage_autostart(action: str = "status", mode: Optional[str] = "ui") -> Dict[str, Any]:
    """
    Unified entry point for managing Willy's Windows startup behavior.
    Actions:
      - 'enable': Register Willy to start automatically when Windows boots.
      - 'disable': Remove Willy from Windows startup.
      - 'status': Check if auto-start is currently enabled.
    """
    action_clean = (action or "status").lower().strip()
    if action_clean in ("enable", "on", "start", "register", "install", "turn_on"):
        return enable_autostart(mode=mode or "ui")
    elif action_clean in ("disable", "off", "stop", "unregister", "uninstall", "turn_off", "remove"):
        return disable_autostart()
    elif action_clean in ("status", "check", "info", "get"):
        return get_autostart_status()
    else:
        return {
            "success": False,
            "error": f"Unknown autostart action '{action}'. Valid actions are: 'enable', 'disable', 'status'.",
        }
