"""
One Willy PC app per Windows session.

A named mutex marks the running app. Launching Willy again (Start menu, desktop, exe) asks
the running one to bring its window to the front through a named event and exits, so a
second copy never fights the first for this PC's hub connection. `--quit` uses a second
event to close the running app cleanly (the exe build does this before replacing files).
"""

import ctypes
import threading
from ctypes import wintypes
from typing import Callable, Dict, Optional

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
_k32.CreateMutexW.restype = wintypes.HANDLE
_k32.CreateEventW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR)
_k32.CreateEventW.restype = wintypes.HANDLE
_k32.OpenEventW.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
_k32.OpenEventW.restype = wintypes.HANDLE
_k32.SetEvent.argtypes = (wintypes.HANDLE,)
_k32.SetEvent.restype = wintypes.BOOL
_k32.WaitForMultipleObjects.argtypes = (wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE), wintypes.BOOL,
                                        wintypes.DWORD)
_k32.WaitForMultipleObjects.restype = wintypes.DWORD
_k32.CloseHandle.argtypes = (wintypes.HANDLE,)
_k32.CloseHandle.restype = wintypes.BOOL

ERROR_ACCESS_DENIED = 5
ERROR_ALREADY_EXISTS = 183
EVENT_MODIFY_STATE = 0x0002
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 0x102
ASFW_ANY = -1  # AllowSetForegroundWindow: any process


class SingleInstance:
    SHOW, QUIT = "Show", "Quit"

    def __init__(self, name: str = "WillyPC"):
        self.name = name
        self._mutex: Optional[int] = None
        self._events: Dict[str, int] = {}
        self._stop = threading.Event()
        self._waiter: Optional[threading.Thread] = None

    def _object(self, kind: str) -> str:
        return f"Local\\{self.name}.{kind}"

    def acquire(self) -> bool:
        """True when no other Willy PC app runs in this session (this one then owns the name)."""
        handle = _k32.CreateMutexW(None, False, self._object("Instance"))
        error = ctypes.get_last_error()
        if not handle:
            # Access denied: an elevated copy holds it. Anything else: can't tell, so run.
            return error != ERROR_ACCESS_DENIED
        if error == ERROR_ALREADY_EXISTS:
            _k32.CloseHandle(handle)
            return False
        self._mutex = handle
        for kind in (self.SHOW, self.QUIT):
            event = _k32.CreateEventW(None, False, False, self._object(kind))  # auto-reset
            if event:
                self._events[kind] = event
        return True

    def signal(self, kind: str = SHOW) -> bool:
        """Asks the running app to show its window (SHOW) or to shut down (QUIT)."""
        handle = _k32.OpenEventW(EVENT_MODIFY_STATE, False, self._object(kind))
        if not handle:
            return False
        try:
            if kind == self.SHOW:
                # Let the running app take the foreground: this launch was started by the user.
                ctypes.windll.user32.AllowSetForegroundWindow(ASFW_ANY)
            return bool(_k32.SetEvent(handle))
        finally:
            _k32.CloseHandle(handle)

    def listen(self, on_show: Callable[[], None], on_quit: Callable[[], None]) -> None:
        """Calls on_show / on_quit (from a daemon thread) when another launch signals."""
        if self._waiter is not None or len(self._events) < 2:
            return
        handles = (wintypes.HANDLE * 2)(self._events[self.SHOW], self._events[self.QUIT])

        def wait() -> None:
            while not self._stop.is_set():
                result = _k32.WaitForMultipleObjects(2, handles, False, 400)
                if self._stop.is_set():
                    break
                if result == WAIT_OBJECT_0:
                    on_show()
                elif result == WAIT_OBJECT_0 + 1:
                    on_quit()
                elif result != WAIT_TIMEOUT:
                    break

        self._waiter = threading.Thread(target=wait, name="willy-instance", daemon=True)
        self._waiter.start()

    def release(self) -> None:
        """Gives up the name so Willy can start again right away."""
        self._stop.set()
        waiter, self._waiter = self._waiter, None
        if waiter is not None and waiter is not threading.current_thread():
            waiter.join(timeout=1.0)
        if waiter is None or not waiter.is_alive():
            for handle in self._events.values():
                _k32.CloseHandle(handle)
            self._events.clear()
        if self._mutex:
            _k32.CloseHandle(self._mutex)
            self._mutex = None
