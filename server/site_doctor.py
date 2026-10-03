"""
"Why is blog.example.com down?" - diagnosis for a website served by this machine's nginx,
and (with the user's yes) starting the app behind it again.

Looks at: the nginx server block (static folder or proxy_pass port), whether anything
listens on that port, pm2's running and saved apps, project folders whose code listens on
that port, and how the user started it before (shell history).
"""

import json
import re
import subprocess
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

HOME = Path.home()
SKIP = {"node_modules", ".git", ".nvm", ".npm", ".cache", ".local", ".pm2", "venv", ".venv", ".willy-trash"}


def _run(cmd: List[str], timeout: float = 15) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _nginx_block(domain: str) -> Optional[str]:
    for conf in sorted(Path("/etc/nginx/conf.d").glob("*.conf")):
        try:
            text = conf.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            text = _run(["sudo", "-n", "cat", str(conf)])
        for m in re.finditer(r"server\s*\{", text):
            depth, i = 0, m.end() - 1
            for j in range(i, len(text)):
                depth += {"{": 1, "}": -1}.get(text[j], 0)
                if depth == 0:
                    block = text[m.start():j + 1]
                    if re.search(rf"server_name[^;]*\b{re.escape(domain)}\b", block) and "443" in block:
                        return block
                    break
    return None


def _pm2() -> str:
    for p in sorted(HOME.glob(".nvm/versions/node/*/bin/pm2"), reverse=True):
        return str(p)
    return "pm2"


def _listening(port: int) -> bool:
    return f":{port} " in _run(["ss", "-ltn"])


def _http(domain: str) -> Optional[int]:
    try:
        with urllib.request.urlopen(f"https://{domain}/", timeout=8) as res:
            return res.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:  # noqa: BLE001
        return None


def _projects_on_port(port: int) -> List[Dict[str, str]]:
    """Folders under home whose server code defaults to this port."""
    found = []
    for pkg in HOME.glob("*/package.json"):
        folder = pkg.parent
        if folder.name in SKIP:
            continue
        try:
            scripts = json.loads(pkg.read_text(encoding="utf-8")).get("scripts", {})
        except (OSError, ValueError):
            scripts = {}
        start = scripts.get("start", "")
        main = re.search(r"node\s+([\w./-]+\.m?js)", start)
        candidates = [folder / main.group(1)] if main else []
        candidates += [folder / n for n in ("server.js", "index.js", "app.js", "src/server.js")]
        env_port = None
        env = folder / ".env"
        if env.exists():
            m = re.search(r"^PORT\s*=\s*(\d+)", env.read_text(encoding="utf-8", errors="ignore"), re.M)
            env_port = int(m.group(1)) if m else None
        for c in candidates:
            if not c.exists():
                continue
            code = c.read_text(encoding="utf-8", errors="ignore")
            default = re.search(r"PORT\s*\|\|\s*(\d+)", code) or re.search(r"listen\(\s*(\d+)", code)
            if env_port == port or (default and int(default.group(1)) == port and env_port in (None, port)):
                found.append({"folder": str(folder), "script": str(c.relative_to(folder))})
                break
    return found


def _history_name(folder: str) -> Optional[str]:
    """The pm2 --name the user gave this app before, from shell history."""
    hist = HOME / ".bash_history"
    if not hist.exists():
        return None
    lines = hist.read_text(encoding="utf-8", errors="ignore").splitlines()
    base = Path(folder).name
    in_folder = False
    for line in lines:
        if re.search(rf"\bcd\s+.*{re.escape(base)}", line):
            in_folder = True
        m = re.search(r"pm2 start .*--name[= ]\"?([\w.-]+)", line)
        if in_folder and m:
            return m.group(1)
    return None


def diagnose(domain: str) -> Dict[str, Any]:
    domain = (domain or "").strip().lower().removeprefix("https://").removeprefix("http://").strip("/")
    block = _nginx_block(domain)
    if not block:
        return {"success": False, "error": f"{domain} isn't one of this server's nginx sites."}
    status = _http(domain)
    proxy = re.search(r"proxy_pass\s+https?://([\w.]+):(\d+)", block)
    root = re.search(r"^\s*root\s+([^;]+);", block, re.M)
    out: Dict[str, Any] = {"success": True, "domain": domain, "http_status": status,
                           "up": status is not None and status < 500}
    if proxy:
        port = int(proxy.group(2))
        out.update({"kind": "app", "port": port, "listening": _listening(port)})
        raw = _run([_pm2(), "jlist"]) or "[]"
        try:
            apps = json.loads(raw[raw.find("["):])
        except ValueError:
            apps = []
        running = [a["name"] for a in apps if str((a.get("pm2_env") or {}).get("env", {}).get("PORT") or "") == str(port)]
        projects = _projects_on_port(port)
        out["pm2_app"] = running[0] if running else None
        out["candidates"] = projects
        if out["listening"]:
            out["explanation"] = (f"Something is listening on port {port}" + (f" (pm2 app {running[0]})" if running else "")
                                  + (", so the site should work." if out["up"] else f", but the site answers HTTP {status}."))
        elif projects:
            p = projects[0]
            name = _history_name(p["folder"]) or Path(p["folder"]).name
            out["start"] = {"folder": p["folder"], "script": p["script"], "name": name}
            out["explanation"] = (f"Nginx forwards {domain} to port {port}, but nothing is running there. The app is in "
                                  f"{p['folder']} ({p['script']}); it isn't running in pm2. It can be started as pm2 app "
                                  f"'{name}'.")
        else:
            out["explanation"] = (f"Nginx forwards {domain} to port {port}, but nothing is running there and I couldn't "
                                  "find a project folder that uses that port.")
    elif root:
        folder = Path(root.group(1).strip())
        exists = folder.exists()
        out.update({"kind": "static", "folder": str(folder), "folder_exists": exists,
                    "has_index": exists and any((folder / n).exists() for n in ("index.html", "index.htm"))})
        out["explanation"] = (f"{domain} serves files from {folder}" + ("" if exists else ", which doesn't exist")
                              + ("" if out.get("has_index") or not exists else " but there's no index.html") + ".")
    return out


def start(domain: str) -> Dict[str, Any]:
    info = diagnose(domain)
    plan = info.get("start")
    if not plan:
        return {"success": False, "error": info.get("explanation") or info.get("error") or "Nothing to start."}
    pm2 = _pm2()
    res = subprocess.run([pm2, "start", plan["script"], "--name", plan["name"]], cwd=plan["folder"],
                         capture_output=True, text=True, timeout=60)
    if res.returncode != 0:
        return {"success": False, "error": f"pm2 couldn't start it: {(res.stderr or res.stdout)[-300:]}"}
    _run([pm2, "save"])
    import time
    time.sleep(3)
    after = _http(info["domain"])
    ok = after is not None and after < 500
    return {"success": ok, "message": (f"Started {plan['name']} from {plan['folder']}; {info['domain']} answers HTTP {after}."
                                       if ok else f"Started {plan['name']}, but {info['domain']} still answers {after}; "
                                                  "check its logs.")}
