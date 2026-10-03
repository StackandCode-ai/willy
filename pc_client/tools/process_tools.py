"""
Process and Application Status Inspector for Willy.
Checks if applications are running and closes them safely.
Works 100% locally without internet.
"""

import os
import psutil
import subprocess
from typing import Dict, Any, List

# Common friendly app process names
PROCESS_MAP = {
    "chrome": ["chrome.exe"],
    "google chrome": ["chrome.exe"],
    "edge": ["msedge.exe"],
    "microsoft edge": ["msedge.exe"],
    "genshin": ["GenshinImpact.exe", "launcher.exe"],
    "genshin impact": ["GenshinImpact.exe", "launcher.exe"],
    "spotify": ["Spotify.exe"],
    "notepad": ["Notepad.exe"],
    "calc": ["CalculatorApp.exe", "Calculator.exe", "calc.exe"],
    "calculator": ["CalculatorApp.exe", "Calculator.exe", "calc.exe"],
    "code": ["Code.exe"],
    "vscode": ["Code.exe"],
    "discord": ["Discord.exe"],
    "telegram": ["Telegram.exe"],
    "steam": ["steam.exe"],
}


def check_app_running(target: str) -> Dict[str, Any]:
    """
    Checks if a target application or game is currently running on the system.
    """
    clean_target = target.lower().strip()
    target_procs = PROCESS_MAP.get(clean_target, [f"{clean_target}.exe", clean_target])

    matches: List[Dict[str, Any]] = []
    total_mem_mb = 0.0

    for proc in psutil.process_iter(['pid', 'name', 'memory_info', 'status']):
        try:
            p_name = proc.info['name'].lower()
            if any(t.lower() in p_name for t in target_procs) or (clean_target in p_name):
                mem_mb = round(proc.info['memory_info'].rss / (1024 * 1024), 1)
                total_mem_mb += mem_mb
                matches.append({
                    "pid": proc.info['pid'],
                    "name": proc.info['name'],
                    "status": proc.info['status'],
                    "memory_mb": mem_mb,
                })
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    is_running = len(matches) > 0
    if is_running:
        message = f"Yes, '{target}' is currently running ({len(matches)} processes, using {round(total_mem_mb, 1)} MB of RAM)."
    else:
        message = f"No, '{target}' is not currently running."

    return {
        "success": True,
        "target": target,
        "is_running": is_running,
        "process_count": len(matches),
        "total_memory_mb": round(total_mem_mb, 1),
        "processes": matches[:5],
        "message": message,
    }


def close_app(target: str, force: bool = False) -> Dict[str, Any]:
    """
    Gracefully terminates or kills processes belonging to a target application.
    """
    clean_target = target.lower().strip()
    target_procs = PROCESS_MAP.get(clean_target, [f"{clean_target}.exe", clean_target])

    killed_count = 0
    for proc in psutil.process_iter(['pid', 'name']):
        try:
            p_name = proc.info['name'].lower()
            if any(t.lower() in p_name for t in target_procs) or (clean_target in p_name):
                if force:
                    proc.kill()
                else:
                    proc.terminate()
                killed_count += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    if killed_count > 0:
        return {
            "success": True,
            "target": target,
            "killed_processes": killed_count,
            "message": f"Successfully closed '{target}' ({killed_count} process instances closed).",
        }
    else:
        return {
            "success": False,
            "target": target,
            "message": f"Could not find any running process for '{target}'.",
        }
