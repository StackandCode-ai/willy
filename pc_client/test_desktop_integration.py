"""
Tests for the Windows desktop integration: logo and tray icon rendering, tray status
updates, Start with Windows (against a scratch registry key, never the real Run key) and
the single-instance handshake. Nothing here shows a window or a tray icon.
"""

import sys
import threading
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest
from PIL import Image

from pc_client import autostart
from pc_client.brand import STATUS_COLORS, _rgb, logo_image, save_ico, status_icon
from pc_client.tray import TIP_LIMIT, TrayIcon, menu_header, tooltip

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only APIs")


# ------------------------------------------------------------------ brand

def test_logo_and_status_icons_render():
    logo = logo_image(64)
    assert logo.size == (64, 64) and logo.mode == "RGBA"
    assert logo.getpixel((0, 0))[3] == 0, "rounded corners are transparent"
    assert logo.getpixel((32, 3))[3] == 255
    for state, color in STATUS_COLORS.items():
        icon = status_icon(state, 64, logo)
        assert icon.size == (64, 64)
        r, g, b, a = icon.getpixel((46, 46))  # middle of the status dot
        assert a == 255
        assert all(abs(x - y) <= 8 for x, y in zip((r, g, b), _rgb(color))), state
    assert status_icon("online", 64, logo).getpixel((12, 12)) == logo.getpixel((12, 12)), "logo left intact"


def test_exe_icon_has_small_and_large_sizes(tmp_path):
    path = save_ico(tmp_path / "willy.ico")
    with Image.open(path) as ico:
        assert ico.format == "ICO"
        sizes = ico.ico.sizes()
    assert {(16, 16), (32, 32), (48, 48), (256, 256)} <= sizes


# ------------------------------------------------------------------- tray

def test_tooltip_and_menu_header_text():
    assert tooltip("online", 62, "truewilly.com") == "Willy PC — Online · 62 ms\ntruewilly.com"
    assert tooltip("connecting") == "Willy PC — Connecting…"
    assert len(tooltip("online", 1, "x" * 300)) == TIP_LIMIT
    assert menu_header("online", "truewilly.com") == "Online · truewilly.com"
    assert menu_header("offline") == "Offline · retrying automatically"
    assert "took over" in menu_header("stopped")


class _FakeIcon:
    def __init__(self):
        self.icon = None
        self.title = None
        self.menu_updates = 0

    def update_menu(self):
        self.menu_updates += 1


def test_tray_status_only_touches_the_shell_when_something_changed():
    tray = TrayIcon(lambda fn, *a: fn(*a), {}, hub="truewilly.com")
    fake = tray._icon = _FakeIcon()
    tray._images = {state: state for state in ("online", "connecting", "offline", "stopped")}

    tray.set_status("online", 62)
    assert fake.icon == "online" and fake.title == "Willy PC — Online · 62 ms\ntruewilly.com"
    assert fake.menu_updates == 1
    fake.icon = fake.title = None
    tray.set_status("online", 62)
    assert fake.icon is None and fake.title is None and fake.menu_updates == 1
    tray.set_status("online", 70)  # latency only changes the tooltip
    assert "70 ms" in fake.title and fake.icon is None and fake.menu_updates == 1
    tray.set_status("something-unknown")
    assert fake.icon == "offline" and fake.menu_updates == 2


def test_tray_menu_actions_are_handed_to_the_app_thread():
    dispatched = []
    tray = TrayIcon(lambda fn, *a: dispatched.append((fn, a)), {"open": "OPEN", "quit": "QUIT"})
    tray._action("open")(None, None)
    tray._action("quit")(None, None)
    tray._action("missing")(None, None)
    assert dispatched == [("OPEN", ()), ("QUIT", ())]


# -------------------------------------------------------------- autostart

@pytest.fixture
def scratch_run_keys(monkeypatch):
    """Points the autostart module at throwaway keys under HKCU\\Software\\WillyPCTests."""
    import winreg

    parent = r"Software\WillyPCTests"
    base = rf"{parent}\{uuid.uuid4().hex}"
    monkeypatch.setattr(autostart, "RUN_KEY", base + r"\Run")
    monkeypatch.setattr(autostart, "APPROVED_KEY", base + r"\Approved")
    for sub in ("Run", "Approved"):
        winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{base}\{sub}").Close()
    yield base
    for key in (rf"{base}\Run", rf"{base}\Approved", base, parent):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
        except OSError:
            pass  # e.g. the parent still holds another test's key


def test_sign_in_commands_start_hidden_in_the_tray(tmp_path):
    exe = tmp_path / "Willy PC" / "WillyPC.exe"
    assert autostart.command_for(exe) == f'"{exe.resolve()}" --tray'
    source = autostart.command_for()
    assert source.endswith('main.py" --tray') and "python" in source.lower()
    assert autostart.launch_command() == source  # pytest isn't a frozen exe


@windows_only
def test_enable_replaces_the_old_assistant_and_clears_a_switched_off_mark(scratch_run_keys):
    import winreg

    hkcu = winreg.HKEY_CURRENT_USER
    with winreg.OpenKey(hkcu, autostart.RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, autostart.LEGACY_VALUE_NAME, 0, winreg.REG_SZ, '"pythonw.exe" "main.py" --mode ui')
    with winreg.OpenKey(hkcu, autostart.APPROVED_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, autostart.VALUE_NAME, 0, winreg.REG_BINARY, b"\x03" + b"\x00" * 11)

    before = autostart.status()
    assert before["enabled"] is False and before["legacy_command"].endswith("--mode ui")

    command = r'"C:\Apps\WillyPC\WillyPC.exe" --tray'
    res = autostart.enable(command=command)
    assert res["success"] and res["replaced_legacy"].endswith("--mode ui")
    after = autostart.status()
    assert after["enabled"] and after["command"] == command and after["legacy_command"] is None
    assert not after["switched_off_in_startup_apps"] and autostart.is_enabled()

    # Switched off later in Task Manager: the entry stays, but it no longer counts as on.
    with winreg.OpenKey(hkcu, autostart.APPROVED_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, autostart.VALUE_NAME, 0, winreg.REG_BINARY, b"\x03" + b"\x00" * 11)
    assert autostart.status()["switched_off_in_startup_apps"] and not autostart.is_enabled()

    assert autostart.disable()["removed"] is True
    assert autostart.status()["command"] is None and not autostart.is_enabled()
    assert autostart.disable()["removed"] is False


# --------------------------------------------------------- single instance

@windows_only
def test_second_launch_signals_the_running_app_instead_of_starting():
    from pc_client.instance import SingleInstance

    name = f"WillyPCTest-{uuid.uuid4().hex}"
    first, second = SingleInstance(name), SingleInstance(name)
    try:
        assert first.acquire() is True
        assert second.acquire() is False
        shown, quit_requested = threading.Event(), threading.Event()
        first.listen(on_show=shown.set, on_quit=quit_requested.set)
        assert second.signal(SingleInstance.SHOW) and shown.wait(3)
        assert not quit_requested.is_set()
        assert second.signal(SingleInstance.QUIT) and quit_requested.wait(3)
        first.release()
        assert second.acquire() is True, "the name is free again once the app has quit"
    finally:
        first.release()
        second.release()
    assert SingleInstance(name).signal() is False, "nothing to signal when no app runs"
