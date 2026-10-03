"""
Configuration manager for Willy using Pydantic Settings and dotenv.
"""

from typing import List
from pathlib import Path
import os
from dotenv import load_dotenv

# Preload environment from .env file if available
BASE_DIR = Path(__file__).resolve().parent.parent
env_path = BASE_DIR / ".env"
if env_path.exists():
    load_dotenv(dotenv_path=env_path)
else:
    load_dotenv()

try:
    from pydantic_settings import BaseSettings
    from pydantic import Field

    class Settings(BaseSettings):
        # LLM & STT
        LLM_PROVIDER: str = Field(default="groq", description="Provider for LLM (groq, gemini, or openai)")
        STT_PROVIDER: str = Field(default="groq", description="Provider for STT (groq or openai)")
        GROQ_API_KEY: str = Field(default="", description="API key for Groq Cloud")
        GROQ_API_KEY_BACKUP: str = Field(default="", description="Backup API key for Groq Cloud")
        GROQ_MODEL: str = Field(default="qwen/qwen3.8-27b", description="Groq LLM model name")
        GEMINI_API_KEY: str = Field(default="", description="API key for Google Gemini")
        GEMINI_MODEL: str = Field(default="gemini-2.5-flash", description="Google Gemini model name")
        OPENAI_API_KEY: str = Field(default="", description="API key for OpenAI")
        OPENAI_MODEL: str = Field(default="gpt-4o", description="OpenAI LLM model name")

        # Wake Word & Audio
        WAKE_WORDS: str = Field(default="willy,willie,hey willy,hey willie,willy wake up", description="Comma-separated wake words")
        ENERGY_THRESHOLD: float = Field(default=0.0035, description="Audio RMS energy silence threshold")
        SILENCE_DURATION: float = Field(default=1.25, description="Silence length (seconds) indicating end of query")
        MAX_RECORDING_DURATION: float = Field(default=20.0, description="Max recording duration in seconds")
        MIN_SPEECH_DURATION: float = Field(default=0.25, description="Minimum speech duration in seconds to avoid clicks/bursts")
        NOISE_SUPPRESSION: bool = Field(default=True, description="Enable active spectral noise reduction")
        CONVERSATION_TIMEOUT: float = Field(default=12.0, description="Active session duration without wake-word")
        SAMPLE_RATE: int = Field(default=16000, description="Audio sample rate in Hz")

        # Speech Synthesis
        TTS_PROVIDER: str = Field(default="edge-tts", description="TTS Provider (edge-tts or pyttsx3)")
        TTS_VOICE: str = Field(default="en-US-ChristopherNeural", description="Voice ID for edge-tts (English)")
        TTS_RATE: str = Field(default="+5%", description="Speech rate adjustment for edge-tts")

        # System, Cloud Relay & Guardrails
        ENABLE_ACOUSTIC_BARGE_IN: bool = Field(default=False, description="Enable microphone barge-in during speech (disabled by default to prevent speaker echo false-triggers)")
        ENABLE_CLOUD_RELAY: bool = Field(default=False, description="Enable outbound cloud bridge to EC2/Siri")
        POWERSHELL_TIMEOUT: int = Field(default=30, description="PowerShell command execution timeout (seconds)")
        DEBUG: bool = Field(default=False, description="Debug mode")

        class Config:
            env_file = str(BASE_DIR / ".env")
            env_file_encoding = "utf-8"
            extra = "ignore"

    settings = Settings()

except Exception:
    # Fallback if pydantic_settings isn't installed yet
    class SettingsFallback:
        def __init__(self):
            self.LLM_PROVIDER = os.getenv("LLM_PROVIDER", "groq")
            self.STT_PROVIDER = os.getenv("STT_PROVIDER", "groq")
            self.GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
            self.GROQ_MODEL = os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b")
            self.GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", os.getenv("GOOGLE_API_KEY", ""))
            self.GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
            self.OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
            self.OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
            self.WAKE_WORDS = os.getenv("WAKE_WORDS", "willy,willie,hey willy,hey willie,willy wake up")
            self.ENERGY_THRESHOLD = float(os.getenv("ENERGY_THRESHOLD", "0.0035"))
            self.SILENCE_DURATION = float(os.getenv("SILENCE_DURATION", "1.25"))
            self.MAX_RECORDING_DURATION = float(os.getenv("MAX_RECORDING_DURATION", "20.0"))
            self.MIN_SPEECH_DURATION = float(os.getenv("MIN_SPEECH_DURATION", "0.25"))
            self.NOISE_SUPPRESSION = os.getenv("NOISE_SUPPRESSION", "true").lower() in ("1", "true", "yes")
            self.CONVERSATION_TIMEOUT = float(os.getenv("CONVERSATION_TIMEOUT", "12.0"))
            self.SAMPLE_RATE = int(os.getenv("SAMPLE_RATE", "16000"))
            self.TTS_PROVIDER = os.getenv("TTS_PROVIDER", "edge-tts")
            self.TTS_VOICE = os.getenv("TTS_VOICE", "en-US-ChristopherNeural")
            self.TTS_RATE = os.getenv("TTS_RATE", "+5%")
            self.ENABLE_ACOUSTIC_BARGE_IN = os.getenv("ENABLE_ACOUSTIC_BARGE_IN", "false").lower() in ("1", "true", "yes")
            self.ENABLE_CLOUD_RELAY = os.getenv("ENABLE_CLOUD_RELAY", "false").lower() in ("1", "true", "yes")
            self.POWERSHELL_TIMEOUT = int(os.getenv("POWERSHELL_TIMEOUT", "30"))
            self.DEBUG = os.getenv("DEBUG", "false").lower() in ("1", "true", "yes")

    settings = SettingsFallback()


def get_wake_word_list() -> List[str]:
    """Returns a normalized list of lowercase wake words."""
    return [w.strip().lower() for w in settings.WAKE_WORDS.split(",") if w.strip()]
