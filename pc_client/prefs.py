"""Small persistent settings for Willy PC (%LOCALAPPDATA%\\WillyPC\\prefs.json)."""

import json
import os
from pathlib import Path
from typing import Any

PREFS_PATH = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "WillyPC" / "prefs.json"
DEFAULTS = {"hey_willy": True, "mic_muted": False}


def load() -> dict:
    try:
        data = json.loads(PREFS_PATH.read_text(encoding="utf-8"))
        return {**DEFAULTS, **(data if isinstance(data, dict) else {})}
    except (OSError, ValueError):
        return dict(DEFAULTS)


def get(key: str) -> Any:
    return load().get(key, DEFAULTS.get(key))


def set(key: str, value: Any) -> None:  # noqa: A001 - prefs.set reads naturally
    data = load()
    data[key] = value
    try:
        PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
        PREFS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass
