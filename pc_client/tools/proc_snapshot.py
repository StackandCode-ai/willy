"""
Fast whole-system process snapshot for Windows.

psutil queries processes one at a time, and for protected processes it falls back to a
full system scan per process - on a busy machine listing ~600 processes can take 5-10 s.
A single NtQuerySystemInformation(SystemProcessInformation) call returns name, PID,
working set and CPU times for every process at once in a few milliseconds.
"""

import sys
import time
import ctypes
from typing import Dict, List, Optional, Tuple

_SYSTEM_PROCESS_INFORMATION_CLASS = 5
_STATUS_INFO_LENGTH_MISMATCH = 0xC0000004


class _UNICODE_STRING(ctypes.Structure):
    _fields_ = [
        ("Length", ctypes.c_ushort),
        ("MaximumLength", ctypes.c_ushort),
        ("Buffer", ctypes.c_void_p),
    ]


class _SYSTEM_PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("NextEntryOffset", ctypes.c_ulong),
        ("NumberOfThreads", ctypes.c_ulong),
        ("WorkingSetPrivateSize", ctypes.c_longlong),
        ("HardFaultCount", ctypes.c_ulong),
        ("NumberOfThreadsHighWatermark", ctypes.c_ulong),
        ("CycleTime", ctypes.c_ulonglong),
        ("CreateTime", ctypes.c_longlong),
        ("UserTime", ctypes.c_longlong),
        ("KernelTime", ctypes.c_longlong),
        ("ImageName", _UNICODE_STRING),
        ("BasePriority", ctypes.c_long),
        ("UniqueProcessId", ctypes.c_void_p),
        ("InheritedFromUniqueProcessId", ctypes.c_void_p),
        ("HandleCount", ctypes.c_ulong),
        ("SessionId", ctypes.c_ulong),
        ("UniqueProcessKey", ctypes.c_void_p),
        ("PeakVirtualSize", ctypes.c_size_t),
        ("VirtualSize", ctypes.c_size_t),
        ("PageFaultCount", ctypes.c_ulong),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivatePageCount", ctypes.c_size_t),
    ]


# pid -> (name, create_time, cpu_time_100ns, working_set_bytes, threads)
RawSnapshot = Dict[int, Tuple[str, int, int, int, int]]


def raw_snapshot() -> Optional[RawSnapshot]:
    """All processes in one kernel call, or None when unavailable (non-Windows / failure)."""
    if sys.platform != "win32":
        return None
    try:
        ntdll = ctypes.WinDLL("ntdll")
        query = ntdll.NtQuerySystemInformation
        query.restype = ctypes.c_ulong
        query.argtypes = [ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)]

        size = 1 << 20
        for _ in range(6):
            buf = ctypes.create_string_buffer(size)
            needed = ctypes.c_ulong(0)
            status = query(_SYSTEM_PROCESS_INFORMATION_CLASS, buf, size, ctypes.byref(needed))
            if status == _STATUS_INFO_LENGTH_MISMATCH:
                size = max(size * 2, needed.value + (64 << 10))
                continue
            if status != 0:
                return None
            break
        else:
            return None

        result: RawSnapshot = {}
        offset = 0
        while True:
            info = _SYSTEM_PROCESS_INFORMATION.from_buffer(buf, offset)
            pid = info.UniqueProcessId or 0
            name_len = info.ImageName.Length // 2
            if info.ImageName.Buffer and name_len:
                name = ctypes.wstring_at(info.ImageName.Buffer, name_len)
            else:
                name = "System Idle Process" if pid == 0 else ""
            result[pid] = (
                name,
                int(info.CreateTime),
                int(info.UserTime) + int(info.KernelTime),
                int(info.WorkingSetSize),
                int(info.NumberOfThreads),
            )
            if info.NextEntryOffset == 0:
                break
            offset += info.NextEntryOffset
        return result
    except Exception:
        return None


class ProcessSampler:
    """Turns successive snapshots into per-process CPU percentages (share of the whole machine)."""

    def __init__(self, cpu_count: int):
        self.cpu_count = max(1, cpu_count)
        self._prev: Optional[RawSnapshot] = None
        self._prev_t = 0.0

    def sample(self) -> Optional[List[Dict[str, float]]]:
        snap = raw_snapshot()
        if snap is None:
            return None
        now = time.monotonic()
        prev, prev_t = self._prev, self._prev_t
        self._prev, self._prev_t = snap, now
        if prev is None:
            # First call: take a short second sample so CPU numbers are meaningful.
            time.sleep(0.25)
            return self.sample()

        wall_100ns = max(1.0, (now - prev_t) * 1e7) * self.cpu_count
        rows = []
        for pid, (name, created, cpu_time, working_set, threads) in snap.items():
            if pid == 0:
                continue
            before = prev.get(pid)
            cpu = 0.0
            if before and before[1] == created:
                cpu = max(0.0, (cpu_time - before[2]) / wall_100ns * 100.0)
            rows.append({
                "pid": pid,
                "name": name,
                "cpu": round(min(cpu, 100.0), 1),
                "mem_mb": round(working_set / (1024 * 1024), 1),
                "threads": threads,
            })
        return rows
