"""
Finding files by a spoken name, opening them in a chosen app (VLC...), and placing windows
on a given monitor ("play Sarvamaya on VLC on screen 2").

Spoken names are fuzzy ("sarvamaya" for "Sarvam_Maya_2024.mkv"), so matching normalises
both sides and also accepts close spellings.
"""

import difflib
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HOME = Path.home()
KINDS = {
    "video": {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".webm", ".m4v", ".flv", ".mpg", ".mpeg", ".ts", ".3gp"},
    "audio": {".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg", ".wma", ".opus"},
    "image": {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".heic", ".tif", ".tiff"},
    "document": {".pdf", ".doc", ".docx", ".txt", ".md", ".xls", ".xlsx", ".ppt", ".pptx", ".csv", ".odt"},
}
SKIP_DIRS = {"windows", "program files", "program files (x86)", "programdata", "$recycle.bin", "appdata",
             "node_modules", ".git", "__pycache__", "system volume information", "venv", ".venv", "site-packages"}
SEARCH_SECONDS = 4.0
APP_EXES = {
    "vlc": [r"%ProgramFiles%\VideoLAN\VLC\vlc.exe", r"%ProgramFiles(x86)%\VideoLAN\VLC\vlc.exe"],
    "mpc": [r"%ProgramFiles%\MPC-HC\mpc-hc64.exe", r"%ProgramFiles(x86)%\MPC-HC\mpc-hc.exe"],
    "potplayer": [r"%ProgramFiles%\DAUM\PotPlayer\PotPlayerMini64.exe"],
    "notepad++": [r"%ProgramFiles%\Notepad++\notepad++.exe"],
    "word": [r"%ProgramFiles%\Microsoft Office\root\Office16\WINWORD.EXE"],
    "excel": [r"%ProgramFiles%\Microsoft Office\root\Office16\EXCEL.EXE"],
}


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def _score(query: str, filename: str) -> float:
    """1.0 = exact; 0 = no match. Compares without spaces/punctuation, then fuzzily."""
    stem = Path(filename).stem
    q, n = _norm(query), _norm(stem)
    if not q or not n:
        return 0.0
    if q == n:
        return 1.0
    if q in n:
        return 0.9 - min(0.3, (len(n) - len(q)) / 200)
    words = [w for w in re.split(r"[^a-z0-9]+", query.lower()) if w]
    if words and all(_norm(w) in n for w in words):
        return 0.8
    ratio = difflib.SequenceMatcher(None, q, n[: len(q) + 4]).ratio()
    return ratio * 0.75 if ratio >= 0.72 else 0.0


def _search_roots(folder_hint: str) -> List[Path]:
    from pc_client.tools.file_ops import _known_folders

    known = _known_folders()
    base = [known[k] for k in ("downloads", "desktop", "documents", "videos", "music", "pictures") if known[k].exists()]
    hint = (folder_hint or "").strip()
    if hint:
        raw = Path(os.path.expandvars(os.path.expanduser(hint)))
        if raw.is_absolute() and raw.is_dir():
            return [raw]
        key = hint.lower().split("/")[0].split("\\")[0].strip()
        if key in known and known[key].exists():
            sub = Path(hint.replace("\\", "/")).parts[1:]
            target = known[key].joinpath(*sub)
            return [target] if target.is_dir() else [known[key]]
        # A folder name like "telegram desktop": find it one or two levels under the usual places.
        wanted = _norm(hint)
        found = []
        for root in base:
            for pattern in ("*", "*/*"):
                try:
                    for d in root.glob(pattern):
                        if d.is_dir() and wanted and wanted in _norm(d.name):
                            found.append(d)
                except OSError:
                    continue
        if found:
            return found
    extra = [Path(f"{d}:\\") for d in "DEFGH" if os.path.exists(f"{d}:\\")]
    return base + extra


def find_files(name: str, folder: str = "", kind: str = "", limit: int = 8) -> Dict[str, Any]:
    if not (name or "").strip():
        return {"success": False, "error": "Which file? Give me (part of) its name."}
    exts = KINDS.get((kind or "").lower())
    roots = _search_roots(folder)
    deadline = time.monotonic() + SEARCH_SECONDS
    hits: List[Tuple[float, float, Path]] = []
    seen = set()
    timed_out = False
    for root in roots:
        max_depth = 6 if len(str(root)) > 3 else 3  # whole drives: stay shallow
        root_depth = len(root.parts)
        for dirpath, dirnames, filenames in os.walk(root):
            if time.monotonic() > deadline:
                timed_out = True
                break
            depth = len(Path(dirpath).parts) - root_depth
            dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_DIRS and not d.startswith(".")
                           and depth < max_depth]
            for fname in filenames:
                if exts and Path(fname).suffix.lower() not in exts:
                    continue
                score = _score(name, fname)
                if score <= 0:
                    continue
                path = Path(dirpath) / fname
                if path in seen:
                    continue
                seen.add(path)
                try:
                    mtime = path.stat().st_mtime
                except OSError:
                    continue
                hits.append((score, mtime, path))
        if timed_out:
            break
    hits.sort(key=lambda h: (h[0], h[1]), reverse=True)
    files = [{"name": p.name, "path": str(p), "folder": str(p.parent), "score": round(s, 2)} for s, _, p in hits[:limit]]
    if not files:
        where = folder or "Downloads, Desktop, Documents, Videos, Music, Pictures and the other drives"
        return {"success": False, "error": f"No file matching '{name}' in {where}.", "searched": [str(r) for r in roots]}
    return {"success": True, "files": files, "message": f"Found {len(files)} match(es); best: {files[0]['name']}."}


# ------------------------------------------------------------------ apps & monitors

def find_app(app: str) -> Optional[str]:
    key = (app or "").strip().lower().replace(" media player", "").replace(".exe", "")
    for candidate in APP_EXES.get(key, []):
        path = os.path.expandvars(candidate)
        if os.path.exists(path):
            return path
    if key:
        try:
            import winreg

            for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                try:
                    with winreg.OpenKey(hive, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{key}.exe") as k:
                        value = winreg.QueryValue(k, None)
                        if value and os.path.exists(value.strip('"')):
                            return value.strip('"')
                except OSError:
                    continue
        except ImportError:
            pass
        return shutil.which(key)
    return None


def monitors() -> List[Dict[str, int]]:
    """Monitors as Windows numbers them for the user: the main one first, then left to right."""
    import win32api

    out = []
    for handle, _, rect in win32api.EnumDisplayMonitors():
        info = win32api.GetMonitorInfo(handle)
        left, top, right, bottom = info["Work"]
        out.append({"left": left, "top": top, "width": right - left, "height": bottom - top,
                    "primary": bool(info.get("Flags", 0) & 1)})
    out.sort(key=lambda m: (not m["primary"], m["left"]))
    return out


def _windows_for_pid(pid: int) -> List[int]:
    import win32gui
    import win32process

    found = []

    def visit(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd):
            if win32process.GetWindowThreadProcessId(hwnd)[1] == pid:
                found.append(hwnd)
        return True

    win32gui.EnumWindows(visit, None)
    return found


def _windows_titled(text: str) -> List[int]:
    import win32gui

    wanted = (text or "").lower()
    found = []

    def visit(hwnd, _):
        title = win32gui.GetWindowText(hwnd)
        if win32gui.IsWindowVisible(hwnd) and title and wanted in title.lower():
            found.append(hwnd)
        return True

    win32gui.EnumWindows(visit, None)
    return found


def place_window(hwnd: int, monitor: int, maximize: bool = True) -> Dict[str, Any]:
    import win32con
    import win32gui

    screens = monitors()
    if not 1 <= monitor <= len(screens):
        return {"success": False, "error": f"There's no screen {monitor}; this PC has {len(screens)}."}
    m = screens[monitor - 1]
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    win32gui.SetWindowPos(hwnd, 0, m["left"] + 40, m["top"] + 40, max(400, m["width"] - 80),
                          max(300, m["height"] - 80), win32con.SWP_NOZORDER | win32con.SWP_SHOWWINDOW)
    if maximize:
        win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass
    return {"success": True}


def move_window(target: str, monitor: int) -> Dict[str, Any]:
    hwnds = _windows_titled(target)
    if not hwnds:
        return {"success": False, "error": f"No open window matches '{target}'."}
    res = place_window(hwnds[0], int(monitor))
    if res.get("success"):
        res["message"] = f"Moved {target} to screen {monitor}."
    return res


def open_with(path: Path, app: str = "", monitor: Optional[int] = None, fullscreen: bool = False) -> Dict[str, Any]:
    exe = find_app(app) if app else None
    if app and not exe:
        return {"success": False, "error": f"I couldn't find {app} on this PC."}
    if exe:
        args = [exe, str(path)]
        if fullscreen and "vlc" in exe.lower():
            args.insert(1, "--fullscreen")
        proc = subprocess.Popen(args, close_fds=True)
        pid = proc.pid
    else:
        os.startfile(str(path))  # noqa: S606 - default app for the user's own file
        pid = None
    where = ""
    if monitor:
        hwnd = None
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and hwnd is None:
            time.sleep(0.3)
            candidates = _windows_for_pid(pid) if pid else []
            if not candidates:
                candidates = _windows_titled(path.stem[:20]) or (_windows_titled(app) if app else [])
            hwnd = candidates[0] if candidates else None
        if hwnd is None:
            where = f" (I couldn't find its window to move it to screen {monitor})"
        else:
            placed = place_window(hwnd, int(monitor), maximize=True)
            where = f" on screen {monitor}" if placed.get("success") else f" ({placed.get('error')})"
    verb = "Playing" if path.suffix.lower() in KINDS["video"] | KINDS["audio"] else "Opened"
    app_name = Path(exe).stem.upper() if exe and "vlc" in exe.lower() else (Path(exe).stem if exe else "its default app")
    return {"success": True, "path": str(path), "message": f"{verb} {path.name} in {app_name}{where}."}


def open_file_smart(path: str = "", name: str = "", folder: str = "", app: str = "",
                    monitor: Optional[int] = None, fullscreen: bool = False, kind: str = "") -> Dict[str, Any]:
    """Opens a file given its path, or finds it by (part of) its name first."""
    target: Optional[Path] = None
    if path:
        from pc_client.tools.file_ops import resolve

        try:
            candidate = resolve(path, must_exist=True)
            if candidate.exists():
                target = candidate
        except (OSError, ValueError):
            name = name or Path(path).name
    if target is None:
        if not name:
            return {"success": False, "error": "Which file should I open?"}
        if not kind and app and app.lower() in ("vlc", "mpc", "potplayer"):
            kind = "video"
        found = find_files(name, folder, kind)
        if not found.get("success") and kind:
            found = find_files(name, folder)  # maybe it isn't the type we guessed
        if not found.get("success"):
            return found
        files = found["files"]
        best = files[0]
        close = [f for f in files[1:] if f["score"] >= best["score"] - 0.02 and f["name"] != best["name"]]
        if close and best["score"] < 1.0:
            return {"success": False, "error": "AMBIGUOUS", "files": files[:5],
                    "reply": "Several files match; ask the user which one: " + "; ".join(f["name"] for f in files[:5])}
        target = Path(best["path"])
    if target.is_dir() and not app:
        os.startfile(str(target))  # noqa: S606
        return {"success": True, "path": str(target), "message": f"Opened the {target.name} folder."}
    return open_with(target, app, monitor, fullscreen)
