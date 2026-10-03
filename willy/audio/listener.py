"""
Low-latency microphone capture with circular pre-buffering, bandpass filtering,
adaptive noise floor calibration, multi-frame speech detection, and spectral denoising.
"""

import io
import time
import wave
import collections
import threading
from typing import Optional
import numpy as np

try:
    import sounddevice as sd
except ImportError:
    sd = None

try:
    import noisereduce as nr
except ImportError:
    nr = None

try:
    from scipy import signal
except ImportError:
    signal = None

from willy.config import settings


class AudioListener:
    """
    Captures live audio from the microphone, performing bandpass-filtered
    Voice Activity Detection (VAD), multi-frame onset gating, dynamic noise floor
    tracking, and spectral background noise cancellation.
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        channels: int = 1,
        chunk_duration_ms: int = 30,
        energy_threshold: float = None,
        silence_duration: float = None,
    ):
        self.sample_rate = sample_rate or settings.SAMPLE_RATE
        self.channels = channels
        self.chunk_size = int(self.sample_rate * (chunk_duration_ms / 1000.0))
        self.energy_threshold = energy_threshold or settings.ENERGY_THRESHOLD
        self.silence_duration = silence_duration if silence_duration is not None else float(settings.SILENCE_DURATION)

        # Pre-compute bandpass filter coefficients (100 Hz to 3800 Hz) for speech extraction
        self._filter_b = None
        self._filter_a = None
        if signal is not None:
            try:
                nyq = 0.5 * self.sample_rate
                low = max(0.01, min(0.9, 100.0 / nyq))
                high = max(low + 0.05, min(0.95, 3800.0 / nyq))
                self._filter_b, self._filter_a = signal.butter(2, [low, high], btype="band")
            except Exception:
                self._filter_b, self._filter_a = None, None
        
        # Ring buffer for ~0.6s of audio to retain speech onset (prevents clipping first syllables)
        pre_buffer_chunks = int(600 / chunk_duration_ms)
        self.pre_buffer = collections.deque(maxlen=pre_buffer_chunks)
        self.is_recording = False
        self.is_speaker_active = False  # Acoustic Echo Cancellation (AEC) gate
        self._abort_requested = threading.Event()
        self.ambient_profile = None
        self.ambient_rms = 0.0020

    def abort(self):
        """Signals the listener to abort any active recording or wait loop immediately."""
        self._abort_requested.set()

    def set_speaker_active(self, active: bool):
        """Notifies the listener that the speaker is currently outputting sound."""
        self.is_speaker_active = active
        self.pre_buffer.clear()

    def filter_chunk(self, chunk: np.ndarray) -> np.ndarray:
        """Applies 100Hz-3800Hz bandpass filter to suppress low-frequency rumble and high-frequency hiss."""
        if self._filter_b is not None and self._filter_a is not None and chunk.size > 16:
            try:
                return signal.lfilter(self._filter_b, self._filter_a, chunk).astype(np.float32)
            except Exception:
                pass
        return chunk

    def calculate_rms(self, chunk: np.ndarray) -> float:
        """Calculates Root-Mean-Square energy of a bandpass-filtered audio frame."""
        if chunk is None or chunk.size == 0:
            return 0.0
        filtered = self.filter_chunk(chunk)
        return float(np.sqrt(np.mean(filtered**2)))

    def calibrate_ambient_noise(self, duration_sec: float = 1.0) -> float:
        """
        Listens to ambient room noise and dynamically sets the speech trigger threshold
        safely above the room's noise floor.
        """
        if sd is None:
            return self.energy_threshold

        print(f"[*] Calibrating ambient noise for {duration_sec}s... Please stay quiet.")
        samples = int(self.sample_rate * duration_sec)
        try:
            recording = sd.rec(samples, samplerate=self.sample_rate, channels=self.channels, dtype="float32")
            sd.wait()
            self.ambient_profile = recording.flatten()
            rms = self.calculate_rms(recording)
            # Bound ambient_rms to sane noise floor (0.0010 to 0.015)
            self.ambient_rms = max(0.0010, min(0.015, rms))

            # Dynamic threshold: Set safely above the ambient noise floor (1.5x ambient RMS + 0.0015 margin).
            # This allows quiet laptop microphones, built-in mic arrays, and soft voices to trigger easily.
            calculated_threshold = float(self.ambient_rms * 1.5 + 0.0015)
            base_min = float(getattr(settings, "ENERGY_THRESHOLD", 0.0035))
            self.energy_threshold = max(base_min, calculated_threshold)
            self.energy_threshold = min(0.018, self.energy_threshold)

            print(f"[+] Ambient noise RMS: {self.ambient_rms:.4f} -> Speech Trigger Threshold: {self.energy_threshold:.4f} (Mic Sensitivity Optimized)")
            return self.energy_threshold
        except Exception as e:
            print(f"[-] Ambient noise calibration failed ({e}). Using default: {self.energy_threshold}")
            return self.energy_threshold

    def listen_and_record_utterance(
        self,
        max_duration_sec: float = None,
        on_speech_start = None,
        on_volume_chunk = None,
        on_barge_in = None,
    ) -> Optional[bytes]:
        """
        Streams audio frames until sustained speech is detected, records while speech continues,
        and finishes when silence lasts for `silence_duration`.
        Uses multi-frame onset gating and dynamic hysteresis to reject background noise.
        Supports on_barge_in to instantly interrupt ongoing Willy speech when human speaks.
        Returns audio as in-memory WAV bytes.
        """
        if sd is None:
            raise RuntimeError("sounddevice is not installed or no audio host API found.")

        self._abort_requested.clear()
        max_duration = max_duration_sec or settings.MAX_RECORDING_DURATION
        silence_chunks_limit = int(self.silence_duration / (self.chunk_size / self.sample_rate))
        max_total_chunks = int(max_duration / (self.chunk_size / self.sample_rate))

        # Hysteresis thresholds:
        # 1. speech_start_threshold: must be exceeded to begin recording
        # 2. speech_cutoff_threshold: must drop below this to count as silence
        speech_start_threshold = self.energy_threshold
        speech_cutoff_threshold = max(self.ambient_rms * 1.25 + 0.002, self.energy_threshold * 0.60)

        self.pre_buffer.clear()
        recorded_chunks = []
        speech_started = False
        consecutive_speech_chunks = 0
        consecutive_silence = 0

        # Start microphone input stream with automatic PortAudio recovery
        stream = None
        try:
            for attempt in range(2):
                try:
                    stream = sd.InputStream(
                        samplerate=self.sample_rate,
                        channels=self.channels,
                        dtype="float32",
                        blocksize=self.chunk_size,
                    )
                    stream.start()
                    break
                except Exception as e:
                    err_str = str(e)
                    if ("-9988" in err_str or "stream pointer" in err_str.lower() or "portaudio" in err_str.lower()) and attempt == 0:
                        try:
                            sd._terminate()
                            sd._initialize()
                        except Exception:
                            pass
                        time.sleep(0.15)
                    else:
                        raise

            while True:
                if self._abort_requested.is_set():
                    return None

                try:
                    data, overflowed = stream.read(self.chunk_size)
                except Exception as e:
                    if "-9988" in str(e) or "stream pointer" in str(e).lower():
                        try:
                            sd._terminate()
                            sd._initialize()
                        except Exception:
                            pass
                    break
                # Calculate energy on voice-band filtered audio
                energy = self.calculate_rms(data)

                # Feed real-time volume to UI visualizer if callback provided
                if on_volume_chunk is not None:
                    try:
                        on_volume_chunk(energy)
                    except Exception:
                        pass

                # Acoustic Echo Suppression & Speaker Gate:
                # While Willy's speaker is actively playing sound, ignore all microphone input
                # to prevent Willy from hearing its own voice and cutting itself off.
                if self.is_speaker_active:
                    enable_barge_in = getattr(settings, "ENABLE_ACOUSTIC_BARGE_IN", False)
                    if enable_barge_in and on_barge_in is not None:
                        bargein_threshold = max(self.energy_threshold * 2.5, self.ambient_rms * 4.0 + 0.05)
                        if energy > bargein_threshold:
                            consecutive_speech_chunks += 1
                            if consecutive_speech_chunks >= 4:
                                self.is_speaker_active = False
                                try:
                                    on_barge_in()
                                except Exception:
                                    pass
                                speech_started = True
                                recorded_chunks.clear()
                                recorded_chunks.append(data.copy())
                                consecutive_silence = 0
                                if on_speech_start:
                                    on_speech_start()
                                continue
                        else:
                            consecutive_speech_chunks = 0
                    else:
                        consecutive_speech_chunks = 0

                    self.pre_buffer.clear()
                    recorded_chunks.clear()
                    speech_started = False
                    consecutive_silence = 0
                    continue

                if not speech_started:
                    self.pre_buffer.append(data.copy())
                    
                    # Multi-frame onset gate: Require 2 consecutive frames (~60ms) above threshold
                    # This filters isolated mouse clicks while ensuring soft words like "Willy" are never missed.
                    if energy > speech_start_threshold:
                        consecutive_speech_chunks += 1
                        if consecutive_speech_chunks >= 2:
                            speech_started = True
                            if on_speech_start:
                                on_speech_start()
                            # Prepend pre-buffer so beginning syllables (e.g. "Hey") aren't clipped
                            recorded_chunks.extend(list(self.pre_buffer))
                            consecutive_silence = 0
                    else:
                        consecutive_speech_chunks = 0
                        # Gently track slowly drifting background noise floor when quiet
                        if energy < self.energy_threshold * 0.7:
                            self.ambient_rms = self.ambient_rms * 0.98 + energy * 0.02
                            speech_cutoff_threshold = max(self.ambient_rms * 1.25 + 0.002, self.energy_threshold * 0.60)

                else:
                    # Currently recording active utterance
                    recorded_chunks.append(data.copy())
                    if energy < speech_cutoff_threshold:
                        consecutive_silence += 1
                        if consecutive_silence >= silence_chunks_limit:
                            # User finished speaking
                            break
                    else:
                        consecutive_silence = 0

                    if len(recorded_chunks) >= max_total_chunks:
                        # Reached maximum recording duration safety limit
                        break
        finally:
            if stream is not None:
                try:
                    stream.stop()
                except Exception:
                    pass
                try:
                    stream.close()
                except Exception:
                    pass

        # Discard transient noises shorter than MIN_SPEECH_DURATION
        min_chunks_required = int(float(settings.MIN_SPEECH_DURATION) / (self.chunk_size / self.sample_rate))
        if not recorded_chunks or len(recorded_chunks) < max(5, min_chunks_required):
            return None

        # Concatenate raw recorded chunks
        audio_array = np.concatenate(recorded_chunks, axis=0).flatten()

        # Check overall voice energy: ensure utterance actually contains speech above ambient floor
        overall_rms = float(np.sqrt(np.mean(audio_array**2)))
        if overall_rms < (self.ambient_rms * 1.02):
            # Only background room noise was recorded, discard
            return None

        # Spectral Noise Suppression: remove stationary background hum, fan noise, and hiss
        if nr is not None and getattr(settings, "NOISE_SUPPRESSION", True):
            try:
                noise_profile = self.ambient_profile
                # If ambient profile not captured, use earliest pre-buffer frames as noise sample
                if noise_profile is None and len(self.pre_buffer) >= 3:
                    noise_profile = np.concatenate(list(self.pre_buffer)[:3], axis=0).flatten()

                if noise_profile is not None and len(noise_profile) > 500 and len(audio_array) > 1600:
                    audio_array = nr.reduce_noise(
                        y=audio_array,
                        sr=self.sample_rate,
                        y_noise=noise_profile,
                        prop_decrease=0.75,
                        stationary=True,
                    )
            except Exception:
                pass

        # Normalize and convert float32 to 16-bit PCM WAV
        audio_int16 = (np.clip(audio_array, -1.0, 1.0) * 32767).astype(np.int16)

        # Build in-memory WAV buffer
        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, "wb") as wf:
            wf.setnchannels(self.channels)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(self.sample_rate)
            wf.writeframes(audio_int16.tobytes())

        return wav_buffer.getvalue()

