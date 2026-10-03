"""
File transfer between this PC and the user's phone, relayed through the hub.

    send_file(path, target="phone")  -> uploads to the hub, which tells the phone to download it
    receive_file(payload)            -> downloads a file the phone sent into Downloads\\Willy
    find_file(path, which)           -> resolves "latest screenshot", "latest download", a file
                                        copied in Explorer, or a file name

HTTP goes over the hub's last known address when DNS is slow (same address cache the
WebSocket client keeps); TLS still verifies the certificate for the hub's real name.
"""

import http.client
import json
import mimetypes
import os
import secrets
import socket
import ssl
import urllib.parse
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from pc_client import config

CHUNK = 256 * 1024
MAX_BYTES = 100 * 1024 * 1024
HOME = Path.home()
RECEIVE_DIR = HOME / "Downloads" / "Willy"
ADDRESS_CACHE = Path(os.environ.get("LOCALAPPDATA") or HOME) / "WillyPC" / "hub_address.json"


# ------------------------------------------------------------------ finding files

def _screenshot_dirs() -> List[Path]:
    dirs = [HOME / "Pictures" / "Screenshots", HOME / "OneDrive" / "Pictures" / "Screenshots",
            HOME / "Videos" / "Captures", HOME / "Desktop"]
    onedrive = os.environ.get("OneDrive")
    if onedrive:
        dirs.insert(1, Path(onedrive) / "Pictures" / "Screenshots")
    return dirs


def _newest(folders: List[Path], suffixes: Optional[Tuple[str, ...]] = None) -> Optional[Path]:
    best: Optional[Path] = None
    best_time = 0.0
    for folder in folders:
        try:
            entries = list(folder.iterdir())
        except OSError:
            continue
        for entry in entries:
            try:
                if not entry.is_file() or entry.name.lower() in ("desktop.ini", "thumbs.db"):
                    continue
                if entry.suffix.lower() in (".tmp", ".crdownload", ".part", ".partial"):
                    continue
                if suffixes and entry.suffix.lower() not in suffixes:
                    continue
                mtime = entry.stat().st_mtime
            except OSError:
                continue
            if mtime > best_time:
                best, best_time = entry, mtime
    return best


def _clipboard_files() -> List[Path]:
    """Files copied in Explorer (Ctrl+C)."""
    try:
        import win32clipboard
        import win32con
    except ImportError:
        return []
    try:
        win32clipboard.OpenClipboard()
        try:
            if not win32clipboard.IsClipboardFormatAvailable(win32con.CF_HDROP):
                return []
            return [Path(p) for p in win32clipboard.GetClipboardData(win32con.CF_HDROP)]
        finally:
            win32clipboard.CloseClipboard()
    except Exception:
        return []


def find_file(path: str = "", which: str = "") -> Tuple[Optional[Path], str]:
    """Returns (file, error)."""
    which = (which or "").strip().lower()
    if which == "latest_screenshot":
        found = _newest(_screenshot_dirs(), (".png", ".jpg", ".jpeg", ".webp", ".mp4"))
        return (found, "") if found else (None, "I couldn't find a screenshot in Pictures\\Screenshots.")
    if which == "latest_download":
        found = _newest([HOME / "Downloads"])
        return (found, "") if found else (None, "Your Downloads folder is empty.")
    if which == "clipboard":
        files = [p for p in _clipboard_files() if p.is_file()]
        return (files[0], "") if files else (None, "There's no file copied right now (select it in Explorer and press Ctrl+C).")

    raw = os.path.expandvars(os.path.expanduser((path or "").strip().strip('"')))
    if not raw:
        return None, "Tell me which file to send (a path or a file name)."
    candidate = Path(raw)
    if candidate.is_absolute():
        if candidate.is_file():
            return candidate, ""
        return None, f"There's no file at {candidate}."
    # A bare name: look in the usual places, newest match first.
    name = candidate.name.lower()
    matches: List[Path] = []
    for folder in (HOME / "Downloads", HOME / "Desktop", HOME / "Documents", HOME / "Pictures", Path.cwd()):
        try:
            for depth, pattern in ((0, "*"), (1, "*/*")):
                for entry in folder.glob(pattern):
                    if entry.is_file() and (entry.name.lower() == name or name in entry.name.lower()):
                        matches.append(entry)
        except OSError:
            continue
    if not matches:
        return None, f"I couldn't find a file called '{candidate.name}' in Downloads, Desktop, Documents or Pictures."
    exact = [m for m in matches if m.name.lower() == name]
    pool = exact or matches
    return max(pool, key=lambda p: p.stat().st_mtime), ""


# ------------------------------------------------------------------ hub HTTP

def _hub() -> Tuple[str, str, int, str]:
    """(scheme, host, port, base path) of the hub's HTTP API."""
    base = config.SERVER_URL.replace("wss://", "https://").replace("ws://", "http://").split("/ws/")[0]
    parsed = urllib.parse.urlparse(base)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return parsed.scheme, parsed.hostname or "127.0.0.1", port, parsed.path.rstrip("/")


def _cached_ip(host: str) -> Optional[str]:
    try:
        data = json.loads(ADDRESS_CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data.get("ip") if isinstance(data, dict) and data.get("host") == host else None


class _Connection(http.client.HTTPSConnection):
    """HTTPS to `host` dialled at a known IP address (certificate still checked for `host`)."""

    def __init__(self, host: str, port: int, ip: Optional[str], timeout: float):
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self._ip = ip

    def connect(self) -> None:
        sock = socket.create_connection((self._ip or self.host, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


DNS_FAST_TIMEOUT_SEC = 2.5


def _resolve_fast(host: str, port: int) -> bool:
    """True when DNS answers within DNS_FAST_TIMEOUT_SEC (then the normal name is used)."""
    import concurrent.futures

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        pool.submit(socket.getaddrinfo, host, port, 0, socket.SOCK_STREAM).result(timeout=DNS_FAST_TIMEOUT_SEC)
        return True
    except Exception:
        return False
    finally:
        pool.shutdown(wait=False)


def _connect(timeout: float = 60) -> http.client.HTTPConnection:
    """Dials the hub by name; its last known address is used only when DNS is slow or failing
    (a saved address from another network can't connect and would just time out)."""
    scheme, host, port, _ = _hub()
    if scheme != "https":
        return http.client.HTTPConnection(host, port, timeout=timeout)
    if _resolve_fast(host, port):
        return _Connection(host, port, None, timeout)
    ip = _cached_ip(host)
    if ip:
        conn = _Connection(host, port, ip, min(timeout, 5))
        try:
            conn.connect()
            conn.timeout = timeout
            conn.sock.settimeout(timeout)
            return conn
        except OSError:
            conn.close()
    return _Connection(host, port, None, timeout)


def _headers() -> Dict[str, str]:
    return {"Authorization": f"Bearer {config.TOKEN}"}


def send_file(file: Path, target: str = "phone") -> Dict[str, Any]:
    """Uploads `file` to the hub for `target` ('phone', 'pc' or a device id)."""
    try:
        size = file.stat().st_size
    except OSError as e:
        return {"success": False, "error": f"Can't read {file}: {e}"}
    if size > MAX_BYTES:
        return {"success": False, "error": f"{file.name} is {size // (1024 * 1024)} MB; files up to 100 MB can be sent."}
    boundary = "----willy" + secrets.token_hex(12)
    mime = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
    safe = file.name.replace('"', "'")
    head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{safe}\"\r\n"
            f"Content-Type: {mime}\r\n\r\n").encode("utf-8")
    tail = f"\r\n--{boundary}--\r\n".encode("ascii")

    def body() -> Iterator[bytes]:
        yield head
        with open(file, "rb") as fh:
            while True:
                chunk = fh.read(CHUNK)
                if not chunk:
                    break
                yield chunk
        yield tail

    _, _, _, base = _hub()
    query = urllib.parse.urlencode({"target": target, "from": config.DEVICE_ID})
    conn = _connect(timeout=300)
    try:
        conn.request("POST", f"{base}/api/v1/files?{query}", body=body(), headers={
            **_headers(),
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(head) + size + len(tail)),
        })
        res = conn.getresponse()
        raw = res.read()
    except OSError as e:
        return {"success": False, "error": f"Couldn't reach the hub: {e}"}
    finally:
        conn.close()
    try:
        data = json.loads(raw)
    except ValueError:
        data = {}
    if res.status == 413:
        return {"success": False, "error": data.get("detail") or "That file is too big for the hub."}
    if res.status != 200:
        return {"success": False, "error": data.get("detail") or f"The hub answered {res.status}."}
    return {
        "success": True,
        "delivered": bool(data.get("delivered")),
        "file": data.get("file"),
        "message": data.get("reply") or f"Sent {file.name}.",
    }


def _unique(folder: Path, name: str) -> Path:
    target = folder / name
    stem, suffix = target.stem, target.suffix
    n = 1
    while target.exists():
        n += 1
        target = folder / f"{stem} ({n}){suffix}"
    return target


def receive_file(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Downloads a file another device sent (payload: id, name, size, url, from)."""
    url = str(payload.get("url") or "")
    name = Path(str(payload.get("name") or "file").replace("\\", "/")).name or "file"
    if not url.startswith("/api/v1/files/"):
        return {"success": False, "error": "Bad file link."}
    RECEIVE_DIR.mkdir(parents=True, exist_ok=True)
    target = _unique(RECEIVE_DIR, name)
    partial = target.with_name(target.name + ".part")
    _, _, _, base = _hub()
    conn = _connect(timeout=300)
    try:
        conn.request("GET", base + url, headers=_headers())
        res = conn.getresponse()
        if res.status != 200:
            return {"success": False, "error": f"The hub answered {res.status} for {name}."}
        written = 0
        with open(partial, "wb") as out:
            while True:
                chunk = res.read(CHUNK)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_BYTES:
                    raise OSError("file is larger than 100 MB")
                out.write(chunk)
        partial.replace(target)
    except OSError as e:
        partial.unlink(missing_ok=True)
        return {"success": False, "error": f"Download of {name} failed: {e}"}
    finally:
        conn.close()
    return {"success": True, "path": str(target), "message": f"Saved {target.name} to Downloads\\Willy on your PC."}
