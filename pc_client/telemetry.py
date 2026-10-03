"""
Willy PC Client Telemetry Collector.

Collects live battery, CPU, RAM, disk, network throughput, audio, active window,
user idle time and top processes. Cheap readings are sampled on every heartbeat;
slower ones (Wi-Fi name, IP address, process list) are cached and refreshed on
their own schedule so a heartbeat stays in the low milliseconds.
"""

import os
import sys
import time
import socket
import platform
import threading
import subprocess
from typing import Any, Dict, List, Optional

import psutil

from pc_client.tools.proc_snapshot import ProcessSampler

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
TOP_PROCESS_COUNT = 8

_IGNORED_PROCESSES = {"system idle process", "idle", "system", "registry", "memory compression", "secure system"}


def get_active_window_title() -> str:
    """Returns the title of the currently focused window on Windows."""
    if sys.platform != "win32":
        return "N/A"
    try:
        import win32gui
        hwnd = win32gui.GetForegroundWindow()
        if hwnd:
            title = win32gui.GetWindowText(hwnd)
            return title.strip() if title else "Desktop"
    except Exception:
        pass
    return "Desktop"


def _active_process_name() -> Optional[str]:
    if sys.platform != "win32":
        return None
    try:
        import win32gui
        import win32process
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return None
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        return psutil.Process(pid).name()
    except Exception:
        return None


def _idle_seconds() -> Optional[float]:
    """Seconds since the last keyboard/mouse input (Windows)."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(info)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        millis = (ctypes.windll.kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF
        return round(millis / 1000.0, 1)
    except Exception:
        return None


def _local_ip() -> Optional[str]:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except Exception:
        return None


def _wifi_ssid() -> Optional[str]:
    if sys.platform != "win32":
        return None
    try:
        out = subprocess.run(
            ["netsh", "wlan", "show", "interfaces"],
            capture_output=True, text=True, timeout=3,
            creationflags=CREATE_NO_WINDOW, errors="replace",
        ).stdout
        for line in out.splitlines():
            key, _, value = line.partition(":")
            if key.strip().lower() == "ssid" and value.strip():
                return value.strip()
    except Exception:
        pass
    return None


def _tz_offset_minutes() -> int:
    offset = time.localtime().tm_gmtoff
    return int(offset // 60) if offset is not None else 0


def _system_drive() -> str:
    return (os.environ.get("SystemDrive", "C:") + "\\") if sys.platform == "win32" else "/"


class TelemetryCollector:
    """Stateful collector (keeps network counters and per-process CPU baselines)."""

    def __init__(self, background: bool = True):
        self._lock = threading.RLock()
        self._net_prev: Optional[tuple] = None
        self._cpu_count = psutil.cpu_count(logical=True) or 1
        self._sampler = ProcessSampler(self._cpu_count)
        self._psutil_procs: Dict[int, psutil.Process] = {}
        # Slow readings, refreshed by the background thread and read without blocking.
        self._slow: Dict[str, Any] = {
            "top_processes": [], "process_rows": [], "volume": {},
            "ip_address": None, "wifi_ssid": None, "brightness": None,
        }
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._background = background
        # psutil.cpu_percent() keeps one baseline per calling thread, and collect() runs on
        # whichever pool thread is free, so system CPU is computed from our own baseline.
        self._cpu_prev: Optional[tuple] = self._cpu_totals()
        self._last_cpu_pct: Optional[float] = None

    # ------------------------------------------------------------ background

    def start(self) -> None:
        if self._thread is None and self._background:
            self._thread = threading.Thread(target=self._slow_loop, name="willy-telemetry", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def note_brightness(self, level: int) -> None:
        """Reflects a brightness change right away instead of on the next WMI poll."""
        self._slow["brightness"] = level

    def _slow_loop(self) -> None:
        due = {"volume": 0.0, "procs": 0.0, "ip": 0.0, "ssid": 0.0, "brightness": 0.0}
        while not self._stop.is_set():
            now = time.monotonic()
            try:
                if now >= due["volume"]:
                    self._slow["volume"] = _read_volume()
                    due["volume"] = now + 2
                if now >= due["procs"]:
                    rows = self._process_rows()
                    self._slow["process_rows"] = rows
                    self._slow["top_processes"] = _rank(rows, "cpu", TOP_PROCESS_COUNT)
                    due["procs"] = now + 4
                if now >= due["ip"]:
                    self._slow["ip_address"] = _local_ip()
                    due["ip"] = now + 30
                if now >= due["ssid"]:
                    self._slow["wifi_ssid"] = _wifi_ssid()
                    due["ssid"] = now + 60
                if now >= due["brightness"]:
                    self._slow["brightness"] = _read_brightness()
                    due["brightness"] = now + 15
            except Exception:
                pass
            self._stop.wait(0.5)

    # ------------------------------------------------------------- processes

    def _process_rows(self) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._sampler.sample()
            if rows is None:
                rows = self._psutil_rows()
        return [r for r in rows if r["name"] and r["name"].lower() not in _IGNORED_PROCESSES]

    def _psutil_rows(self) -> List[Dict[str, Any]]:
        """Portable (slower) fallback when the NT snapshot is unavailable."""
        seen, rows = set(), []
        for proc in psutil.process_iter(["pid", "name", "memory_info"]):
            pid = proc.info["pid"]
            seen.add(pid)
            tracked = self._psutil_procs.setdefault(pid, proc)
            try:
                cpu = tracked.cpu_percent(interval=None) / self._cpu_count
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
            mem = proc.info.get("memory_info")
            rows.append({
                "pid": pid,
                "name": proc.info.get("name") or "",
                "cpu": round(cpu, 1),
                "mem_mb": round(mem.rss / (1024 * 1024), 1) if mem else 0.0,
            })
        for pid in list(self._psutil_procs):
            if pid not in seen:
                self._psutil_procs.pop(pid, None)
        return rows

    def top_processes(self, limit: int = TOP_PROCESS_COUNT, sort_by: str = "cpu", fresh: bool = True) -> List[Dict[str, Any]]:
        """Busiest processes; CPU is a share of the whole machine like Task Manager."""
        rows = self._process_rows() if fresh or not self._slow["process_rows"] else self._slow["process_rows"]
        return _rank(rows, sort_by, limit)

    # ------------------------------------------------------------- heartbeat

    @staticmethod
    def _cpu_totals() -> Optional[tuple]:
        try:
            t = psutil.cpu_times()
        except Exception:
            return None
        return (sum(t), getattr(t, "idle", 0.0) + getattr(t, "iowait", 0.0))

    def _system_cpu_pct(self) -> Optional[float]:
        """Whole-machine CPU % since the previous heartbeat, independent of the calling thread."""
        now = self._cpu_totals()
        prev, self._cpu_prev = self._cpu_prev, now
        if now is None or prev is None:
            return self._last_cpu_pct
        total = now[0] - prev[0]
        if total <= 0:
            return self._last_cpu_pct
        busy = total - (now[1] - prev[1])
        self._last_cpu_pct = round(max(0.0, min(100.0, busy / total * 100.0)), 1)
        return self._last_cpu_pct

    def _network_rates(self) -> tuple:
        try:
            counters = psutil.net_io_counters()
        except Exception:
            return None, None
        now = time.monotonic()
        prev, self._net_prev = self._net_prev, (now, counters.bytes_sent, counters.bytes_recv)
        if not prev or now - prev[0] <= 0:
            return None, None
        dt = now - prev[0]
        up = max(0, counters.bytes_sent - prev[1]) / dt / 1024
        down = max(0, counters.bytes_recv - prev[2]) / dt / 1024
        return round(up, 1), round(down, 1)

    def collect(self) -> Dict[str, Any]:
        """Heartbeat reading: fast fields sampled now, slow ones from the background cache."""
        self.start()
        data: Dict[str, Any] = {}

        try:
            battery = psutil.sensors_battery()
        except Exception:
            battery = None
        if battery:
            data["battery_pct"] = round(battery.percent)
            data["is_charging"] = bool(battery.power_plugged)
            secs = battery.secsleft
            data["battery_secs_left"] = secs if isinstance(secs, int) and secs >= 0 else None
        else:
            data.update(battery_pct=None, is_charging=None, battery_secs_left=None)

        with self._lock:
            data["cpu_pct"] = self._system_cpu_pct()
        try:
            freq = psutil.cpu_freq()
            data["cpu_freq_mhz"] = round(freq.current) if freq else None
        except Exception:
            data["cpu_freq_mhz"] = None

        try:
            ram = psutil.virtual_memory()
            data["ram_pct"] = round(ram.percent)
            data["ram_used_gb"] = round((ram.total - ram.available) / (1024 ** 3), 1)
            data["ram_total_gb"] = round(ram.total / (1024 ** 3), 1)
        except Exception:
            data["ram_pct"] = None

        try:
            disk = psutil.disk_usage(_system_drive())
            data["disk_free_gb"] = round(disk.free / (1024 ** 3), 1)
            data["disk_total_gb"] = round(disk.total / (1024 ** 3), 1)
            data["disk_pct"] = round(disk.percent)
        except Exception:
            data["disk_free_gb"] = None

        with self._lock:
            data["net_up_kbps"], data["net_down_kbps"] = self._network_rates()

        data["active_window"] = get_active_window_title()
        data["active_process"] = _active_process_name()
        data["idle_sec"] = _idle_seconds()

        volume = self._slow["volume"] or {}
        data["volume_level"] = volume.get("level")
        data["is_muted"] = volume.get("muted")
        data["ip_address"] = self._slow["ip_address"]
        data["wifi_ssid"] = self._slow["wifi_ssid"]
        data["brightness"] = self._slow["brightness"]
        try:
            data["uptime_hours"] = round((time.time() - psutil.boot_time()) / 3600, 1)
        except Exception:
            data["uptime_hours"] = None
        rows = self._slow["process_rows"]
        data["process_count"] = len(rows) if rows else None
        data["top_processes"] = self._slow["top_processes"]
        data["tz_offset_min"] = _tz_offset_minutes()
        return data


def _rank(rows: List[Dict[str, Any]], sort_by: str, limit: int) -> List[Dict[str, Any]]:
    if sort_by == "memory":
        key = lambda r: (r["mem_mb"], r["cpu"])  # noqa: E731
    else:
        key = lambda r: (r["cpu"], r["mem_mb"])  # noqa: E731
    return sorted(rows, key=key, reverse=True)[:max(1, limit)]


def _read_brightness() -> Optional[int]:
    """Built-in panel brightness via WMI (~10 ms); None where unsupported, e.g. desktop monitors."""
    if sys.platform != "win32":
        return None
    try:
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()  # once per thread; repeated calls are harmless
        wmi = win32com.client.GetObject("winmgmts:root/WMI")
        for item in wmi.ExecQuery("SELECT CurrentBrightness FROM WmiMonitorBrightness"):
            return int(item.CurrentBrightness)
    except Exception:
        pass
    return None


def set_brightness(level: int) -> bool:
    """Sets the built-in panel brightness via WMI; False where unsupported (desktop monitors)."""
    if sys.platform != "win32":
        return False
    try:
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        wmi = win32com.client.GetObject("winmgmts:root/WMI")
        done = False
        for item in wmi.ExecQuery("SELECT * FROM WmiMonitorBrightnessMethods"):
            # Calling item.WmiSetBrightness(...) directly fails with "Invalid parameter" (late-bound
            # arg order); typed InParameters work.
            params = item.Methods_("WmiSetBrightness").InParameters.SpawnInstance_()
            params.Properties_.Item("Timeout").Value = 1
            params.Properties_.Item("Brightness").Value = int(level)
            item.ExecMethod_("WmiSetBrightness", params)
            done = True
        return done
    except Exception:
        return False


def _read_volume() -> Dict[str, Any]:
    from pc_client.tools.audio_tools import get_volume
    state = get_volume()
    return state if state.get("success") else {}


def _cpu_model() -> str:
    if sys.platform == "win32":
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
        except Exception:
            pass
    return platform.processor() or "Unknown CPU"


def _windows_name() -> str:
    if sys.platform != "win32":
        return f"{platform.system()} {platform.release()}"
    try:
        build = sys.getwindowsversion().build
        return f"Windows {'11' if build >= 22000 else platform.release()}"
    except Exception:
        return f"Windows {platform.release()}"


def _gpu_names() -> List[str]:
    if sys.platform != "win32":
        return []
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "(Get-CimInstance Win32_VideoController).Name"],
            capture_output=True, text=True, timeout=8,
            creationflags=CREATE_NO_WINDOW, errors="replace",
        ).stdout
        return [line.strip() for line in out.splitlines() if line.strip()]
    except Exception:
        return []


def _screens() -> Dict[str, Any]:
    if sys.platform != "win32":
        return {}
    try:
        import win32api
        monitors = win32api.EnumDisplayMonitors()
        primary = f"{win32api.GetSystemMetrics(0)}x{win32api.GetSystemMetrics(1)}"
        return {"monitors": len(monitors), "primary_resolution": primary}
    except Exception:
        return {}


_specs_cache: Optional[Dict[str, Any]] = None


def collect_static_specs(include_slow: bool = True) -> Dict[str, Any]:
    """Hardware / OS details that don't change while the client runs (cached after first call)."""
    global _specs_cache
    if _specs_cache is not None:
        return dict(_specs_cache)  # the cached copy is the complete set
    specs = _collect_static_specs(include_slow)
    if include_slow:
        _specs_cache = specs
    return dict(specs)


def _collect_static_specs(include_slow: bool) -> Dict[str, Any]:
    specs: Dict[str, Any] = {
        "os": _windows_name(),
        "os_version": platform.version(),
        "hostname": socket.gethostname(),
        "machine": platform.machine(),
        "cpu_model": _cpu_model(),
        "cpu_cores": psutil.cpu_count(logical=False),
        "cpu_threads": psutil.cpu_count(logical=True),
        "python": platform.python_version(),
    }
    try:
        specs["user"] = os.getlogin()
    except Exception:
        specs["user"] = os.environ.get("USERNAME")
    try:
        specs["ram_total_gb"] = round(psutil.virtual_memory().total / (1024 ** 3), 1)
    except Exception:
        pass
    try:
        specs["boot_time"] = psutil.boot_time()
    except Exception:
        pass
    try:
        specs["has_battery"] = psutil.sensors_battery() is not None
    except Exception:
        specs["has_battery"] = False
    specs.update(_screens())
    if include_slow:
        gpus = _gpu_names()
        if gpus:
            specs["gpu"] = gpus
    return specs


_default_collector: Optional[TelemetryCollector] = None


def get_collector() -> TelemetryCollector:
    global _default_collector
    if _default_collector is None:
        _default_collector = TelemetryCollector()
    return _default_collector


def collect_pc_telemetry() -> Dict[str, Any]:
    """Collects lightweight live telemetry from the physical Windows PC."""
    data = get_collector().collect()
    data.setdefault("last_ping_ms", None)
    return data
