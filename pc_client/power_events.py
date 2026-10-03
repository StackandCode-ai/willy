"""
Windows power and session notifications for the PC client.

A hidden top-level window on its own thread (message-only windows don't receive
broadcasts) gets WM_POWERBROADCAST (sleep / wake) and WM_ENDSESSION (shutdown, restart,
sign-out). The client uses them to tell the hub *why* the PC is going offline, and to
reconnect the moment the PC wakes up instead of waiting for a retry timer.
"""

import ctypes
import threading
from ctypes import wintypes
from typing import Callable, Optional

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_QUERYENDSESSION = 0x0011
WM_ENDSESSION = 0x0016
WM_POWERBROADCAST = 0x0218
PBT_APMSUSPEND = 0x0004
PBT_APMRESUMESUSPEND = 0x0007
PBT_APMRESUMEAUTOMATIC = 0x0012
ENDSESSION_LOGOFF = 0x80000000

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH), ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


# Private library handles: other code (pystray) sets its own prototypes on the shared windll ones.
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_user32.DefWindowProcW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
_user32.DefWindowProcW.restype = LRESULT
_user32.RegisterClassW.argtypes = (ctypes.POINTER(WNDCLASSW),)
_user32.RegisterClassW.restype = wintypes.ATOM
_user32.CreateWindowExW.argtypes = (wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                    wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID)
_user32.CreateWindowExW.restype = wintypes.HWND
_user32.GetMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT)
_user32.GetMessageW.restype = wintypes.BOOL
_user32.TranslateMessage.argtypes = (ctypes.POINTER(wintypes.MSG),)
_user32.DispatchMessageW.argtypes = (ctypes.POINTER(wintypes.MSG),)
_user32.DispatchMessageW.restype = LRESULT
_user32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
_user32.PostMessageW.restype = wintypes.BOOL
_user32.DestroyWindow.argtypes = (wintypes.HWND,)
_user32.PostQuitMessage.argtypes = (ctypes.c_int,)
_user32.UnregisterClassW.argtypes = (wintypes.LPCWSTR, wintypes.HINSTANCE)
_kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
_kernel32.GetModuleHandleW.restype = wintypes.HMODULE


class PowerEvents:
    """on_suspend() / on_resume() / on_session_end(kind: 'shutdown' | 'logoff') run on the
    listener thread; keep them short (the system waits for sleep / shutdown handlers)."""

    def __init__(self, on_suspend: Callable[[], None], on_resume: Callable[[], None],
                 on_session_end: Callable[[str], None]):
        self.on_suspend = on_suspend
        self.on_resume = on_resume
        self.on_session_end = on_session_end
        self.hwnd: Optional[int] = None
        self._proc = WNDPROC(self._wndproc)  # must outlive the window
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()

    def start(self) -> bool:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="willy-power", daemon=True)
            self._thread.start()
        self._ready.wait(2.0)
        return self.hwnd is not None

    def stop(self) -> None:
        if self.hwnd:
            _user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)

    def _run(self) -> None:
        # One window class per listener: a class carries its window procedure, so a shared
        # name would route a second listener's messages to the first one's callbacks.
        class_name = f"WillyPowerEvents{id(self):x}"
        instance = _kernel32.GetModuleHandleW(None)
        registered = False
        try:
            wc = WNDCLASSW()
            wc.lpfnWndProc = self._proc
            wc.hInstance = instance
            wc.lpszClassName = class_name
            registered = bool(_user32.RegisterClassW(ctypes.byref(wc)))
            if registered:
                # A plain hidden top-level window (never shown): it receives power/session broadcasts.
                self.hwnd = _user32.CreateWindowExW(0, class_name, "Willy power events", 0, 0, 0, 0, 0,
                                                    None, None, instance, None)
        finally:
            self._ready.set()
        if self.hwnd:
            msg = wintypes.MSG()
            while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                _user32.TranslateMessage(ctypes.byref(msg))
                _user32.DispatchMessageW(ctypes.byref(msg))
        self.hwnd = None
        if registered:
            _user32.UnregisterClassW(class_name, instance)

    @staticmethod
    def _safe(fn: Callable, *args) -> None:
        try:
            fn(*args)
        except Exception:
            pass

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_POWERBROADCAST:
            if wparam == PBT_APMSUSPEND:
                self._safe(self.on_suspend)
            elif wparam in (PBT_APMRESUMEAUTOMATIC, PBT_APMRESUMESUSPEND):
                self._safe(self.on_resume)
            return 1
        if msg == WM_QUERYENDSESSION:
            return 1  # never block a shutdown
        if msg == WM_ENDSESSION:
            if wparam:
                self._safe(self.on_session_end, "logoff" if (lparam or 0) & ENDSESSION_LOGOFF else "shutdown")
            return 0
        if msg == WM_CLOSE:
            _user32.DestroyWindow(hwnd)
            return 0
        if msg == WM_DESTROY:
            _user32.PostQuitMessage(0)
            return 0
        return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)
