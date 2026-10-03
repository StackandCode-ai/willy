"""
Speech-to-Text (STT) layer supporting Groq Whisper and OpenAI Whisper API.
"""

import io
from typing import Optional
from willy.config import settings


class SpeechToText:
    """
    Transcribes audio bytes into clean English text using ultra-fast Groq Whisper
    or OpenAI Whisper.
    """

    def __init__(self, provider: str = None):
        self.provider = (provider or settings.STT_PROVIDER).lower()
        self._groq_client = None
        self._openai_client = None
        self._init_client()

    def _init_client(self):
        if self.provider == "groq":
            try:
                from groq import Groq
                api_key = settings.GROQ_API_KEY
                if not api_key:
                    print("[!] Warning: GROQ_API_KEY is not set in environment or .env file.")
                self._groq_client = Groq(api_key=api_key)
            except ImportError:
                print("[-] 'groq' package is not installed. Run: pip install groq")
        elif self.provider == "openai":
            try:
                from openai import OpenAI
                api_key = settings.OPENAI_API_KEY
                if not api_key:
                    print("[!] Warning: OPENAI_API_KEY is not set in environment or .env file.")
                self._openai_client = OpenAI(api_key=api_key)
            except ImportError:
                print("[-] 'openai' package is not installed. Run: pip install openai")
        else:
            raise ValueError(f"Unsupported STT provider: {self.provider}. Use 'groq' or 'openai'.")

    @staticmethod
    def _is_noise_hallucination(text: str) -> bool:
        """Filters out known Whisper hallucinations on background noise and static."""
        if not text:
            return True
        cleaned = text.lower().strip().rstrip(".!?, ")
        if not any(c.isalnum() for c in cleaned):
            return True
        
        # Substring checks for subtitle tags and video endings
        noise_substrings = (
            "thank you", "thanks for watching", "thanks for listening",
            "subtitles by", "amara.org", "translated by", "english subtitles",
            "like and subscribe", "please subscribe", "all rights reserved"
        )
        for sub in noise_substrings:
            if sub in cleaned:
                return True

        # Exact keywords
        exact_hallucinations = {
            "thanks", "you", "bye", "bye bye", "so", "the end",
            "music", "[music]", "(music)", "applause", "[applause]", "(applause)",
            "cheering", "laughter", "[laughter]", "(laughter)", "silence",
            "whispering", "watching", "subscribe", "goodbye", "peace", "copyright"
        }
        if cleaned in exact_hallucinations:
            return True

        # Text completely enclosed in brackets, e.g. [Music], (Laughter), [Background noise]
        if (cleaned.startswith("[") and cleaned.endswith("]")) or (cleaned.startswith("(") and cleaned.endswith(")")):
            return True

        return False

    def transcribe(self, wav_bytes: bytes, language: str = "en", prompt: str = None) -> Optional[str]:
        """
        Transcribes in-memory WAV bytes to text.
        Supports high-accuracy English speech recognition.
        """
        if not wav_bytes or len(wav_bytes) < 1000:
            return None

        # English prompt primes Whisper to recognize voice commands cleanly
        prompt_text = prompt or "Hey Willy, open chrome, notepad, what should we do."
        audio_file = io.BytesIO(wav_bytes)
        if wav_bytes.startswith(b"\x1a\x45\xdf\xa3"):
            audio_file.name = "audio.webm"
        elif wav_bytes.startswith(b"OggS"):
            audio_file.name = "audio.ogg"
        elif wav_bytes.startswith(b"ID3") or wav_bytes.startswith(b"\xff\xfb") or wav_bytes.startswith(b"\xff\xf3"):
            audio_file.name = "audio.mp3"
        elif len(wav_bytes) > 8 and wav_bytes[4:8] == b"ftyp":
            audio_file.name = "audio.m4a"
        else:
            audio_file.name = "audio.wav"

        # 1. Try Groq Whisper with automatic fallback to turbo
        if self.provider == "groq" and self._groq_client:
            for model_name in ("whisper-large-v3-turbo", "whisper-large-v3"):
                try:
                    audio_file.seek(0)
                    kwargs = {
                        "file": audio_file,
                        "model": model_name,
                        "prompt": prompt_text,
                        "response_format": "json",
                        "temperature": 0.0,
                    }
                    if language:
                        kwargs["language"] = language

                    transcription = self._groq_client.audio.transcriptions.create(**kwargs)
                    text = transcription.text.strip()
                    if not text or self._is_noise_hallucination(text):
                        continue

                    return text
                except Exception as e:
                    err_str = str(e)
                    if "429" in err_str or "rate_limit" in err_str.lower():
                        continue  # Try next model or fallback
                    print(f"[-] Groq STT ({model_name}) error: {e}")

        # 2. Try OpenAI Whisper if configured
        if self.provider == "openai" and self._openai_client:
            try:
                audio_file.seek(0)
                kwargs = {
                    "file": audio_file,
                    "model": "whisper-1",
                    "prompt": prompt_text,
                    "response_format": "json",
                    "temperature": 0.0,
                }
                if language:
                    kwargs["language"] = language

                transcription = self._openai_client.audio.transcriptions.create(**kwargs)
                text = transcription.text.strip()
                if text and not self._is_noise_hallucination(text):
                    return text
            except Exception as e:
                print(f"[-] OpenAI STT error: {e}")

        # 3. Unlimited Free Fallback: SpeechRecognition (Google STT)
        try:
            import speech_recognition as sr
            r = sr.Recognizer()
            audio_file.seek(0)
            with sr.AudioFile(audio_file) as source:
                audio_data = r.record(source)
            try:
                text = r.recognize_google(audio_data, language=language or "en-US")
            except Exception:
                text = None

            if text and text.strip() and not self._is_noise_hallucination(text):
                return text.strip()
        except Exception:
            pass

        return None
