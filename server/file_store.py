"""
Short-lived file relay between the user's devices ("send this file to my phone").

The sender uploads to the hub, the hub tells the target device to download it, and the file
is deleted after FILE_TTL_SEC. Files live under DATA_DIR/files/<id>/<name> with a small JSON
sidecar, so they survive a hub restart until they expire.
"""

import json
import re
import secrets
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

FILE_TTL_SEC = 24 * 3600
MAX_FILE_BYTES = 100 * 1024 * 1024
_CHUNK = 1024 * 1024
_ID_RE = re.compile(r"^[a-f0-9]{24}$")


def safe_name(name: str) -> str:
    """A plain file name (no folders, no characters Windows or Android refuse)."""
    name = Path(str(name or "").replace("\\", "/")).name
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return (name or "file")[:150]


class FileTooLarge(Exception):
    pass


class FileStore:
    def __init__(self, root: Path, ttl_sec: float = FILE_TTL_SEC, max_bytes: int = MAX_FILE_BYTES):
        self.root = Path(root)
        self.ttl_sec = ttl_sec
        self.max_bytes = max_bytes
        self.root.mkdir(parents=True, exist_ok=True)

    def _dir(self, file_id: str) -> Optional[Path]:
        return self.root / file_id if _ID_RE.match(file_id or "") else None

    async def save(self, upload, sender: str = "", mime: str = "") -> Dict[str, Any]:
        """Streams an UploadFile to disk; raises FileTooLarge past max_bytes."""
        self.cleanup()
        file_id = secrets.token_hex(12)
        folder = self.root / file_id
        folder.mkdir(parents=True)
        name = safe_name(getattr(upload, "filename", "") or "file")
        size = 0
        try:
            with open(folder / name, "wb") as out:
                while True:
                    chunk = await upload.read(_CHUNK)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > self.max_bytes:
                        raise FileTooLarge(f"Files up to {self.max_bytes // (1024 * 1024)} MB can be sent.")
                    out.write(chunk)
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        meta = {
            "id": file_id,
            "name": name,
            "size": size,
            "mime": mime or getattr(upload, "content_type", "") or "application/octet-stream",
            "from": sender,
            "created_at": time.time(),
            "url": f"/api/v1/files/{file_id}",
        }
        (folder / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        return meta

    def get(self, file_id: str) -> Optional[Dict[str, Any]]:
        folder = self._dir(file_id)
        if folder is None or not (folder / "meta.json").exists():
            return None
        try:
            meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if time.time() - meta.get("created_at", 0) > self.ttl_sec:
            shutil.rmtree(folder, ignore_errors=True)
            return None
        path = folder / meta["name"]
        return {**meta, "path": str(path)} if path.exists() else None

    def list(self) -> List[Dict[str, Any]]:
        self.cleanup()
        out = [self.get(p.name) for p in self.root.iterdir() if p.is_dir()]
        return sorted((m for m in out if m), key=lambda m: m["created_at"], reverse=True)

    def cleanup(self) -> int:
        """Deletes expired files; returns how many were removed."""
        removed = 0
        now = time.time()
        for folder in list(self.root.iterdir()) if self.root.exists() else []:
            if not folder.is_dir():
                continue
            try:
                created = json.loads((folder / "meta.json").read_text(encoding="utf-8")).get("created_at", 0)
            except (OSError, ValueError):
                created = folder.stat().st_mtime
            if now - created > self.ttl_sec:
                shutil.rmtree(folder, ignore_errors=True)
                removed += 1
        return removed


def describe_size(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    if size >= 1024:
        return f"{round(size / 1024)} KB"
    return f"{size} bytes"
