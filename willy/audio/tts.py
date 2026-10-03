"""
Text-to-Speech (TTS) engine for Willy using Microsoft Edge-TTS with pyttsx3 fallback.
"""

import os
import io
import time
import asyncio
import tempfile
import threading
import subprocess
from typing import Optional
from willy.config import settings

# Initialize pygame mixer for crystal-clear, low-latency audio playback without buffer underruns
_pygame_initialized = False
try:
    import pygame
    # Pre-init with 24000Hz (native Edge-TTS rate) and a 4096-sample buffer to prevent crackle/stutter under load
    try:
        pygame.mixer.pre_init(frequency=24000, size=-16, channels=2, buffer=4096)
    except Exception:
        pass
    pygame.mixer.init(frequency=24000, size=-16, channels=2, buffer=4096)
    _pygame_initialized = True
except Exception:
    try:
        import pygame
        pygame.mixer.init()
        _pygame_initialized = True
    except Exception:
        _pygame_initialized = False


class TextToSpeech:
    """
    Synthesizes and speaks text using Edge-TTS (natural neural voice)
    or local pyttsx3 (SAPI5 offline).
    """

    def __init__(
        self,
        provider: str = None,
        voice: str = None,
        rate: str = None,
    ):
        self.provider = (provider or settings.TTS_PROVIDER).lower()
        self.voice = voice or settings.TTS_VOICE
        self.rate = rate or settings.TTS_RATE
        self._lock = threading.Lock()
        self._pyttsx3_engine = None
        self.interrupt_event = threading.Event()
        self.is_speaking = False

    @staticmethod
    def is_tamil_text(text: str) -> bool:
        """Returns False (English-only mode)."""
        return False

    def _get_pyttsx3(self):
        if self._pyttsx3_engine is None:
            try:
                import pyttsx3
                self._pyttsx3_engine = pyttsx3.init()
                self._pyttsx3_engine.setProperty("rate", 190)
            except Exception as e:
                print(f"[-] pyttsx3 initialization failed: {e}")
        return self._pyttsx3_engine

    def stop(self):
        """Immediately halts any current speech playback and signals interruption."""
        self.interrupt_event.set()
        if _pygame_initialized:
            try:
                pygame.mixer.music.stop()
                pygame.mixer.music.unload()
            except Exception:
                pass
        self.is_speaking = False

    def speak(self, text: str, block: bool = True, listener = None):
        """
        Speaks the given text out loud with active speaker/mic AEC coordination.
        """
        if not text or not text.strip():
            return

        # Clean text from raw markdown or code block tokens for smoother speech
        cleaned_text = self._clean_for_speech(text)

        # Register spoken text in self-speech filter so microphone ignores acoustic echo
        try:
            from willy.audio.wake_word import record_willy_speech
            record_willy_speech(cleaned_text)
        except Exception:
            pass

        if block:
            self._speak_sync(cleaned_text, listener)
        else:
            t = threading.Thread(target=self._speak_sync, args=(cleaned_text, listener), daemon=True)
            t.start()

    def _speak_sync(self, text: str, listener = None):
        with self._lock:
            self.interrupt_event.clear()
            self.is_speaking = True
            if listener:
                listener.set_speaker_active(True)
                try:
                    listener.pre_buffer.clear()
                except Exception:
                    pass
            try:
                if self.interrupt_event.is_set():
                    return
                if self.provider == "edge-tts":
                    success = self._speak_edge_tts(text)
                    if not success and not self.interrupt_event.is_set():
                        # Fallback to pyttsx3
                        self._speak_pyttsx3(text)
                else:
                    self._speak_pyttsx3(text)
            finally:
                self.is_speaking = False
                if listener:
                    # Generous acoustic cooldown (0.35s) so speaker decay and room reverberation fully dissipate
                    time.sleep(0.35)
                    listener.set_speaker_active(False)
                    try:
                        listener.pre_buffer.clear()
                    except Exception:
                        pass

    def _speak_edge_tts(self, text: str) -> bool:
        try:
            import edge_tts
            
            # Use configured English voice
            active_voice = self.voice

            async def _generate():
                communicate = edge_tts.Communicate(text=text, voice=active_voice, rate=self.rate)
                with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                    tmp_path = f.name
                await communicate.save(tmp_path)
                return tmp_path

            # Run edge-tts in a new event loop
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                audio_path = loop.run_until_complete(_generate())
            finally:
                loop.close()

            # Play audio file
            self._play_audio_file(audio_path)
            try:
                os.remove(audio_path)
            except OSError:
                pass
            return True

        except Exception as e:
            print(f"[-] Edge-TTS error: {e}. Falling back to pyttsx3.")
            return False

    def _get_physical_speaker_device(self):
        """Finds physical hardware speaker (e.g. Realtek) to bypass virtual audio sinks."""
        try:
            import sounddevice as sd
            for i, d in enumerate(sd.query_devices()):
                name = d['name'].lower()
                if ('speakers (realtek' in name or 'realtek' in name) and d['max_output_channels'] > 0:
                    return i
        except Exception:
            pass
        return None

    def _speak_pyttsx3(self, text: str):
        """Synthesizes speech using native Windows SAPI COM - works for unlimited consecutive calls."""
        try:
            import win32com.client
            import pythoncom

            # Initialize COM for the current thread
            pythoncom.CoInitialize()
            try:
                speaker = win32com.client.Dispatch("SAPI.SpVoice")
                speaker.Volume = 100
                speaker.Rate = 3  # Natural conversational speed (1 was sluggish and robotic)
                try:
                    voices = speaker.GetVoices()
                    for i in range(voices.Count):
                        desc = voices.Item(i).GetDescription().lower()
                        if "zira" in desc or "natural" in desc:
                            speaker.Voice = voices.Item(i)
                            break
                except Exception:
                    pass
                speaker.Speak(text)
            finally:
                pythoncom.CoUninitialize()
            return
        except Exception as e:
            print(f"[-] SAPI COM speech error: {e}")

        # Fallback to direct PowerShell speech
        try:
            escaped = text.replace("'", "''")
            ps_cmd = f"$s = New-Object -ComObject SAPI.SpVoice; $s.Volume = 100; $s.Rate = 2; $s.Speak('{escaped}')"
            subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps_cmd], capture_output=True, timeout=5)
        except Exception:
            print(f"[Willy Speaks]: {text}")

    def _play_audio_file(self, filepath: str):
        """Plays an audio file using pygame with explicit max volume or SAPI fallback."""
        if self.interrupt_event.is_set():
            return

        if _pygame_initialized:
            try:
                pygame.mixer.music.set_volume(1.0)
                pygame.mixer.music.load(filepath)
                pygame.mixer.music.play()
                while pygame.mixer.music.get_busy():
                    if self.interrupt_event.is_set():
                        try:
                            pygame.mixer.music.stop()
                        except Exception:
                            pass
                        break
                    pygame.time.Clock().tick(50)  # Check every 20ms for instant interruption
                try:
                    pygame.mixer.music.unload()
                except Exception:
                    pass
                return
            except Exception as e:
                print(f"[-] pygame audio playback error: {e}")

        if not self.interrupt_event.is_set():
            # Direct pyttsx3 fallback if media player fails
            self._speak_pyttsx3("Audio playback complete.")

    @staticmethod
    def _clean_for_speech(text: str) -> str:
        """Strips markdown links, URLs, and code blocks for fluid spoken output."""
        import re
        # Remove URLs
        text = re.sub(r'https?://\S+', 'link', text)
        # Remove markdown bold/italics
        text = re.sub(r'[\*_`#]', '', text)
        # Simplify path slashes
        text = text.replace('\\', ' ')
        return text.strip()
