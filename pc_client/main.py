"""
Willy PC entry point (also the entry point of WillyPC.exe).

    WillyPC.exe  |  python pc_client/main.py      open the app (or bring the running one to the front)
        --tray                                    start hidden in the notification area (Windows sign-in)
        --quit                                    close the running app
        --enable-autostart / --disable-autostart / --autostart-status
        --cli  (or --headless)                    console client, no window
"""

import os
import sys
import time
from pathlib import Path

# Add pc_client directory and parent to sys.path
PC_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PC_DIR.parent

if str(PC_DIR) not in sys.path:
    sys.path.insert(0, str(PC_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

LOG_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "WillyPC"


def _log_to_file_without_console() -> None:
    """pythonw.exe and the windowed exe have no console (sys.stdout is None): keep a small
    log file instead, so start-up problems (e.g. at sign-in) can still be diagnosed."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = LOG_DIR / "willy-pc.log"
        if path.exists() and path.stat().st_size > 2_000_000:
            path.replace(path.with_name("willy-pc.old.log"))
        stream = open(path, "a", encoding="utf-8", errors="replace", buffering=1)
        stream.write(f"\n==== Willy PC {time.strftime('%Y-%m-%d %H:%M:%S')} pid {os.getpid()} "
                     f"args {sys.argv[1:]} ====\n")
        sys.stdout = sys.stdout or stream
        sys.stderr = sys.stderr or stream
    except Exception:
        pass


def _tell(text: str) -> None:
    """Prints, or shows a message box when there is no console to print to."""
    if sys.stdout is not None:
        print(text)
        return
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, "Willy PC", 0x40)  # MB_ICONINFORMATION
    except Exception:
        print(text)


def _autostart_command(args: set) -> int:
    from pc_client import autostart

    if "--enable-autostart" in args:
        res = autostart.enable()
        if not res["success"]:
            _tell(res["error"])
            return 1
        extra = "\nReplaced the old Willy assistant's startup entry." if res.get("replaced_legacy") else ""
        _tell(f"Willy PC will start in the tray when you sign in.\n{res['command']}{extra}")
    elif "--disable-autostart" in args:
        autostart.disable()
        _tell("Willy PC won't start automatically any more.")
    else:
        st = autostart.status()
        lines = [f"Start with Windows: {'on' if st['enabled'] else 'off'}"]
        if st["command"]:
            lines.append(f"Command: {st['command']}")
        if st["switched_off_in_startup_apps"]:
            lines.append("Switched off in Task Manager > Startup apps.")
        if st["legacy_command"]:
            lines.append(f"Old assistant entry still present: {st['legacy_command']}")
        _tell("\n".join(lines))
    return 0


def _relaunch() -> None:
    """Starts a fresh copy (it reads the settings just saved) and lets this one exit."""
    import subprocess

    cmd = [sys.executable] + ([] if getattr(sys, "frozen", False) else sys.argv)
    subprocess.Popen(cmd + (sys.argv[1:] if getattr(sys, "frozen", False) else []), close_fds=True)


def main(argv=None) -> int:
    args = set(sys.argv[1:] if argv is None else argv)
    if "--cli" in args or "--headless" in args:
        from pc_client.client import run_pc_client
        run_pc_client()
        return 0
    if args & {"--enable-autostart", "--disable-autostart", "--autostart-status"}:
        return _autostart_command(args)

    _log_to_file_without_console()
    from pc_client.instance import SingleInstance

    instance = SingleInstance()
    if "--quit" in args:
        return 0 if instance.signal(SingleInstance.QUIT) else 1
    if not instance.acquire():
        # Already running: a normal launch brings its window up; a sign-in launch does nothing.
        if "--tray" not in args:
            instance.signal(SingleInstance.SHOW)
        return 0

    from pc_client import config

    if len((config.TOKEN or "").strip()) < 20 and "--tray" not in args:
        # A fresh download: connect to a hub first (the tray start at sign-in just waits).
        from pc_client.setup_dialog import run_setup

        if not run_setup(config.DEVICE_ID, config.DEVICE_NAME, os.getenv("WILLY_HUB_URL", "")):
            return 0
        instance.release()
        _relaunch()
        return 0

    from pc_client.app_window import main as run_gui
    run_gui(start_hidden="--tray" in args, instance=instance)
    return 0


if __name__ == "__main__":
    sys.exit(main())
