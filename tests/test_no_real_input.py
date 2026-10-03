"""The test run must never reach the real keyboard or mouse (conftest.py blocks it)."""

import ctypes
import os

import pytest


def test_keyboard_and_mouse_are_blocked_during_tests():
    import conftest
    import pyautogui

    before = len(conftest._BlockedInput.calls)
    pyautogui.press("f24")  # F24 does nothing in any app, even if the guard ever broke
    pyautogui.hotkey("ctrl", "f24")
    assert [c[0] for c in conftest._BlockedInput.calls[before:]] == ["pyautogui.press", "pyautogui.hotkey"]


@pytest.mark.skipif(os.name != "nt", reason="Windows only")
def test_windows_key_events_are_blocked_during_tests():
    import conftest

    before = len(conftest._BlockedInput.calls)
    ctypes.windll.user32.keybd_event(0x87, 0, 0, 0)  # VK_F24
    assert conftest._BlockedInput.calls[before:][0][0] == "user32.keybd_event"
