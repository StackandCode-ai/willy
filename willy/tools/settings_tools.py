"""
Windows System Settings and Configuration Tools for Willy.
Allows Willy to touch, inspect, and modify Windows settings directly.
"""

import os
import winreg
import subprocess
from typing import Dict, Any, Optional

SETTINGS_PAGES = {
    "system": "ms-settings:about",
    "about": "ms-settings:about",
    "display": "ms-settings:display",
    "screen": "ms-settings:display",
    "sound": "ms-settings:sound",
    "audio": "ms-settings:sound",
    "volume": "ms-settings:sound",
    "notifications": "ms-settings:notifications",
    "power": "ms-settings:powersleep",
    "battery": "ms-settings:powersleep",
    "sleep": "ms-settings:powersleep",
    "storage": "ms-settings:storagesense",
    "bluetooth": "ms-settings:bluetooth",
    "devices": "ms-settings:bluetooth",
    "network": "ms-settings:network-status",
    "wifi": "ms-settings:network-wifi",
    "wi-fi": "ms-settings:network-wifi",
    "ethernet": "ms-settings:network-ethernet",
    "personalization": "ms-settings:personalization",
    "background": "ms-settings:personalization-background",
    "colors": "ms-settings:personalization-colors",
    "theme": "ms-settings:themes",
    "lockscreen": "ms-settings:lockscreen",
    "taskbar": "ms-settings:taskbar",
    "apps": "ms-settings:appsfeatures",
    "installed apps": "ms-settings:appsfeatures",
    "default apps": "ms-settings:defaultapps",
    "startup": "ms-settings:startupapps",
    "time": "ms-settings:dateandtime",
    "date": "ms-settings:dateandtime",
    "language": "ms-settings:regionlanguage",
    "gaming": "ms-settings:gaming-gamebar",
    "accessibility": "ms-settings:easeofaccess-display",
    "privacy": "ms-settings:privacy",
    "update": "ms-settings:windowsupdate",
    "windows update": "ms-settings:windowsupdate",
}


def open_settings_page(page: str = "system") -> Dict[str, Any]:
    """
    Opens a specific page in Windows 10/11 Settings.
    """
    page_clean = page.lower().strip()
    uri = SETTINGS_PAGES.get(page_clean, f"ms-settings:{page_clean}")
    try:
        os.startfile(uri)
        return {
            "success": True,
            "page": page_clean,
            "uri": uri,
            "message": f"Opened Windows Settings '{page_clean}'",
        }
    except Exception as e:
        return {"success": False, "error": f"Failed to open Settings page '{page}': {str(e)}"}


def set_windows_theme(mode: str) -> Dict[str, Any]:
    """
    Switches Windows theme between 'dark' and 'light' mode instantly via Registry.
    """
    mode_clean = mode.lower().strip()
    val = 0 if mode_clean in ("dark", "night", "black") else 1

    try:
        key_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, "AppsUseLightTheme", 0, winreg.REG_DWORD, val)
            winreg.SetValueEx(k, "SystemUsesLightTheme", 0, winreg.REG_DWORD, val)

        applied_mode = "Dark Mode" if val == 0 else "Light Mode"
        return {
            "success": True,
            "mode": applied_mode,
            "message": f"Successfully switched Windows to {applied_mode}",
        }
    except Exception as e:
        return {"success": False, "error": f"Failed to change Windows theme: {str(e)}"}


def manage_windows_settings(
    action: str,
    page: Optional[str] = None,
    value: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Unified controller for Windows settings.
    Actions:
      - 'open': Opens a settings page (e.g. 'sound', 'wifi', 'display', 'bluetooth', 'update', 'apps')
      - 'theme': Sets system theme to 'dark' or 'light'
      - 'list_pages': Returns all available settings page names
    """
    action_clean = action.lower().strip()

    if action_clean == "open":
        return open_settings_page(page or "system")
    elif action_clean == "theme":
        return set_windows_theme(value or "dark")
    elif action_clean == "list_pages":
        return {"success": True, "available_pages": sorted(list(set(SETTINGS_PAGES.keys())))}
    elif action_clean in ("autostart", "startup"):
        from willy.tools.autostart_tools import manage_autostart
        return manage_autostart(action=value or "status")
    else:
        return {"success": False, "error": f"Unknown settings action: {action}"}
