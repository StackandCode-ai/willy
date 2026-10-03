"""
Browser and Profile Management for Willy.
Directly inspects and launches Microsoft Edge and Google Chrome profiles.
"""

import os
import json
import subprocess
from typing import Dict, Any, List, Optional

CHROME_LOCAL_STATE = os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data\Local State")
EDGE_LOCAL_STATE = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\User Data\Local State")

CHROME_EXE = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
if not os.path.exists(CHROME_EXE):
    CHROME_EXE = os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe")

EDGE_EXE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
if not os.path.exists(EDGE_EXE):
    EDGE_EXE = r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"


def list_browser_profiles(browser: str = "all") -> Dict[str, Any]:
    """
    Discovers all profiles in Microsoft Edge and Google Chrome from Local State.
    """
    browser_clean = browser.lower().strip()
    result: Dict[str, Any] = {"edge": [], "chrome": []}

    # Inspect Edge
    if browser_clean in ("all", "edge", "msedge") and os.path.exists(EDGE_LOCAL_STATE):
        try:
            with open(EDGE_LOCAL_STATE, "r", encoding="utf-8", errors="replace") as f:
                data = json.load(f)
            cache = data.get("profile", {}).get("info_cache", {})
            for dir_name, info in cache.items():
                result["edge"].append({
                    "profile_dir": dir_name,
                    "name": info.get("name", dir_name),
                    "user_name": info.get("user_name", ""),
                })
        except Exception as e:
            result["edge_error"] = str(e)

    # Inspect Chrome
    if browser_clean in ("all", "chrome", "google chrome") and os.path.exists(CHROME_LOCAL_STATE):
        try:
            with open(CHROME_LOCAL_STATE, "r", encoding="utf-8", errors="replace") as f:
                data = json.load(f)
            cache = data.get("profile", {}).get("info_cache", {})
            for dir_name, info in cache.items():
                result["chrome"].append({
                    "profile_dir": dir_name,
                    "name": info.get("name", dir_name),
                    "user_name": info.get("user_name", ""),
                })
        except Exception as e:
            result["chrome_error"] = str(e)

    return {"success": True, "profiles": result}


def launch_browser_with_profile(
    browser: str = "edge",
    profile_query: Optional[str] = None,
    url: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Launches Edge or Chrome with a specific user profile and optional URL.
    """
    browser_clean = browser.lower().strip()
    is_edge = "edge" in browser_clean
    exe_path = EDGE_EXE if is_edge else CHROME_EXE
    browser_name = "Edge" if is_edge else "Chrome"

    profiles_data = list_browser_profiles("edge" if is_edge else "chrome")
    target_list = profiles_data.get("profiles", {}).get("edge" if is_edge else "chrome", [])

    matched_profile_dir = None
    matched_profile_name = None

    if profile_query:
        pq = profile_query.lower().strip()
        # 1. Exact match on directory, name, or username/email
        for p in target_list:
            if pq in (p["profile_dir"].lower(), p["name"].lower(), p["user_name"].lower()):
                matched_profile_dir = p["profile_dir"]
                matched_profile_name = f"{p['name']} ({p['user_name']})" if p['user_name'] else p['name']
                break

        # 2. Substring match
        if not matched_profile_dir:
            for p in target_list:
                combined = f"{p['profile_dir']} {p['name']} {p['user_name']}".lower()
                if pq in combined or combined in pq:
                    matched_profile_dir = p["profile_dir"]
                    matched_profile_name = f"{p['name']} ({p['user_name']})" if p['user_name'] else p['name']
                    break

    # Build command line
    cmd = [exe_path]
    if matched_profile_dir:
        cmd.append(f'--profile-directory={matched_profile_dir}')
    if url:
        url_clean = url.strip()
        if not url_clean.startswith(("http://", "https://")):
            url_clean = "https://" + url_clean
        cmd.append(url_clean)

    try:
        subprocess.Popen(cmd)
        msg = f"Launched {browser_name}"
        if matched_profile_name:
            msg += f" with profile '{matched_profile_name}'"
        if url:
            msg += f" navigating to {url}"

        return {
            "success": True,
            "browser": browser_name,
            "profile": matched_profile_name,
            "profile_dir": matched_profile_dir,
            "url": url,
            "message": msg,
        }
    except Exception as e:
        return {"success": False, "error": f"Failed to launch {browser_name}: {str(e)}"}
