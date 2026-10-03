"""
Pytest bootstrap: keep test runs away from real data, real servers and the real desktop.

- Test reminders, alarms and activity entries go to a temp dir, never server/data.
- A dummy token and a dead local hub URL are pinned before any .env is loaded, so tests
  never pick up the production token from pc_client/.env or connect to the live hub.
- Tests can never type, press keys, click or move the mouse on the user's PC: every
  pyautogui input call and every Windows keybd_event / SendInput / mouse_event is replaced
  for the whole run. (An old router test once typed "4524", "secret123" and "1234" and
  pressed Enter in whatever window the user was working in.)
"""

import os
import tempfile

import pytest

os.environ.setdefault("WILLY_DATA_DIR", tempfile.mkdtemp(prefix="willy_test_data_"))
os.environ["WILLY_REMOTE_TOKEN"] = "pytest-token"
os.environ["WILLY_SERVER_URL"] = "ws://127.0.0.1:9/ws/devices"

PYAUTOGUI_INPUT = ("write", "typewrite", "press", "hotkey", "keyDown", "keyUp", "click", "doubleClick",
                   "tripleClick", "rightClick", "middleClick", "moveTo", "moveRel", "move", "dragTo", "dragRel",
                   "drag", "scroll", "hscroll", "vscroll", "mouseDown", "mouseUp")
WINDOWS_INPUT = ("keybd_event", "SendInput", "mouse_event", "SetCursorPos")


class _BlockedInput:
    """Stands in for a real input function: records the call and does nothing."""

    calls: list = []

    def __init__(self, name: str):
        self.name = name

    def __call__(self, *args, **kwargs):
        _BlockedInput.calls.append((self.name, args, kwargs))
        return 1


@pytest.fixture(autouse=True)
def _no_real_desktop_input(monkeypatch):
    try:
        import pyautogui
    except Exception:  # noqa: BLE001 - not installed / no display
        pyautogui = None
    if pyautogui is not None:
        for name in PYAUTOGUI_INPUT:
            if hasattr(pyautogui, name):
                monkeypatch.setattr(pyautogui, name, _BlockedInput(f"pyautogui.{name}"))
    if os.name == "nt":
        import ctypes

        user32 = ctypes.windll.user32
        for name in WINDOWS_INPUT:
            monkeypatch.setattr(user32, name, _BlockedInput(f"user32.{name}"), raising=False)
        try:
            import win32api

            for name in ("keybd_event", "mouse_event", "SetCursorPos"):
                monkeypatch.setattr(win32api, name, _BlockedInput(f"win32api.{name}"), raising=False)
        except ImportError:
            pass
    yield
