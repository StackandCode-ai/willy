"""
System administration for the Willy server agent: what a server control panel (Cockpit)
offers, as agent actions. Read-only views run directly; the hub asks the user before
anything that changes the system (updates, cleanup, boot settings, reboot).
"""

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List

HOME = Path.home()


def _run(cmd, timeout: float = 30, sudo: bool = False) -> Dict[str, Any]:
    if sudo:
        cmd = ["sudo", "-n"] + list(cmd)
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return {"ok": p.returncode == 0, "code": p.returncode, "out": p.stdout, "err": p.stderr}
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": -1, "out": "", "err": f"timed out after {timeout}s"}
    except OSError as e:
        return {"ok": False, "code": -1, "out": "", "err": str(e)}


def _size(path: Path, timeout: float = 20) -> int:
    res = _run(["du", "-sxb", str(path)], timeout, sudo=not str(path).startswith(str(HOME)))
    try:
        return int(res["out"].split()[0])
    except (IndexError, ValueError):
        return 0


def _gb(n: float) -> float:
    return round(n / 1e9, 2)


# ------------------------------------------------------------------ updates

def updates() -> Dict[str, Any]:
    check = _run(["dnf", "-q", "check-update", "--refresh"], 180, sudo=True)
    pkgs = []
    for line in check["out"].splitlines():
        parts = line.split()
        if len(parts) >= 3 and "." in parts[0] and not line.startswith(("Obsoleting", "Security", " ")):
            pkgs.append({"name": parts[0], "version": parts[1], "repo": parts[2]})
    security = _run(["dnf", "-q", "updateinfo", "list", "--security"], 120, sudo=True)["out"].strip().splitlines()
    release = _run(["dnf", "check-release-update"], 60, sudo=True)
    newer = re.findall(r"Version (\d{4}\.\d+\.\d+)", release["out"] + release["err"])
    reboot = _run(["needs-restarting", "-r"], 60, sudo=True)
    kernel = _run(["uname", "-r"])["out"].strip()
    message = (f"{len(pkgs)} package update{'s' if len(pkgs) != 1 else ''} available"
               + (f" ({len(security)} security)" if security else "") + ". "
               + (f"A newer Amazon Linux release is available ({newer[-1]}). " if newer else "")
               + ("A reboot is needed to finish earlier updates." if reboot["code"] == 1 else "No reboot needed."))
    return {"success": True, "packages": pkgs[:200], "security_count": len(security), "newer_release": newer[-1] if newer else None,
            "release_note": (release["out"] + release["err"]).strip()[:600], "reboot_needed": reboot["code"] == 1,
            "kernel": kernel, "message": message}


def apply_updates(security_only: bool = False) -> Dict[str, Any]:
    cmd = ["dnf", "-y", "upgrade"] + (["--security"] if security_only else [])
    res = _run(cmd, 1800, sudo=True)
    after = _run(["needs-restarting", "-r"], 60, sudo=True)
    tail = (res["out"] + res["err"]).strip().splitlines()[-15:]
    return {"success": res["ok"], "output": "\n".join(tail), "reboot_needed": after["code"] == 1,
            "message": ("Updates installed." if res["ok"] else "The update failed.")
                       + (" A reboot is needed to finish." if after["code"] == 1 else "")}


# ------------------------------------------------------------------ storage

CLEANABLE = {
    "journal": "System logs (journald)",
    "docker": "Unused Docker images, stopped containers and build cache",
    "pm2_logs": "Old pm2 app logs",
    "dnf_cache": "Package download cache",
    "trash": "Willy's trash (~/.willy-trash)",
}


def storage() -> Dict[str, Any]:
    import psutil

    mounts = []
    for part in psutil.disk_partitions(all=False):
        if part.mountpoint.startswith(("/boot", "/snap")):
            continue
        try:
            u = psutil.disk_usage(part.mountpoint)
        except OSError:
            continue
        mounts.append({"mount": part.mountpoint, "device": part.device, "fs": part.fstype,
                       "used_gb": _gb(u.used), "total_gb": _gb(u.total), "free_gb": _gb(u.free), "pct": u.percent})
    biggest = []
    for root in (HOME, Path("/var"), Path("/opt"), Path("/usr/local")):
        if not root.exists():
            continue
        res = _run(["du", "-xb", "--max-depth=1", str(root)], 40, sudo=root != HOME)
        for line in res["out"].splitlines():
            try:
                size, path = line.split("\t", 1)
            except ValueError:
                continue
            if Path(path) != root:
                biggest.append({"path": path, "gb": _gb(int(size))})
    biggest.sort(key=lambda x: x["gb"], reverse=True)
    journal = _run(["journalctl", "--disk-usage"], 20, sudo=True)["out"]
    m = re.search(r"take up ([\d.]+)([KMGT])", journal)
    journal_gb = float(m.group(1)) * {"K": 1e-6, "M": 1e-3, "G": 1, "T": 1000}[m.group(2)] if m else 0.0
    docker = _run(["docker", "system", "df", "--format", "{{.Type}}\t{{.Reclaimable}}"], 30)["out"]
    cleanable = {
        "journal": round(journal_gb, 2),
        "docker": docker.strip().replace("\t", ": ").replace("\n", "; "),
        "pm2_logs": _gb(_size(HOME / ".pm2" / "logs")),
        "dnf_cache": _gb(_size(Path("/var/cache/dnf"))),
        "trash": _gb(_size(HOME / ".willy-trash")) if (HOME / ".willy-trash").exists() else 0.0,
    }
    swap = psutil.swap_memory()
    root = next((x for x in mounts if x["mount"] == "/"), mounts[0] if mounts else {})
    return {"success": True, "mounts": mounts, "biggest": biggest[:15], "cleanable": cleanable,
            "cleanable_labels": CLEANABLE,
            "swap": {"used_gb": _gb(swap.used), "total_gb": _gb(swap.total), "pct": swap.percent},
            "message": f"Disk / is {root.get('pct', 0):.0f}% full ({root.get('free_gb', 0)} GB free). "
                       f"Logs take {cleanable['journal']} GB, pm2 logs {cleanable['pm2_logs']} GB."}


def cleanup(what: str) -> Dict[str, Any]:
    if what == "journal":
        res = _run(["journalctl", "--vacuum-time=14d", "--vacuum-size=300M"], 120, sudo=True)
    elif what == "docker":
        res = _run(["docker", "system", "prune", "-f"], 300)  # never volumes: those hold app data
    elif what == "pm2_logs":
        pm2 = next(iter(sorted(HOME.glob(".nvm/versions/node/*/bin/pm2"), reverse=True)), None) or shutil.which("pm2")
        res = _run([str(pm2), "flush"], 60) if pm2 else {"ok": False, "out": "", "err": "pm2 not found"}
    elif what == "dnf_cache":
        res = _run(["dnf", "clean", "packages"], 120, sudo=True)
    elif what == "trash":
        trash = HOME / ".willy-trash"
        if trash.exists():
            for child in trash.iterdir():
                shutil.rmtree(child, ignore_errors=True) if child.is_dir() else child.unlink(missing_ok=True)
        res = {"ok": True, "out": "Emptied ~/.willy-trash", "err": ""}
    else:
        return {"success": False, "error": f"Unknown cleanup '{what}'. Options: {', '.join(CLEANABLE)}."}
    return {"success": res["ok"], "output": (res["out"] + res["err"]).strip()[-1500:],
            "message": f"Cleaned up: {CLEANABLE[what]}." if res["ok"] else f"Cleanup of {what} failed."}


# ------------------------------------------------------------------ network

def network() -> Dict[str, Any]:
    import psutil

    addrs = []
    for line in _run(["ip", "-br", "addr"])["out"].splitlines():
        parts = line.split()
        if parts and parts[0] != "lo":
            addrs.append({"interface": parts[0], "state": parts[1] if len(parts) > 1 else "", "addresses": parts[2:]})
    listening = []
    for line in _run(["ss", "-ltnupH"], 15, sudo=True)["out"].splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        local = parts[4]
        proc = re.search(r'\(\("([^"]+)",pid=(\d+)', line)
        host, _, port = local.rpartition(":")
        listening.append({"proto": parts[0], "address": host.strip("[]"), "port": port,
                          "public": host.strip("[]") in ("0.0.0.0", "*", "::", ""),
                          "process": proc.group(1) if proc else "", "pid": int(proc.group(2)) if proc else None})
    listening.sort(key=lambda x: (not x["public"], int(x["port"]) if x["port"].isdigit() else 0))
    before = psutil.net_io_counters()
    time.sleep(1)
    after = psutil.net_io_counters()
    conns = _run(["ss", "-s"])["out"]
    est = re.search(r"estab (\d+)", conns)
    public = sorted({f"{x['port']}/{x['proto']} ({x['process']})" for x in listening if x["public"]})
    return {"success": True, "interfaces": addrs, "listening": listening,
            "rate": {"down_kbps": round((after.bytes_recv - before.bytes_recv) * 8 / 1000, 1),
                     "up_kbps": round((after.bytes_sent - before.bytes_sent) * 8 / 1000, 1)},
            "established": int(est.group(1)) if est else None,
            "message": f"Open to the internet: {', '.join(public) or 'nothing'}. "
                       f"{est.group(1) if est else '?'} active connections."}


# ------------------------------------------------------------------ security

def security() -> Dict[str, Any]:
    who = [line for line in _run(["who"])["out"].splitlines() if line.strip()]
    recent = [line for line in _run(["last", "-n", "10", "-w", "-F"])["out"].splitlines() if line.strip() and not line.startswith("wtmp")]
    log = _run(["journalctl", "-u", "sshd", "--since", "24 hours ago", "--no-pager", "-o", "cat"], 60, sudo=True)["out"]
    failed = re.findall(r"(?:Failed password|Invalid user \S+|authentication failure).*?from (\d+\.\d+\.\d+\.\d+)", log)
    counts: Dict[str, int] = {}
    for ip in failed:
        counts[ip] = counts.get(ip, 0) + 1
    top = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:8]
    password_auth = _run(["sshd", "-T"], 15, sudo=True)["out"]
    pw = re.search(r"^passwordauthentication (\w+)", password_auth, re.M)
    root_login = re.search(r"^permitrootlogin (\S+)", password_auth, re.M)
    fail2ban = _run(["systemctl", "is-active", "fail2ban"])["out"].strip()
    return {"success": True, "logged_in": who, "recent_logins": recent[:10],
            "failed_ssh_24h": len(failed), "top_attackers": [{"ip": ip, "attempts": n} for ip, n in top],
            "ssh_password_login": pw.group(1) if pw else "unknown", "root_login": root_login.group(1) if root_login else "unknown",
            "fail2ban": fail2ban or "not installed",
            "message": f"{len(failed)} failed SSH logins in 24 h"
                       + (f", most from {top[0][0]} ({top[0][1]})" if top else "")
                       + f". Password login over SSH is {'ON' if pw and pw.group(1) == 'yes' else 'off'}; "
                       f"{len(who)} session(s) logged in now."}


# ------------------------------------------------------------------ services, timers, journal

def services_list() -> Dict[str, Any]:
    units = _run(["systemctl", "list-units", "--type=service", "--all", "--no-legend", "--no-pager", "--plain"], 20)["out"]
    enabled_raw = _run(["systemctl", "list-unit-files", "--type=service", "--no-legend", "--no-pager"], 20)["out"]
    enabled = {}
    for line in enabled_raw.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            enabled[parts[0]] = parts[1]
    rows = []
    for line in units.splitlines():
        parts = line.split(None, 4)
        if len(parts) >= 4 and parts[0].endswith(".service"):
            rows.append({"name": parts[0][:-8], "load": parts[1], "active": parts[2], "sub": parts[3],
                         "description": parts[4] if len(parts) > 4 else "", "boot": enabled.get(parts[0], "")})
    rows.sort(key=lambda r: (r["active"] != "failed", r["active"] != "active", r["name"]))
    failed = [r["name"] for r in rows if r["active"] == "failed"]
    return {"success": True, "services": rows,
            "message": f"{sum(r['active'] == 'active' for r in rows)} services running"
                       + (f"; failed: {', '.join(failed)}" if failed else ", none failed") + "."}


def service_boot(name: str, enable: bool) -> Dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9_.@-]+", name or ""):
        return {"success": False, "error": "Bad service name."}
    res = _run(["systemctl", "enable" if enable else "disable", name], 30, sudo=True)
    return {"success": res["ok"], "message": f"{name} will {'start' if enable else 'not start'} at boot." if res["ok"] else None,
            "error": None if res["ok"] else (res["err"] or res["out"]).strip()[-300:]}


def timers() -> Dict[str, Any]:
    cron = [line for line in _run(["crontab", "-l"])["out"].splitlines() if line.strip() and not line.startswith("#")]
    rows = []
    for line in _run(["systemctl", "list-timers", "--all", "--no-legend", "--no-pager"], 20)["out"].splitlines():
        parts = line.split()
        unit = next((p for p in parts if p.endswith(".timer")), "")
        if unit:
            rows.append({"timer": unit, "line": " ".join(parts)[:160]})
    return {"success": True, "cron": cron, "timers": rows,
            "message": f"{len(cron)} cron job(s) and {len(rows)} system timers."}


def journal(unit: str = "", priority: str = "", since: str = "", grep: str = "", lines: int = 100) -> Dict[str, Any]:
    cmd = ["journalctl", "--no-pager", "-o", "short-iso", "-n", str(max(10, min(int(lines or 100), 500)))]
    if unit:
        if not re.fullmatch(r"[A-Za-z0-9_.@-]+", unit):
            return {"success": False, "error": "Bad unit name."}
        cmd += ["-u", unit]
    if priority in ("emerg", "alert", "crit", "err", "warning", "notice", "info", "debug"):
        cmd += ["-p", priority]
    if since and re.fullmatch(r"[\w :+-]{1,30}", since):
        cmd += ["--since", since]
    if grep:
        cmd += ["-g", grep[:80]]
    res = _run(cmd, 30, sudo=True)
    text = re.sub(r"(token|key|secret|password)=([^&\s\"']+)", r"\1=***", res["out"], flags=re.I)
    return {"success": res["ok"], "logs": text[-12000:], "message": f"{len(text.splitlines())} log lines."}


def reboot() -> Dict[str, Any]:
    res = _run(["shutdown", "-r", "+1", "Reboot requested from Willy"], 15, sudo=True)
    return {"success": res["ok"], "message": "The server will reboot in 1 minute; Willy reconnects when it's back."
            if res["ok"] else None, "error": None if res["ok"] else (res["err"] or res["out"]).strip()}


def cancel_reboot() -> Dict[str, Any]:
    res = _run(["shutdown", "-c"], 15, sudo=True)
    return {"success": res["ok"], "message": "Reboot cancelled." if res["ok"] else None,
            "error": None if res["ok"] else res["err"].strip()}


def execute(action: str, p: Dict[str, Any]) -> Dict[str, Any]:
    table = {
        "sys_updates": lambda: updates(),
        "apply_updates": lambda: apply_updates(bool(p.get("security_only"))),
        "storage": lambda: storage(),
        "cleanup": lambda: cleanup(str(p.get("what") or "")),
        "network": lambda: network(),
        "security": lambda: security(),
        "services_list": lambda: services_list(),
        "service_boot": lambda: service_boot(str(p.get("name") or ""), bool(p.get("enable"))),
        "timers": lambda: timers(),
        "journal": lambda: journal(str(p.get("unit") or ""), str(p.get("priority") or ""), str(p.get("since") or ""),
                                   str(p.get("grep") or ""), int(p.get("lines") or 100)),
        "reboot": lambda: reboot(),
        "cancel_reboot": lambda: cancel_reboot(),
    }
    fn = table.get(action)
    return fn() if fn else {"success": False, "error": f"Unknown admin action '{action}'."}


ADMIN_ACTIONS = {"sys_updates", "apply_updates", "storage", "cleanup", "network", "security", "services_list",
                 "service_boot", "timers", "journal", "reboot", "cancel_reboot"}
