"""
Server Configuration and Environment Settings.
Completely standalone configuration for the Willy Central Server Hub.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from server directory or project root
SERVER_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SERVER_DIR.parent

if (SERVER_DIR / ".env").exists():
    load_dotenv(SERVER_DIR / ".env")
elif (PROJECT_ROOT / ".env").exists():
    load_dotenv(PROJECT_ROOT / ".env")
else:
    load_dotenv()

DEFAULT_REMOTE_TOKEN = ""  # no built-in token: set WILLY_REMOTE_TOKEN in server/.env

# Where reminders, alarms, telemetry and the activity log are persisted.
DATA_DIR = Path(os.getenv("WILLY_DATA_DIR") or (SERVER_DIR / "data"))


def _env_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None or not val.strip():
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


class ServerSettings:
    HOST: str = os.getenv("SERVER_HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT") or os.getenv("SERVER_PORT", "8000"))
    WILLY_REMOTE_TOKEN: str = os.getenv("WILLY_REMOTE_TOKEN", DEFAULT_REMOTE_TOKEN)

    # LLM Providers: 'groq' | 'gemini' | 'openai'
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "groq")
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    GROQ_MODEL: str = os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b")

    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "") or os.getenv("GOOGLE_API_KEY", "")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    # Optional 'reasoning_effort' sent to reasoning models (e.g. 'none' makes Qwen3 on Groq
    # answer without a thinking phase). Empty = not sent. Dropped automatically if rejected.
    LLM_REASONING_EFFORT: str = os.getenv("LLM_REASONING_EFFORT", "").strip()
    # Models tried in order when the main one is rate-limited (each has its own daily quota
    # on Groq's free tier). Unknown / unavailable models are skipped automatically.
    LLM_FALLBACK_MODELS: str = os.getenv("LLM_FALLBACK_MODELS", "openai/gpt-oss-120b,openai/gpt-oss-20b")

    # Voice Settings
    TTS_VOICE: str = os.getenv("TTS_VOICE", "en-US-ChristopherNeural")
    TTS_RATE: str = os.getenv("TTS_RATE", "+0%")
    GROQ_WHISPER_MODEL: str = os.getenv("GROQ_WHISPER_MODEL", "whisper-large-v3")

    # Realtime tuning
    # Answer simple, unambiguous commands ("lock pc", "volume 40") without an LLM round-trip.
    FAST_PATH_ENABLED: bool = _env_bool("FAST_PATH_ENABLED", True)
    # A socket-connected device with no heartbeat for this long is shown as offline.
    DEVICE_STALE_SEC: float = float(os.getenv("DEVICE_STALE_SEC", "20"))
    DEVICE_COMMAND_TIMEOUT_SEC: float = float(os.getenv("DEVICE_COMMAND_TIMEOUT_SEC", "20"))

    # IANA zone used for reminders, alarms and briefings (e.g. 'Asia/Kolkata').
    # Empty = use the UTC offset reported by the user's devices, else server local time.
    USER_TIMEZONE: str = os.getenv("USER_TIMEZONE", "").strip()

    @property
    def using_default_token(self) -> bool:
        return len((self.WILLY_REMOTE_TOKEN or "").strip()) < 20


settings = ServerSettings()
