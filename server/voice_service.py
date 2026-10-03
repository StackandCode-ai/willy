"""
Willy Server Voice Service.
Standalone high-speed STT transcription (Groq Whisper) and in-memory neural TTS synthesis (Edge-TTS).
Runs anywhere (Linux VPS, macOS, Windows) without external local C-libraries.
"""

import io
import base64
import asyncio
from collections import OrderedDict
from typing import Optional
from server.config import settings
from server import ai_config

TTS_CACHE_SIZE = 48
TTS_CACHE_MAX_CHARS = 160   # only short, frequently repeated replies are cached


class ServerVoiceService:
    def __init__(self):
        self._groq_client = None
        self._tts_cache: "OrderedDict[str, str]" = OrderedDict()
        self._init_client()

    def _init_client(self):
        """Speech-to-text: Groq Whisper, else OpenAI Whisper (whichever key the owner has saved)."""
        self._stt_model = None
        groq_key = ai_config.key_for("groq")
        openai_key = ai_config.key_for("openai")
        try:
            if groq_key:
                from groq import Groq
                self._groq_client = Groq(api_key=groq_key)
                self._stt_model = settings.GROQ_WHISPER_MODEL
            elif openai_key:
                from openai import OpenAI
                self._groq_client = OpenAI(api_key=openai_key)  # same transcription API shape
                self._stt_model = "whisper-1"
            else:
                self._groq_client = None
        except Exception as e:
            self._groq_client = None
            print(f"[Server Voice] Could not initialize speech-to-text: {e}")

    def reload(self) -> None:
        self._init_client()

    def transcribe_audio(self, audio_bytes: bytes) -> Optional[str]:
        """Transcribes incoming audio bytes (webm, wav, mp3, m4a) using Groq Whisper API."""
        if not audio_bytes or len(audio_bytes) < 300:
            return None

        if not self._groq_client:
            self._init_client()

        if not self._groq_client:
            print("[Server Voice] No Groq or OpenAI key for speech-to-text (Settings > AI).")
            return None

        try:
            # Detect container format from magic bytes
            filename = "audio.wav"
            if audio_bytes.startswith(b"\x1a\x45\xdf\xa3"):
                filename = "audio.webm"
            elif audio_bytes.startswith(b"OggS"):
                filename = "audio.ogg"
            elif audio_bytes.startswith(b"ID3") or audio_bytes.startswith(b"\xff\xfb"):
                filename = "audio.mp3"
            elif len(audio_bytes) > 8 and audio_bytes[4:8] == b"ftyp":
                filename = "audio.m4a"

            bio = io.BytesIO(audio_bytes)
            bio.name = filename

            transcription = self._groq_client.audio.transcriptions.create(
                file=bio,
                model=self._stt_model or settings.GROQ_WHISPER_MODEL,
                temperature=0.0,
                language="en",
                # Vocabulary hint: without it "Willy" is often heard as Lily / Billy / Wiley.
                prompt="Names: Willy.",
            )
            text = transcription.text.strip()
            return text if text else None
        except Exception as e:
            print(f"[Server Voice] Transcription error: {e}")
            return None

    async def transcribe_audio_async(self, audio_bytes: bytes) -> Optional[str]:
        """Runs transcription in a worker thread so the event loop stays responsive."""
        return await asyncio.to_thread(self.transcribe_audio, audio_bytes)

    @staticmethod
    def is_tamil_text(text: str) -> bool:
        """Returns False (English-only mode)."""
        return False

    async def synthesize_speech_base64(self, text: str) -> Optional[str]:
        """Synthesizes text in-memory using Edge-TTS and returns base64 MP3."""
        if not text or not text.strip():
            return None
        key = text.strip()
        cached = self._tts_cache.get(key)
        if cached:
            self._tts_cache.move_to_end(key)
            return cached
        try:
            import edge_tts
            voice = getattr(settings, "TTS_VOICE", "en-US-ChristopherNeural")
            rate = getattr(settings, "TTS_RATE", "+0%") or "+0%"
            communicate = edge_tts.Communicate(text=key, voice=voice, rate=rate)
            audio_buffer = bytearray()
            async for chunk in communicate.stream():
                if chunk.get("type") == "audio":
                    audio_buffer.extend(chunk.get("data", b""))
            if audio_buffer:
                encoded = base64.b64encode(bytes(audio_buffer)).decode("utf-8")
                if len(key) <= TTS_CACHE_MAX_CHARS:
                    self._tts_cache[key] = encoded
                    while len(self._tts_cache) > TTS_CACHE_SIZE:
                        self._tts_cache.popitem(last=False)
                return encoded
        except Exception as e:
            print(f"[Server Voice] Synthesis error: {e}")
        return None
