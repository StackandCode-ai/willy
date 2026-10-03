"""
Willy PC Client Configuration.
"""

import os
import sys
import socket
from pathlib import Path
from dotenv import load_dotenv

# pc_client/.env holds client settings (hub URL, token); the project-root .env holds shared
# keys. The packaged WillyPC.exe reads the .env next to it (the build copies pc_client/.env
# there). None of them overrides variables already set in the environment, and earlier
# files win over later ones.
client_env = Path(__file__).resolve().parent / ".env"
root_env = Path(__file__).resolve().parent.parent / ".env"
# First-run setup (setup_dialog.py) saves the hub address and this PC's key here; unlike the
# app folder it survives app updates.
USER_ENV = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "WillyPC" / ".env"
env_files = [client_env, root_env, USER_ENV]
if getattr(sys, "frozen", False):
    env_files.insert(0, Path(sys.executable).resolve().parent / ".env")
found_env = [path for path in env_files if path.exists()]
for path in found_env:
    load_dotenv(path)
if not found_env:
    load_dotenv()

SERVER_URL = os.getenv("WILLY_SERVER_URL", "ws://127.0.0.1:8000/ws/devices")
# Also support relay URL fallback
if "WILLY_RELAY_URL" in os.environ and "WILLY_SERVER_URL" not in os.environ:
    SERVER_URL = os.getenv("WILLY_RELAY_URL").replace("/ws/pc", "/ws/devices")

TOKEN = os.getenv("WILLY_REMOTE_TOKEN", "")
DEVICE_NAME = os.getenv("WILLY_PC_NAME", socket.gethostname())
DEVICE_ID = f"pc_{socket.gethostname().lower().replace(' ', '_')}"
# Telemetry cadence; 2 s keeps phone/dashboard gauges live while costing ~30 ms of CPU per beat.
HEARTBEAT_INTERVAL_SEC = max(1.0, float(os.getenv("WILLY_HEARTBEAT_SEC", "2")))
# Read due reminders aloud on the PC (in addition to the pop-up).
SPEAK_REMINDERS = os.getenv("WILLY_SPEAK_REMINDERS", "true").strip().lower() not in ("0", "false", "no", "off")
