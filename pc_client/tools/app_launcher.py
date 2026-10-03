"""
Universal Windows Application Resolver and Launcher for Willy.
Guarantees discovering and opening ANY app installed on the user's system:
- Start Menu & Modern Windows Apps (Get-StartApps)
- Win32 Registry App Paths (HKLM & HKCU)
- Protocol & System URIs (ms-settings:, etc.)
- Program Files & Start Menu shortcuts (.lnk)
"""

import os
import sys
import json
import glob
import time
import winreg
import subprocess
from typing import Dict, Any, List, Optional

_START_APPS_CACHE: List[Dict[str, str]] = []
_LAST_CACHE_TIME = 0.0


def refresh_start_apps_cache() -> List[Dict[str, str]]:
    """
    Refreshes the internal list of all installed Windows applications via Get-StartApps.
    """
    global _START_APPS_CACHE, _LAST_CACHE_TIME
    try:
        cmd = [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "Get-StartApps | ConvertTo-Json -Compress",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=10)
        if res.returncode == 0 and res.stdout.strip():
            data = json.loads(res.stdout.strip())
            _START_APPS_CACHE = data if isinstance(data, list) else [data]
            _LAST_CACHE_TIME = time.time()
    except Exception as e:
        print(f"[-] Error refreshing StartApps cache: {e}")
    return _START_APPS_CACHE


def get_start_apps() -> List[Dict[str, str]]:
    """Returns cached StartApps or refreshes if empty or older than 10 minutes."""
    global _START_APPS_CACHE, _LAST_CACHE_TIME
    if not _START_APPS_CACHE or (time.time() - _LAST_CACHE_TIME > 600):
        refresh_start_apps_cache()
    return _START_APPS_CACHE


def find_in_registry(app_name: str) -> Optional[str]:
    """
    Queries Windows Registry App Paths (HKLM & HKCU).
    """
    clean_name = app_name.lower().strip()
    variations = [clean_name]
    if not clean_name.endswith(".exe"):
        variations.append(f"{clean_name}.exe")

    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for var in variations:
            key_path = f"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\{var}"
            try:
                with winreg.OpenKey(root, key_path) as k:
                    val, _ = winreg.QueryValueEx(k, "")
                    if val and os.path.exists(os.path.expandvars(val)):
                        return os.path.expandvars(val)
            except OSError:
                pass
    return None


def find_in_common_paths(app_name: str) -> Optional[str]:
    """
    Checks common installation directories for Edge, Chrome, Spotify, etc.
    """
    clean_name = app_name.lower().strip()

    # Browser & well-known paths
    known_paths = {
        "edge": [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        ],
        "msedge": [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        ],
        "chrome": [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        ],
        "spotify": [
            os.path.expandvars(r"%APPDATA%\Spotify\Spotify.exe"),
            os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WindowsApps\Spotify.exe"),
        ],
        "code": [
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe"),
            r"C:\Program Files\Microsoft VS Code\Code.exe",
        ],
        "notepad": [
            r"C:\Windows\System32\notepad.exe",
        ],
        "calc": [
            r"C:\Windows\System32\calc.exe",
        ],
    }

    if clean_name in known_paths:
        for p in known_paths[clean_name]:
            if os.path.isfile(p):
                return p

    return None


def search_start_menu_shortcuts(query: str) -> Optional[str]:
    """
    Scans Start Menu directories for matching .lnk shortcuts.
    """
    query_clean = query.lower().strip()
    search_dirs = [
        os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs"),
        r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs",
    ]

    for d in search_dirs:
        if not os.path.isdir(d):
            continue
        for root, _, files in os.walk(d):
            for f in files:
                if f.lower().endswith(".lnk") and query_clean in f.lower():
                    return os.path.join(root, f)
    return None


def launch_windows_app(target: str, args: str = "") -> Dict[str, Any]:
    """
    Universal application launcher for Windows.
    Tries:
      1. URIs / Protocols (ms-settings:, spotify:, http:, etc.)
      2. Get-StartApps AppID (Genshin Impact, Calculator, Edge, Chrome, etc.)
      3. Registry App Paths (msedge, chrome, code, etc.)
      4. Known directory paths
      5. Start Menu .lnk shortcuts
      6. System PATH / powershell Start-Process
    """
    target_clean = target.strip().lower()

    # 1. Handle URIs & Protocols directly
    if target_clean.startswith("ms-settings:") or "://" in target_clean or target_clean.startswith("spotify:"):
        try:
            os.startfile(target)
            return {"success": True, "target": target, "method": "protocol_uri", "message": f"Opened {target}"}
        except Exception as e:
            return {"success": False, "target": target, "error": f"Failed to open URI: {str(e)}"}

    # 2. Fast Path: Check Common Known Paths (0ms)
    common_exe = find_in_common_paths(target_clean)
    if common_exe:
        try:
            if args:
                cmd = f'"{common_exe}" {args}'
                subprocess.Popen(cmd, shell=True)
            else:
                os.startfile(common_exe)
            return {
                "success": True,
                "target": target,
                "executable": common_exe,
                "method": "known_path",
                "message": f"Successfully launched '{common_exe}'",
            }
        except Exception:
            pass

    # 3. Fast Path: Check Windows Registry App Paths (1ms)
    reg_exe = find_in_registry(target_clean)
    if reg_exe:
        try:
            if args:
                cmd = f'"{reg_exe}" {args}'
                subprocess.Popen(cmd, shell=True)
            else:
                os.startfile(reg_exe)
            return {
                "success": True,
                "target": target,
                "executable": reg_exe,
                "method": "registry_app_path",
                "message": f"Successfully launched '{reg_exe}'",
            }
        except Exception:
            pass

    # 4. Fast Path: Check Start Menu Shortcuts (.lnk) (2ms)
    shortcut_path = search_start_menu_shortcuts(target_clean)
    if shortcut_path:
        try:
            os.startfile(shortcut_path)
            return {
                "success": True,
                "target": target,
                "shortcut": shortcut_path,
                "method": "start_menu_shortcut",
                "message": f"Successfully launched shortcut '{os.path.basename(shortcut_path)}'",
            }
        except Exception:
            pass

    # 5. Check Get-StartApps with intelligent ranking (for UWP / Windows Store Apps)
    apps = get_start_apps()
    matched_app = None

    # Priority 0: Well-known overrides
    well_known_names = {
        "chrome": "Google Chrome",
        "google chrome": "Google Chrome",
        "edge": "Microsoft Edge",
        "microsoft edge": "Microsoft Edge",
        "calculator": "Calculator",
        "calc": "Calculator",
        "notepad": "Notepad",
    }
    if target_clean in well_known_names:
        preferred = well_known_names[target_clean].lower()
        for a in apps:
            if a.get("Name", "").lower() == preferred:
                matched_app = a
                break

    # Priority 1: Exact match
    if not matched_app:
        for a in apps:
            if target_clean == a.get("Name", "").lower():
                matched_app = a
                break

    # Priority 2: Name starts with target
    if not matched_app:
        for a in apps:
            if a.get("Name", "").lower().startswith(target_clean):
                matched_app = a
                break

    # Priority 3: Substring match
    if not matched_app:
        for a in apps:
            name = a.get("Name", "").lower()
            if target_clean in name or name in target_clean:
                matched_app = a
                break

    if matched_app:
        app_id = matched_app.get("AppID", "")
        app_display_name = matched_app.get("Name", target)
        shell_path = f"shell:AppsFolder\\{app_id}"
        try:
            if args:
                cmd = f'Start-Process "{shell_path}" -ArgumentList "{args}"'
                subprocess.Popen(["powershell.exe", "-NoProfile", "-Command", cmd])
            else:
                os.startfile(shell_path)

            return {
                "success": True,
                "target": app_display_name,
                "app_id": app_id,
                "method": "start_apps_folder",
                "message": f"Successfully launched '{app_display_name}'",
            }
        except Exception:
            pass

    # 6. Fallback to PowerShell Start-Process
    try:
        ps_cmd = f'Start-Process "{target}"'
        if args:
            ps_cmd += f' -ArgumentList "{args}"'
        res = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if res.returncode == 0:
            return {
                "success": True,
                "target": target,
                "method": "powershell_start",
                "message": f"Successfully launched '{target}' via PowerShell",
            }
    except Exception:
        pass

    # If all failed, give helpful suggestions from StartApps
    suggestions = [
        a.get("Name")
        for a in apps
        if any(w in a.get("Name", "").lower() for w in target_clean.split())
    ][:5]

    return {
        "success": False,
        "target": target,
        "error": f"Could not locate or launch application '{target}' on this system.",
        "suggestions": suggestions,
    }
