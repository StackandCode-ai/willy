"""
Builds Willy PC into a standalone Windows app: dist\\WillyPC\\WillyPC.exe

    python build_pc_exe.py               build, then start Willy PC
    python build_pc_exe.py --autostart   also start it (hidden in the tray) at every Windows sign-in
    python build_pc_exe.py --no-launch   build only

A running WillyPC.exe is closed first (its files get replaced). The hub settings
(pc_client/.env) are copied next to the exe, and a "Willy PC" Start menu shortcut is added.

The exe runs without admin rights: Windows won't start an app that demands elevation at
sign-in, and Willy's admin tools (Admin PowerShell, Task Manager) still ask when used.
"""

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
PC_CLIENT_DIR = ROOT_DIR / "pc_client"
MAIN_FILE = PC_CLIENT_DIR / "main.py"
APP_DIR = ROOT_DIR / "dist" / "WillyPC"
# PyInstaller writes here first; the result is then copied into APP_DIR. Deleting APP_DIR
# itself fails while an Explorer window or a terminal has it open, but replacing its
# contents works.
STAGING_DIR = ROOT_DIR / "build" / "staging"
EXE_PATH = APP_DIR / "WillyPC.exe"
ICON_PATH = ROOT_DIR / "build" / "willy.ico"
SHORTCUT = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Willy PC.lnk"

HIDDEN_IMPORTS = (
    "pc_client", "pc_client.app_window", "pc_client.executor", "pc_client.client", "pc_client.telemetry",
    "pc_client.config", "pc_client.tray", "pc_client.autostart", "pc_client.instance", "pc_client.brand",
    "pc_client.tools.audio_tools", "pc_client.tools.proc_snapshot", "pc_client.tools.window_reader",
    "pc_client.power_events", "pc_client.tools.file_relay", "pc_client.tools.file_ops", "pc_client.tools.media_tools", "pc_client.tools.git_ops", "pc_client.hey_willy", "pc_client.prefs", "pc_client.pairing", "pc_client.setup_dialog", "tkinter", "vosk", "sounddevice", "docx", "pypdf",
    "pystray._win32", "pycaw.pycaw", "comtypes", "win32clipboard", "win32com.client", "pythoncom",
)
# Optional imports the PC app never uses. Left in, PyInstaller follows them (Pillow -> IPython ->
# matplotlib ..., openai -> pandas, pyautogui -> cv2, pygame -> numpy) and hooks end up
# bundling PyTorch and friends: gigabytes and a 10+ minute build.
EXCLUDES = (
    "torch", "torchvision", "torchaudio", "tensorflow", "keras", "jax", "transformers", "datasets", "sklearn",
    "scipy", "sympy", "pandas", "numpy", "matplotlib", "matplotlib_inline", "IPython", "jedi", "parso",
    "ipykernel", "jupyter_client", "jupyter_core", "notebook", "zmq", "tornado", "cv2", "numba", "llvmlite",
    "onnxruntime", "pytest", "_pytest", "test", "setuptools", "pkg_resources", "PyQt5", "PyQt6", "PySide2",
    "PySide6", "wx", "gi",
)

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def running_exe_pids() -> list:
    import psutil

    return [p.pid for p in psutil.process_iter(["name", "exe"])
            if (p.info.get("name") or "").lower() == "willypc.exe"]


def close_running_app() -> bool:
    """Asks a running WillyPC.exe to quit (clean hub disconnect, tray icon removed)."""
    if not running_exe_pids():
        return False
    from pc_client.instance import SingleInstance

    print("[*] Closing the running Willy PC so its files can be replaced...")
    SingleInstance().signal(SingleInstance.QUIT)
    deadline = time.time() + 10
    while time.time() < deadline and running_exe_pids():
        time.sleep(0.25)
    left = running_exe_pids()
    if left:
        print("[!] It didn't close in time; ending it.")
        import psutil

        for pid in left:
            try:
                psutil.Process(pid).kill()
            except psutil.Error:
                pass
        time.sleep(1)
    return True


def run_pyinstaller() -> None:
    from pc_client.brand import save_ico

    save_ico(ICON_PATH)
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--onedir",
        "--windowed",  # no console window
        "--name", "WillyPC",
        "--icon", str(ICON_PATH),
        "--collect-all", "customtkinter",
        "--collect-all", "websockets",
        "--collect-all", "pygame",
        "--collect-all", "vosk",           # libvosk.dll
        "--collect-all", "_sounddevice_data",  # PortAudio
        "--paths", str(ROOT_DIR),
        "--distpath", str(STAGING_DIR),
    ]
    for module in HIDDEN_IMPORTS:
        cmd += ["--hidden-import", module]
    for module in EXCLUDES:
        cmd += ["--exclude-module", module]
    cmd.append(str(MAIN_FILE))
    print("[*] Running PyInstaller (takes a minute or two)...")
    proc = subprocess.run(cmd, cwd=str(ROOT_DIR))
    if proc.returncode != 0:
        print(f"[-] PyInstaller failed with exit code {proc.returncode}")
        sys.exit(proc.returncode)
    install_build()


def install_build() -> None:
    """Replaces the contents of dist\WillyPC with the fresh build (the folder itself stays)."""
    built = STAGING_DIR / "WillyPC"
    APP_DIR.mkdir(parents=True, exist_ok=True)
    for entry in APP_DIR.iterdir():
        if entry.name == ".env":
            continue  # rewritten by copy_settings()
        if entry.is_dir():
            shutil.rmtree(entry)
        else:
            entry.unlink()
    shutil.copytree(built, APP_DIR, dirs_exist_ok=True)
    print(f"[+] Installed the build into {APP_DIR}")


def copy_settings() -> None:
    """The exe reads the .env next to it: the client's hub URL and token."""
    source = next((p for p in (PC_CLIENT_DIR / ".env", ROOT_DIR / ".env") if p.exists()), None)
    if source is None:
        print("[!] No pc_client/.env found: the exe will use the defaults (a hub on this PC).\n"
              "    Copy pc_client/.env.example to pc_client/.env, set WILLY_SERVER_URL and WILLY_REMOTE_TOKEN, "
              "and build again.")
        return
    shutil.copy2(source, APP_DIR / ".env")
    print(f"[+] Hub settings: {source.relative_to(ROOT_DIR)} -> dist\\WillyPC\\.env")


def make_start_menu_shortcut() -> None:
    try:
        import win32com.client

        link = win32com.client.Dispatch("WScript.Shell").CreateShortcut(str(SHORTCUT))
        link.TargetPath = str(EXE_PATH)
        link.WorkingDirectory = str(APP_DIR)
        link.IconLocation = f"{EXE_PATH},0"
        link.Description = "Willy PC - connects this PC to your Willy hub"
        link.Save()
        print("[+] Start menu shortcut: Willy PC")
    except Exception as e:
        print(f"[!] Couldn't create the Start menu shortcut: {e}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--autostart", action="store_true",
                        help="start Willy PC hidden in the tray whenever you sign in to Windows")
    parser.add_argument("--no-launch", action="store_true", help="don't start Willy PC after building")
    parser.add_argument("--tray", action="store_true", help="start it hidden in the tray after building")
    parser.add_argument("--ci", action="store_true",
                        help="release build (GitHub Actions): no .env copied, no shortcut, no autostart, no launch; "
                             "writes dist/WillyPC-windows.zip")
    args = parser.parse_args()
    if args.ci:
        print("Building the Windows release")
        run_pyinstaller()
        stale = APP_DIR / ".env"
        if stale.exists():
            stale.unlink()  # a release must never carry anybody's hub settings
        archive = shutil.make_archive(str(ROOT_DIR / "dist" / "WillyPC-windows"), "zip", root_dir=APP_DIR.parent,
                                      base_dir=APP_DIR.name)
        print(f"[+] {archive}")
        return

    print("=" * 65)
    print("  BUILDING WILLY PC (dist\\WillyPC\\WillyPC.exe)")
    print("=" * 65)
    close_running_app()
    run_pyinstaller()
    copy_settings()
    make_start_menu_shortcut()

    from pc_client import autostart

    if args.autostart:
        res = autostart.enable(command=autostart.command_for(EXE_PATH))
        if res["success"]:
            print("[+] Start with Windows: on (starts hidden in the tray)")
            if res.get("replaced_legacy"):
                print(f"    Replaced the old assistant's startup entry: {res['replaced_legacy']}")
        else:
            print(f"[!] {res['error']}")
    else:
        current = autostart.status()["command"]
        print(f"[i] Start with Windows: {'on - ' + current if current else 'off (use --autostart or the tray menu)'}")

    print("\n" + "=" * 65)
    print("  BUILD COMPLETE")
    print(f"  App:   {EXE_PATH}")
    print("  Start: Start menu > Willy PC   (runs in the tray; close the window to hide it)")
    print("=" * 65)

    if not args.no_launch:
        subprocess.Popen([str(EXE_PATH)] + (["--tray"] if args.tray else []), cwd=str(APP_DIR),
                         creationflags=0x00000008 | 0x00000200)  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP


if __name__ == "__main__":
    main()
