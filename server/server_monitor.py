"""
Health of the machine the hub runs on (the "Willy Server"): CPU, memory, disk, pm2 apps,
system services, Docker containers, the websites nginx serves and their TLS certificates.

A background loop samples every CHECK_EVERY_SEC (sites and certificates less often) and
raises alerts when something stays wrong for a couple of checks in a row - plus an
"all clear" when it recovers. Works on Linux; elsewhere (tests, a dev PC) the parts that
don't apply simply report nothing.
"""

import asyncio
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

CHECK_EVERY_SEC = 60
SITES_EVERY_SEC = 600
SERVICES = ("nginx", "mariadb", "docker")
CPU_HIGH, RAM_HIGH, DISK_HIGH = 90.0, 92.0, 85.0
CERT_WARN_DAYS = 14
CRASH_LOOP_RESTARTS = 3        # pm2 restarts within one window = crash loop
CRASH_WINDOW_SEC = 15 * 60

AlertFn = Callable[[Dict[str, Any]], Any]


def _run(cmd: List[str], timeout: float = 8.0) -> Optional[str]:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return out.stdout if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _pm2_binary() -> Optional[str]:
    found = shutil.which("pm2")
    if found:
        return found
    for candidate in sorted(Path.home().glob(".nvm/versions/node/*/bin/pm2"), reverse=True):
        return str(candidate)
    return None


def pm2_apps() -> List[Dict[str, Any]]:
    pm2 = _pm2_binary()
    raw = _run([pm2, "jlist"]) if pm2 else None
    if not raw:
        return []
    try:
        data = json.loads(raw[raw.index("["):])
    except (ValueError, json.JSONDecodeError):
        return []
    apps = []
    for p in data:
        env = p.get("pm2_env") or {}
        mon = p.get("monit") or {}
        started = (env.get("pm_uptime") or 0) / 1000
        apps.append({"name": p.get("name"), "status": env.get("status"), "restarts": env.get("restart_time", 0),
                     "memory_mb": round((mon.get("memory") or 0) / 1e6), "cpu": mon.get("cpu"),
                     "uptime_sec": round(time.time() - started) if started else None})
    return apps


def services() -> Dict[str, str]:
    out = {}
    for name in SERVICES:
        try:  # is-active exits non-zero for inactive units, so read stdout either way
            state = subprocess.run(["systemctl", "is-active", name], capture_output=True, text=True,
                                   timeout=4).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            state = ""
        out[name] = state or "unknown"
    return out if shutil.which("systemctl") else {}


def failed_units() -> List[str]:
    """systemd services in the failed state (e.g. a certificate renewal that keeps failing)."""
    raw = _run(["systemctl", "--failed", "--no-legend", "--plain", "--type=service"], timeout=6) if shutil.which("systemctl") else None
    return [line.split()[0].removesuffix(".service") for line in (raw or "").splitlines() if line.strip()]


def containers() -> List[Dict[str, str]]:
    raw = _run(["docker", "ps", "-a", "--format", "{{.Names}}\t{{.State}}\t{{.Status}}"]) if shutil.which("docker") else None
    rows = []
    for line in (raw or "").splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            rows.append({"name": parts[0], "state": parts[1], "status": parts[2]})
    return rows


def nginx_domains() -> List[str]:
    domains = set()
    for conf in list(Path("/etc/nginx/conf.d").glob("*.conf")) + [Path("/etc/nginx/nginx.conf")]:
        try:
            text = conf.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for match in re.finditer(r"^\s*server_name\s+([^;]+);", text, re.M):
            for name in match.group(1).split():
                if "." in name and "*" not in name and not name.startswith("$"):
                    domains.add(name.strip().lower())
    return sorted(domains)


def check_site(domain: str) -> Dict[str, Any]:
    url = f"https://{domain}/"
    t0 = time.perf_counter()
    status, error = None, None
    try:
        req = urllib.request.Request(url, method="GET", headers={"User-Agent": "WillyMonitor/1.0"})
        with urllib.request.urlopen(req, timeout=8) as res:
            status = res.status
    except urllib.error.HTTPError as e:
        status = e.code
    except Exception as e:  # noqa: BLE001 - DNS, TLS, timeout...
        error = type(e).__name__ + (f": {getattr(e, 'reason', '')}" if getattr(e, "reason", None) else "")
    ms = round((time.perf_counter() - t0) * 1000)
    # 401/403/404 still mean the server answered; only 5xx or no answer is "down".
    up = status is not None and status < 500
    cert_days = None
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((domain, 443), timeout=6) as sock:
            with ctx.wrap_socket(sock, server_hostname=domain) as tls:
                not_after = tls.getpeercert().get("notAfter")
        expires = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        cert_days = (expires - datetime.now(timezone.utc)).days
    except Exception:  # noqa: BLE001
        pass
    return {"domain": domain, "up": up, "status": status, "ms": ms, "error": error, "cert_days": cert_days}


def system_stats() -> Dict[str, Any]:
    try:
        import psutil
    except ImportError:
        return {}
    vm = psutil.virtual_memory()
    du = psutil.disk_usage("/")
    load = os.getloadavg() if hasattr(os, "getloadavg") else (None, None, None)
    return {
        "cpu_pct": psutil.cpu_percent(interval=1.0),
        "cores": psutil.cpu_count(),
        "load": [round(x, 2) for x in load] if load[0] is not None else None,
        "ram_pct": round(vm.percent, 1),
        "ram_used_mb": round((vm.total - vm.available) / 1e6),
        "ram_total_mb": round(vm.total / 1e6),
        "swap_pct": round(psutil.swap_memory().percent, 1),
        "disk_pct": round(du.percent, 1),
        "disk_free_gb": round(du.free / 1e9, 1),
        "uptime_sec": round(time.time() - psutil.boot_time()),
        "hostname": socket.gethostname(),
    }


def _human_uptime(sec: Optional[float]) -> str:
    if not sec:
        return "unknown"
    days, rem = divmod(int(sec), 86400)
    hours = rem // 3600
    return f"{days} days" if days >= 2 else (f"{days} day {hours} h" if days else f"{hours} h")


class ServerMonitor:
    HISTORY_MAX = 7 * 24 * 60  # one sample a minute for a week

    def __init__(self, on_alert: Optional[AlertFn] = None, name: str = "Willy Server",
                 settings_path: Optional[Path] = None):
        self.name = name
        # [ts, cpu, ram, disk, swap, load1, net_rx_bytes, net_tx_bytes]
        self.history: List[list] = []
        self._history_path = settings_path.with_name("server_history.json") if settings_path else None
        self._history_saved = 0.0
        if self._history_path and self._history_path.exists():
            try:
                self.history = json.loads(self._history_path.read_text(encoding="utf-8"))[-self.HISTORY_MAX:]
            except (OSError, ValueError):
                self.history = []
        self.on_alert = on_alert
        self.settings_path = settings_path
        self.ignored: set = set()  # problem keys the user muted ("site:test.truewilly.com")
        if settings_path and settings_path.exists():
            try:
                self.ignored = set(json.loads(settings_path.read_text(encoding="utf-8")).get("ignored") or [])
            except (OSError, ValueError):
                pass
        self.snapshot: Dict[str, Any] = {}
        self._sites: List[Dict[str, Any]] = []
        self._sites_at = 0.0
        self._strikes: Dict[str, int] = {}      # problem key -> consecutive bad checks
        self._active: Dict[str, str] = {}       # problem key -> message (alert already sent)
        self._restart_history: Dict[str, List[tuple]] = {}
        self._task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------ sampling
    def collect(self, include_sites: bool = False) -> Dict[str, Any]:
        snap = {"name": self.name, "checked_at": time.time(), "system": system_stats(),
                "apps": pm2_apps(), "services": services(), "containers": containers(), "failed_units": failed_units()}
        if include_sites or not self._sites:
            self._sites = [check_site(d) for d in nginx_domains()]
            self._sites_at = time.time()
        snap["sites"] = self._sites
        snap["sites_checked_at"] = self._sites_at
        self.snapshot = snap
        return snap

    # ------------------------------------------------------------------ problems
    def problems(self, snap: Dict[str, Any]) -> Dict[str, str]:
        """Things wrong right now: key -> sentence. Thresholds apply over consecutive checks."""
        found: Dict[str, str] = {}
        s = snap.get("system") or {}
        if (s.get("cpu_pct") or 0) >= CPU_HIGH:
            found["cpu"] = f"CPU is at {s['cpu_pct']:.0f}%."
        if (s.get("ram_pct") or 0) >= RAM_HIGH:
            found["ram"] = f"Memory is at {s['ram_pct']:.0f}% ({s.get('ram_used_mb')} of {s.get('ram_total_mb')} MB)."
        if (s.get("disk_pct") or 0) >= DISK_HIGH:
            found["disk"] = f"Disk is {s['disk_pct']:.0f}% full ({s.get('disk_free_gb')} GB free)."
        now = time.time()
        for app in snap.get("apps") or []:
            name = app.get("name")
            if app.get("status") != "online":
                found[f"app:{name}"] = f"The {name} app is {app.get('status')}."
            hist = self._restart_history.setdefault(name, [])
            hist.append((now, app.get("restarts") or 0))
            hist[:] = [h for h in hist if now - h[0] <= CRASH_WINDOW_SEC]
            if len(hist) >= 2 and hist[-1][1] - hist[0][1] >= CRASH_LOOP_RESTARTS:
                found[f"loop:{name}"] = f"The {name} app restarted {hist[-1][1] - hist[0][1]} times in 15 minutes."
        for svc, state in (snap.get("services") or {}).items():
            if state not in ("active", "unknown"):
                found[f"svc:{svc}"] = f"The {svc} service is {state}."
        for unit in snap.get("failed_units") or []:
            if f"svc:{unit}" not in found:
                found[f"failed:{unit}"] = f"The system service {unit} has failed."
        for c in snap.get("containers") or []:
            if c.get("state") not in ("running",):
                found[f"ctr:{c['name']}"] = f"The {c['name']} container is {c.get('state')} ({c.get('status')})."
        for site in snap.get("sites") or []:
            d = site["domain"]
            if not site.get("up"):
                why = f"HTTP {site['status']}" if site.get("status") else (site.get("error") or "no answer")
                found[f"site:{d}"] = f"{d} is down ({why})."
            days = site.get("cert_days")
            if days is not None and days <= CERT_WARN_DAYS:
                found[f"cert:{d}"] = f"The HTTPS certificate for {d} expires in {days} days."
        return found

    def set_ignored(self, key: str, ignore: bool) -> None:
        (self.ignored.add if ignore else self.ignored.discard)(key)
        self._active.pop(key, None)
        self._strikes.pop(key, None)
        if self.settings_path:
            try:
                self.settings_path.parent.mkdir(parents=True, exist_ok=True)
                self.settings_path.write_text(json.dumps({"ignored": sorted(self.ignored)}), encoding="utf-8")
            except OSError:
                pass

    def current_problems(self) -> Dict[str, str]:
        """Problems right now that aren't muted (for the status page)."""
        return {k: v for k, v in self.problems(self.snapshot).items() if k not in self.ignored} if self.snapshot else {}

    def record(self, snap: Dict[str, Any]) -> None:
        s = snap.get("system") or {}
        if not s:
            return
        try:
            import psutil
            net = psutil.net_io_counters()
            rx, tx = net.bytes_recv, net.bytes_sent
        except Exception:  # noqa: BLE001
            rx = tx = None
        load = (s.get("load") or [None])[0]
        self.history.append([round(time.time()), s.get("cpu_pct"), s.get("ram_pct"), s.get("disk_pct"),
                             s.get("swap_pct"), load, rx, tx])
        del self.history[:-self.HISTORY_MAX]
        if self._history_path and time.time() - self._history_saved > 600:
            try:
                tmp = self._history_path.with_suffix(".tmp")
                tmp.write_text(json.dumps(self.history), encoding="utf-8")
                tmp.replace(self._history_path)
                self._history_saved = time.time()
            except OSError:
                pass

    def history_series(self, hours: float = 24, points: int = 240) -> Dict[str, Any]:
        """Downsampled history for graphs: averages per bucket; network as kbit/s."""
        since = time.time() - hours * 3600
        rows = [r for r in self.history if r[0] >= since]
        if not rows:
            return {"hours": hours, "points": []}
        size = max(1, len(rows) // points)
        out = []
        prev = None
        for i in range(0, len(rows), size):
            chunk = rows[i:i + size]
            avg = lambda k: (round(sum(r[k] for r in chunk if r[k] is not None) / max(1, sum(r[k] is not None for r in chunk)), 1)
                             if any(r[k] is not None for r in chunk) else None)
            last = chunk[-1]
            rx = tx = None
            if prev and last[6] is not None and prev[6] is not None and last[0] > prev[0] and last[6] >= prev[6]:
                secs = last[0] - prev[0]
                rx = round((last[6] - prev[6]) * 8 / 1000 / secs, 1)
                tx = round((last[7] - prev[7]) * 8 / 1000 / secs, 1)
            prev = last
            out.append({"t": chunk[0][0], "cpu": avg(1), "ram": avg(2), "disk": avg(3), "swap": avg(4), "load": avg(5),
                        "rx_kbps": rx, "tx_kbps": tx})
        return {"hours": hours, "points": out}

    async def check(self, include_sites: bool = False) -> List[Dict[str, Any]]:
        snap = await asyncio.to_thread(self.collect, include_sites)
        self.record(snap)
        now_wrong = {k: v for k, v in self.problems(snap).items() if k not in self.ignored}
        alerts = []
        for key, message in now_wrong.items():
            self._strikes[key] = self._strikes.get(key, 0) + 1
            needed = 3 if key in ("cpu",) else 2  # short spikes and blips don't alert
            if key not in self._active and self._strikes[key] >= needed:
                self._active[key] = message
                alerts.append({"key": key, "state": "problem", "message": message})
        for key in list(self._strikes):
            if key not in now_wrong:
                self._strikes.pop(key, None)
                if key in self._active:
                    self._active.pop(key)
                    alerts.append({"key": key, "state": "resolved", "message": _resolved_text(key)})
        # One notification per check and kind, not one per site.
        for state in ("problem", "resolved"):
            batch = [a for a in alerts if a["state"] == state]
            if not batch or not self.on_alert:
                continue
            combined = batch[0] if len(batch) == 1 else {
                "key": ",".join(a["key"] for a in batch), "state": state,
                "message": " ".join(a["message"] for a in batch)}
            try:
                res = self.on_alert(combined)
                if asyncio.iscoroutine(res):
                    await res
            except Exception as e:  # noqa: BLE001
                print(f"[ServerMonitor] alert delivery failed: {e}")
        return alerts

    async def run(self) -> None:
        await asyncio.sleep(5)
        while True:
            try:
                due_sites = time.time() - self._sites_at >= SITES_EVERY_SEC
                await self.check(include_sites=due_sites)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                print(f"[ServerMonitor] check failed: {e}")
            await asyncio.sleep(CHECK_EVERY_SEC)

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self.run())

    # ------------------------------------------------------------------ text
    def summary(self) -> str:
        """One or two spoken sentences."""
        snap = self.snapshot
        if not snap:
            return "I haven't checked the server yet; give me a moment."
        s = snap.get("system") or {}
        issues = list(self.current_problems().values())
        apps = snap.get("apps") or []
        sites = snap.get("sites") or []
        up_sites = sum(1 for x in sites if x.get("up"))
        head = (f"Your server is {'healthy' if not issues else 'having trouble'}: CPU {s.get('cpu_pct', 0):.0f}%, "
                f"memory {s.get('ram_pct', 0):.0f}%, disk {s.get('disk_pct', 0):.0f}%, up {_human_uptime(s.get('uptime_sec'))}. "
                f"{sum(1 for a in apps if a.get('status') == 'online')} of {len(apps)} apps running, "
                f"{up_sites} of {len(sites)} sites up.")
        return head + (" " + " ".join(issues[:3]) if issues else "")

    def live_line(self) -> str:
        s = (self.snapshot or {}).get("system") or {}
        if not s:
            return ""
        issues = list(self._active.values())
        return (f"- Server '{self.name}': ONLINE. CPU {s.get('cpu_pct', 0):.0f}%, RAM {s.get('ram_pct', 0):.0f}%, "
                f"disk {s.get('disk_pct', 0):.0f}%, up {_human_uptime(s.get('uptime_sec'))}"
                + (f". Problems: {' '.join(issues)}" if issues else ". No problems."))


def _resolved_text(key: str) -> str:
    kind, _, name = key.partition(":")
    return {
        "cpu": "CPU is back to normal.", "ram": "Memory use is back to normal.", "disk": "Disk space is OK again.",
        "app": f"The {name} app is running again.", "loop": f"The {name} app has stopped crash-looping.",
        "svc": f"The {name} service is running again.", "ctr": f"The {name} container is running again.",
        "site": f"{name} is back up.", "cert": f"The certificate for {name} was renewed.",
        "failed": f"The {name} service is OK again.",
    }.get(kind, "Resolved.")


def tail_app_log(app: str, lines: int = 40) -> str:
    """Last lines of a pm2 app's logs, with secrets in URLs masked."""
    safe = re.sub(r"[^A-Za-z0-9_.-]", "", app or "")
    if not safe:
        return ""
    texts = []
    for kind in ("out", "error"):
        path = Path.home() / ".pm2" / "logs" / f"{safe}-{kind}.log"
        if path.exists():
            raw = _run(["tail", "-n", str(max(5, min(lines, 200))), str(path)]) or ""
            texts.append(f"--- {kind} ---\n{raw}")
    text = "\n".join(texts)
    return re.sub(r"(token|key|secret|password)=([^&\s\"']+)", r"\1=***", text, flags=re.I)
