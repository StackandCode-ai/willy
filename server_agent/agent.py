"""
Willy server agent: makes the Linux machine that runs the hub a device in Willy, like the PC.

It connects to the hub's device socket (locally, ws://127.0.0.1:8765), sends live telemetry
(CPU, RAM, disk, load, uptime) and runs actions the hub sends: browse and manage files,
upload/download through the hub, run shell commands, control pm2 apps, systemd services and
Docker containers, and read logs.

Run it next to the hub:
    pm2 start agent.py --name willy-server-agent --interpreter ../venv/bin/python

Settings (env or ~/win-assist/.env): WILLY_REMOTE_TOKEN, WILLY_HUB_WS (default
ws://127.0.0.1:8765/ws/devices), WILLY_SERVER_NAME (default "Willy Server").

Safety: commands that change things are only run when the hub says the user confirmed them
(the hub enforces that before sending); deletes go to ~/.willy-trash, never rm.
"""

import asyncio
import json
import os
import re
import shutil
import socket
import subprocess
import time
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

HOME = Path.home()
TRASH = HOME / ".willy-trash"
INBOX = HOME / "willy-inbox"
OUTPUT_LIMIT = 12_000
READ_LIMIT = 12_000
MAX_FILE = 100 * 1024 * 1024


def _load_env() -> None:
    for candidate in (Path(__file__).resolve().parent.parent / ".env", HOME / "win-assist" / ".env"):
        if candidate.exists():
            for line in candidate.read_text(encoding="utf-8", errors="ignore").splitlines():
                if "=" in line and not line.lstrip().startswith("#"):
                    key, value = line.split("=", 1)
                    os.environ[key.strip()] = value.strip().strip('"').strip("'")
            break


_load_env()
TOKEN = os.getenv("WILLY_REMOTE_TOKEN", "")
HUB_WS = os.getenv("WILLY_HUB_WS", "wss://truewilly.com/willy/ws/devices")
HUB_HTTP = HUB_WS.replace("ws://", "http://").replace("wss://", "https://").split("/ws/")[0]
NAME = os.getenv("WILLY_SERVER_NAME", "Willy Server")
DEVICE_ID = "server_" + re.sub(r"[^a-z0-9]+", "_", socket.gethostname().lower()).strip("_")
HEARTBEAT_SEC = 5.0


def log(text: str) -> None:
    print(time.strftime("%H:%M:%S") + " " + text, flush=True)


# ------------------------------------------------------------------ helpers

def run(cmd, timeout: float = 30, cwd: Optional[str] = None, shell: bool = False) -> Dict[str, Any]:
    t0 = time.perf_counter()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd, shell=shell,
                           executable="/bin/bash" if shell else None)
        out, err, code = p.stdout, p.stderr, p.returncode
    except subprocess.TimeoutExpired as e:
        out, err, code = (e.stdout or "") if isinstance(e.stdout, str) else "", f"Timed out after {timeout}s", -1
    except (OSError, ValueError) as e:
        out, err, code = "", str(e), -1
    mask = lambda t: re.sub(r"(token|key|secret|password)=([^&\s\"']+)", r"\1=***", t or "", flags=re.I)
    return {"success": code == 0, "exit_code": code, "stdout": mask(out)[-OUTPUT_LIMIT:],
            "stderr": mask(err)[-4000:], "duration_sec": round(time.perf_counter() - t0, 2)}


def pm2_bin() -> Optional[str]:
    found = shutil.which("pm2")
    if found:
        return found
    candidates = sorted(HOME.glob(".nvm/versions/node/*/bin/pm2"), reverse=True)
    return str(candidates[0]) if candidates else None


def human(n: Optional[float]) -> str:
    if n is None:
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def resolve(raw: str) -> Path:
    text = os.path.expandvars(os.path.expanduser(str(raw or "").strip().strip('"')))
    if not text:
        raise ValueError("No path given.")
    path = Path(text)
    return path if path.is_absolute() else HOME / path


# ------------------------------------------------------------------ telemetry

def telemetry() -> Dict[str, Any]:
    import psutil

    vm = psutil.virtual_memory()
    du = psutil.disk_usage("/")
    load = os.getloadavg()
    return {
        "cpu_pct": psutil.cpu_percent(interval=None),
        "ram_pct": round(vm.percent, 1),
        "ram_used_gb": round((vm.total - vm.available) / 1e9, 2),
        "ram_total_gb": round(vm.total / 1e9, 2),
        "swap_pct": round(psutil.swap_memory().percent, 1),
        "disk_pct": round(du.percent, 1),
        "disk_free_gb": round(du.free / 1e9, 1),
        "disk_total_gb": round(du.total / 1e9, 1),
        "load_1": round(load[0], 2), "load_5": round(load[1], 2), "load_15": round(load[2], 2),
        "cores": psutil.cpu_count(),
        "uptime_hours": round((time.time() - psutil.boot_time()) / 3600, 1),
        "process_count": len(psutil.pids()),
        "net_sent_mb": round(psutil.net_io_counters().bytes_sent / 1e6),
        "net_recv_mb": round(psutil.net_io_counters().bytes_recv / 1e6),
        "hostname": socket.gethostname(),
        "battery_pct": None,
        "active_window": None,
    }


def processes(limit: int = 15, sort: str = "memory") -> Dict[str, Any]:
    import psutil

    procs = list(psutil.process_iter())
    for p in procs:  # the first cpu_percent() of a process is always 0: prime, wait, then read
        try:
            p.cpu_percent(None)
        except psutil.Error:
            pass
    time.sleep(0.5)
    rows = []
    for p in psutil.process_iter(["pid", "name", "username", "cpu_percent", "memory_info", "cmdline"], ad_value=None):
        info = p.info
        rows.append({"pid": info["pid"], "name": info["name"], "user": info.get("username"),
                     "cpu": info.get("cpu_percent") or 0.0,
                     "memory_mb": round((info["memory_info"].rss if info.get("memory_info") else 0) / 1e6, 1),
                     "command": " ".join(info.get("cmdline") or [])[:120]})
    by_cpu = (sort or "").lower().startswith("cpu")
    rows.sort(key=lambda r: (r["cpu"], r["memory_mb"]) if by_cpu else (r["memory_mb"], r["cpu"]), reverse=True)
    return {"success": True, "processes": rows[:max(1, min(limit, 50))]}


# ------------------------------------------------------------------ files

def list_dir(path: str = "") -> Dict[str, Any]:
    if not (path or "").strip():
        drives = []
        import psutil

        for part in psutil.disk_partitions(all=False):
            if part.mountpoint.startswith(("/boot", "/snap")):
                continue
            try:
                u = psutil.disk_usage(part.mountpoint)
                drives.append({"name": part.mountpoint, "label": part.device,
                               "free_gb": round(u.free / 1e9, 1), "total_gb": round(u.total / 1e9, 1)})
            except OSError:
                continue
        places = [p for p in (HOME, HOME / "win-assist", Path("/var/www"), Path("/etc/nginx/conf.d"), INBOX,
                              Path("/var/log")) if p.exists()]
        entries = [{"name": str(p), "path": str(p), "folder": True, "size": None, "modified": p.stat().st_mtime}
                   for p in places]
        return {"success": True, "path": "", "parent": None, "entries": entries, "drives": drives,
                "message": "Server folders."}
    try:
        folder = resolve(path)
    except ValueError as e:
        return {"success": False, "error": str(e)}
    if not folder.is_dir():
        return {"success": False, "error": f"{folder} isn't a folder."}
    entries = []
    try:
        with os.scandir(folder) as it:
            for entry in it:
                try:
                    is_dir = entry.is_dir()
                    st = entry.stat()
                except OSError:
                    continue
                entries.append({"name": entry.name, "path": entry.path, "folder": is_dir,
                                "size": None if is_dir else st.st_size, "modified": st.st_mtime})
    except PermissionError:
        return {"success": False, "error": f"No permission to open {folder}."}
    entries.sort(key=lambda e: (not e["folder"], e["name"].lower()))
    parent = str(folder.parent) if folder.parent != folder else ""
    return {"success": True, "path": str(folder), "parent": parent, "entries": entries[:1000],
            "truncated": len(entries) > 1000, "message": f"{len(entries)} items in {folder}."}


def manage_file(op: str, path: str = "", destination: str = "", content: str = "", overwrite: bool = False) -> Dict[str, Any]:
    op = (op or "").lower()
    try:
        if op in ("write", "create"):
            target = resolve(path)
            if target.exists() and not overwrite:
                return {"success": False, "error": f"{target} already exists. Say if it should be replaced."}
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content or "", encoding="utf-8")
            return {"success": True, "path": str(target), "message": f"Saved {target}."}
        if op in ("mkdir", "create_folder"):
            target = resolve(path)
            target.mkdir(parents=True, exist_ok=True)
            return {"success": True, "path": str(target), "message": f"Made the folder {target}."}
        if op == "list":
            return list_dir(path)
        source = resolve(path)
        if not source.exists():
            return {"success": False, "error": f"There's nothing at {source}."}
        if op == "read":
            if source.is_dir():
                return list_dir(str(source))
            raw = source.read_bytes()[: READ_LIMIT * 4]
            if b"\x00" in raw[:4096]:
                return {"success": False, "error": f"{source.name} isn't a text file."}
            text = raw.decode("utf-8", errors="replace")
            return {"success": True, "path": str(source), "content": text[:READ_LIMIT],
                    "truncated": len(text) > READ_LIMIT, "message": f"Read {source.name}."}
        if op in ("move", "copy"):
            if not destination:
                return {"success": False, "error": f"Where should I {op} {source.name}?"}
            dest = resolve(destination)
            if dest.is_dir() or not dest.suffix:
                dest.mkdir(parents=True, exist_ok=True)
                dest = dest / source.name
            if dest.exists() and not overwrite:
                return {"success": False, "error": f"{dest} already exists."}
            if op == "move":
                shutil.move(str(source), str(dest))
            elif source.is_dir():
                shutil.copytree(source, dest)
            else:
                shutil.copy2(source, dest)
            return {"success": True, "path": str(dest), "message": f"{'Moved' if op == 'move' else 'Copied'} {source.name} to {dest.parent}."}
        if op == "rename":
            new_name = Path(str(destination or "")).name
            if not new_name:
                return {"success": False, "error": "What should the new name be?"}
            dest = source.with_name(new_name)
            source.rename(dest)
            return {"success": True, "path": str(dest), "message": f"Renamed {source.name} to {new_name}."}
        if op == "zip":
            base = resolve(destination) if destination else source.with_suffix("")
            archive = Path(shutil.make_archive(str(base), "zip", root_dir=str(source.parent), base_dir=source.name))
            return {"success": True, "path": str(archive), "message": f"Zipped {source.name} into {archive}."}
        if op == "unzip":
            dest = resolve(destination) if destination else source.with_suffix("")
            with zipfile.ZipFile(source) as zf:
                for member in zf.namelist():
                    if not str((dest / member).resolve()).startswith(str(dest.resolve())):
                        return {"success": False, "error": "That zip has unsafe paths; not extracting."}
                zf.extractall(dest)
            return {"success": True, "path": str(dest), "message": f"Extracted {source.name} to {dest}."}
        if op == "delete":
            TRASH.mkdir(exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            target = TRASH / f"{stamp}__{source.name}"
            shutil.move(str(source), str(target))
            return {"success": True, "message": f"Moved {source.name} to ~/.willy-trash (restore it from there)."}
        return {"success": False, "error": f"Unknown file operation '{op}'."}
    except (OSError, ValueError, zipfile.BadZipFile) as e:
        return {"success": False, "error": str(e)}


def find_files(name: str, folder: str = "", limit: int = 15) -> Dict[str, Any]:
    if not name:
        return {"success": False, "error": "Which file?"}
    roots = [resolve(folder)] if folder else [HOME, Path("/var/www"), Path("/etc/nginx")]
    wanted = re.sub(r"[^a-z0-9]+", "", name.lower())
    hits, deadline = [], time.monotonic() + 5
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            if time.monotonic() > deadline:
                break
            dirnames[:] = [d for d in dirnames if d not in ("node_modules", ".git", "venv", ".venv", "__pycache__",
                                                             ".cache", ".npm", ".nvm", ".willy-trash")]
            for f in filenames + [d for d in dirnames]:
                if wanted and wanted in re.sub(r"[^a-z0-9]+", "", f.lower()):
                    hits.append(str(Path(dirpath) / f))
                    if len(hits) >= limit:
                        break
    if not hits:
        return {"success": False, "error": f"No file matching '{name}' found."}
    return {"success": True, "files": [{"name": Path(h).name, "path": h} for h in hits],
            "message": f"Found {len(hits)}: {', '.join(Path(h).name for h in hits[:5])}."}


def _upload(path: Path, target: str = "") -> Dict[str, Any]:
    """Uploads a server file to the hub (target '' = just store it for a download link)."""
    if path.stat().st_size > MAX_FILE:
        return {"success": False, "error": f"{path.name} is over 100 MB."}
    boundary = "----willy" + uuid.uuid4().hex
    head = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n").encode()
    body = head + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    query = urllib.parse.urlencode({"target": target, "from": DEVICE_ID})
    req = urllib.request.Request(f"{HUB_HTTP}/api/v1/files?{query}", data=body, method="POST", headers={
        "Authorization": f"Bearer {TOKEN}", "Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=120) as res:
        data = json.loads(res.read())
    return {"success": True, "file": data.get("file"), "delivered": data.get("delivered"),
            "message": data.get("reply") or f"Uploaded {path.name}."}


def file_to_hub(path: str, target: str = "") -> Dict[str, Any]:
    try:
        source = resolve(path)
        if not source.is_file():
            return {"success": False, "error": f"{source} isn't a file (zip folders first)."}
        return _upload(source, target)
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": str(e)}


def receive_file(payload: Dict[str, Any]) -> Dict[str, Any]:
    url = str(payload.get("url") or "")
    if not url.startswith("/api/v1/files/"):
        return {"success": False, "error": "Bad file link."}
    folder = resolve(payload.get("folder")) if payload.get("folder") else INBOX
    folder.mkdir(parents=True, exist_ok=True)
    name = Path(str(payload.get("name") or "file")).name
    target = folder / name
    n = 1
    while target.exists():
        n += 1
        target = folder / f"{Path(name).stem} ({n}){Path(name).suffix}"
    req = urllib.request.Request(HUB_HTTP + url, headers={"Authorization": f"Bearer {TOKEN}"})
    try:
        with urllib.request.urlopen(req, timeout=300) as res, open(target, "wb") as out:
            shutil.copyfileobj(res, out)
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": f"Download failed: {e}"}
    return {"success": True, "path": str(target), "message": f"Saved {target.name} to {folder} on the server."}


# ------------------------------------------------------------------ control

def control(kind: str, name: str, action: str, lines: int = 80) -> Dict[str, Any]:
    kind, action = (kind or "").lower(), (action or "status").lower()
    if not re.fullmatch(r"[A-Za-z0-9_.@-]+", name or ""):
        return {"success": False, "error": "Give the app, service or container name."}
    if kind in ("app", "pm2"):
        pm2 = pm2_bin()
        if not pm2:
            return {"success": False, "error": "pm2 isn't installed."}
        if action == "logs":
            res = run([pm2, "logs", name, "--lines", str(lines), "--nostream", "--raw"], timeout=20)
        elif action in ("restart", "stop", "start", "reload"):
            res = run([pm2, action, name], timeout=60)
        else:
            res = run([pm2, "describe", name], timeout=20)
    elif kind in ("service", "systemd"):
        if action == "logs":
            res = run(["sudo", "-n", "journalctl", "-u", name, "-n", str(lines), "--no-pager"], timeout=20)
        elif action in ("restart", "stop", "start", "reload"):
            res = run(["sudo", "-n", "systemctl", action, name], timeout=60)
        else:
            res = run(["systemctl", "status", name, "--no-pager", "-n", "5"], timeout=20)
            res["success"] = True  # status of a stopped unit exits non-zero but answered
    elif kind in ("container", "docker"):
        if action == "logs":
            res = run(["docker", "logs", "--tail", str(lines), name], timeout=20)
            res["stdout"] = (res["stdout"] + res["stderr"])[-OUTPUT_LIMIT:]
        elif action in ("restart", "stop", "start"):
            res = run(["docker", action, name], timeout=90)
        else:
            res = run(["docker", "inspect", "--format", "{{.State.Status}} since {{.State.StartedAt}}", name], timeout=20)
    else:
        return {"success": False, "error": "kind must be app, service or container."}
    verb = {"restart": "Restarted", "stop": "Stopped", "start": "Started", "reload": "Reloaded"}.get(action)
    if res["success"] and verb:
        res["message"] = f"{verb} {name}."
    elif not res["success"]:
        res["error"] = (res.get("stderr") or res.get("stdout") or "failed").strip()[-300:]
    return res


def overview() -> Dict[str, Any]:
    t = telemetry()
    pm2 = pm2_bin()
    apps = []
    if pm2:
        raw = run([pm2, "jlist"], timeout=15).get("stdout") or "[]"
        try:
            for p in json.loads(raw[raw.index("["):]):
                env = p.get("pm2_env") or {}
                apps.append({"name": p.get("name"), "status": env.get("status"), "restarts": env.get("restart_time"),
                             "memory_mb": round((p.get("monit") or {}).get("memory", 0) / 1e6)})
        except ValueError:
            pass
    containers = []
    raw = run(["docker", "ps", "-a", "--format", "{{.Names}}\t{{.State}}"], timeout=15).get("stdout") or ""
    for line in raw.splitlines():
        if "\t" in line:
            n, s = line.split("\t", 1)
            containers.append({"name": n, "state": s})
    message = (f"{NAME}: CPU {t['cpu_pct']:.0f}%, RAM {t['ram_pct']:.0f}% ({t['ram_used_gb']} of {t['ram_total_gb']} GB), "
               f"disk {t['disk_pct']:.0f}% ({t['disk_free_gb']} GB free), up {t['uptime_hours'] / 24:.0f} days.")
    return {"success": True, "message": message, "telemetry": t, "apps": apps, "containers": containers}


def execute(action: str, p: Dict[str, Any]) -> Dict[str, Any]:
    a = (action or "").lower()
    if a in ("telemetry", "get_pc_status", "system_info", "server_status", "get_device_status"):
        return overview()
    if a == "list_processes":
        return processes(int(p.get("limit") or 15), str(p.get("sort") or p.get("sort_by") or "memory"))
    if a in ("list_dir", "list_folder"):
        return list_dir(str(p.get("path") or ""))
    if a in ("manage_file", "file_operation"):
        return manage_file(str(p.get("op") or ""), str(p.get("path") or ""), str(p.get("destination") or ""),
                           str(p.get("content") or ""), bool(p.get("overwrite")))
    if a == "find_files":
        return find_files(str(p.get("name") or ""), str(p.get("folder") or ""))
    if a == "file_to_hub":
        return file_to_hub(str(p.get("path") or ""))
    if a in ("send_file_to_phone", "send_file"):
        return file_to_hub(str(p.get("path") or ""), str(p.get("target") or "phone"))
    if a == "receive_file":
        return receive_file(p)
    if a in ("run_shell", "server_shell", "execute_command"):
        cmd = str(p.get("command") or "").strip()
        if not cmd:
            return {"success": False, "error": "No command."}
        cwd = str(resolve(p["cwd"])) if p.get("cwd") else str(HOME)
        res = run(cmd, timeout=min(float(p.get("timeout") or 60), 600), cwd=cwd, shell=True)
        res["message"] = f"Exit code {res['exit_code']}."
        return res
    if a in ("control", "server_control"):
        return control(str(p.get("kind") or ""), str(p.get("name") or ""), str(p.get("action") or "status"),
                       int(p.get("lines") or 80))
    if a in ("sys_updates", "apply_updates", "storage", "cleanup", "network", "security", "services_list",
             "service_boot", "timers", "journal", "reboot", "cancel_reboot"):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import admin

        return admin.execute(a, p)
    if a in ("git", "git_action", "git_repos"):
        import sys
        here = Path(__file__).resolve().parent
        # git_ops is shared with the PC client (pc_client/tools); a copy next to the agent wins.
        for folder in (here.parent / "pc_client" / "tools", here):
            sys.path.insert(0, str(folder))
        from git_ops import git_action, repos

        if a == "git_repos":
            return repos(refresh=bool(p.get("refresh")))
        return git_action(str(p.get("action") or "status"), str(p.get("repo") or ""), str(p.get("branch") or ""),
                          p.get("files") or None, str(p.get("message") or ""), str(p.get("url") or ""),
                          bool(p.get("stash")), int(p.get("lines") or 15))
    if a in ("ping", "notify", "speak", "ring_device", "stop_ring"):
        return {"success": True, "message": "The server has no screen or speaker; noted."}
    return {"success": False, "error": f"Action '{action}' isn't available on the server."}


# ------------------------------------------------------------------ connection

async def main() -> None:
    import websockets
    import psutil

    _load_env()
    token = os.getenv("WILLY_REMOTE_TOKEN", "") or TOKEN
    hub_ws = os.getenv("WILLY_HUB_WS", "") or HUB_WS
    name = os.getenv("WILLY_SERVER_NAME", "") or NAME
    if not token:
        log("[!] WILLY_REMOTE_TOKEN is not configured in .env.")
        log("[!] Please run './install' to connect this machine to your Willy account.")
        return

    psutil.cpu_percent(interval=None)  # prime the CPU counter
    query = urllib.parse.urlencode({"token": token, "device_id": DEVICE_ID, "device_type": "server", "name": name,
                                    "hostname": socket.gethostname(), "platform": "Linux (" + os.uname().release + ")",
                                    "boot_time": int(psutil.boot_time())})
    delay = 2
    while True:
        try:
            async with websockets.connect(f"{hub_ws}?{query}", ping_interval=20, max_size=16 * 1024 * 1024) as ws:
                log(f"[+] Connected to Willy Hub as {name} ({DEVICE_ID})")
                delay = 2

                async def heartbeat():
                    while True:
                        await ws.send(json.dumps({"type": "heartbeat", "device_id": DEVICE_ID,
                                                  "telemetry": await asyncio.to_thread(telemetry), "ts": time.time()}))
                        await asyncio.sleep(HEARTBEAT_SEC)

                async def handle(req_id, action, payload):
                    try:
                        result = await asyncio.to_thread(execute, action, payload)
                    except Exception as e:  # noqa: BLE001
                        result = {"success": False, "error": str(e)}
                    log(f"[task] {action} -> {'ok' if result.get('success', True) is not False else 'failed'}")
                    await ws.send(json.dumps({"req_id": req_id, "result": result, "device_id": DEVICE_ID,
                                              "timestamp": time.time()}, default=str))

                beat = asyncio.create_task(heartbeat())
                try:
                    async for raw in ws:
                        try:
                            data = json.loads(raw)
                        except ValueError:
                            continue
                        if isinstance(data, dict) and data.get("req_id") and data.get("action"):
                            asyncio.create_task(handle(data["req_id"], data["action"], data.get("payload") or {}))
                finally:
                    beat.cancel()
        except Exception as e:  # noqa: BLE001
            log(f"[-] Hub connection: {e}; retrying in {delay}s")
        await asyncio.sleep(delay)
        delay = min(30, delay * 2)


if __name__ == "__main__":
    asyncio.run(main())
