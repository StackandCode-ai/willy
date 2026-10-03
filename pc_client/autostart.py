"""
Start Willy PC when you sign in to Windows.

Uses the per-user Run key (HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run): no admin
rights needed, and the entry shows up in Task Manager > Startup apps as "WillyPC". It starts
Willy hidden in the notification area (--tray): the packaged WillyPC.exe when running from a
build, otherwise pythonw.exe with pc_client/main.py (no console window).
"""

import sys
import winreg
from pathlib import Path
from typing import Any, Dict, Optional

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
# Task Manager / Settings > Startup apps record "switched off" here without touching Run.
APPROVED_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
VALUE_NAME = "WillyPC"
# The old standalone assistant (python main.py --mode ui). It would start a second Willy with
# its own microphone listener, so enabling this entry replaces it.
LEGACY_VALUE_NAME = "WillyVoiceAssistant"
TRAY_ARG = "--tray"


def command_for(exe: Optional[Path] = None) -> str:
    """Sign-in command for a packaged WillyPC.exe, or (exe=None) for this Python and source tree."""
    if exe is not None:
        return f'"{Path(exe).resolve()}" {TRAY_ARG}'
    python = Path(sys.executable).resolve()
    pythonw = python.with_name("pythonw.exe")
    main = Path(__file__).resolve().parent / "main.py"
    return f'"{pythonw if pythonw.exists() else python}" "{main}" {TRAY_ARG}'


def launch_command() -> str:
    """Sign-in command for the Willy that is running now."""
    return command_for(Path(sys.executable) if getattr(sys, "frozen", False) else None)


def _read(name: str) -> Optional[str]:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, name)
            return str(value)
    except OSError:
        return None


def _switched_off(name: str) -> bool:
    """True when the entry was switched off in Task Manager / Settings > Startup apps."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, APPROVED_KEY) as key:
            data, _kind = winreg.QueryValueEx(key, name)
    except OSError:
        return False
    return isinstance(data, (bytes, bytearray)) and len(data) > 0 and bool(data[0] & 1)


def _delete(subkey: str, name: str) -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, name)
        return True
    except OSError:
        return False


def status() -> Dict[str, Any]:
    command = _read(VALUE_NAME)
    switched_off = bool(command) and _switched_off(VALUE_NAME)
    expected = launch_command()
    return {
        "enabled": bool(command) and not switched_off,
        "command": command,
        "expected_command": expected,
        "up_to_date": command == expected,
        "switched_off_in_startup_apps": switched_off,
        "legacy_command": _read(LEGACY_VALUE_NAME),
    }


def is_enabled() -> bool:
    return bool(_read(VALUE_NAME)) and not _switched_off(VALUE_NAME)


def enable(command: Optional[str] = None, replace_legacy: bool = True) -> Dict[str, Any]:
    """Starts Willy (hidden in the tray) at every sign-in. Replaces the old assistant's entry."""
    command = command or launch_command()
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command)
    except OSError as e:
        return {"success": False, "enabled": False, "error": f"Couldn't write the startup entry: {e}"}
    # A "switched off" mark from Task Manager would silently keep the entry from running.
    _delete(APPROVED_KEY, VALUE_NAME)
    replaced = None
    if replace_legacy:
        legacy = _read(LEGACY_VALUE_NAME)
        if legacy and _delete(RUN_KEY, LEGACY_VALUE_NAME):
            replaced = legacy
    return {"success": True, "enabled": True, "command": command, "replaced_legacy": replaced}


def disable() -> Dict[str, Any]:
    removed = _delete(RUN_KEY, VALUE_NAME)
    _delete(APPROVED_KEY, VALUE_NAME)
    return {"success": True, "enabled": False, "removed": removed}
