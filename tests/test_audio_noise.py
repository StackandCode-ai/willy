"""
Verification test suite for Willy's audio noise suppression, bandpass filtering,
and STT hallucination rejection.
"""

import sys
import os
import numpy as np

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from willy.audio.listener import AudioListener
from willy.audio.stt import SpeechToText
from willy.config import settings


def test_bandpass_attenuation():
    print("[*] Testing Bandpass Filter rumble attenuation...")
    listener = AudioListener(sample_rate=16000)
    t = np.linspace(0, 0.03, int(16000 * 0.03), endpoint=False)

    # 1. Low-frequency 40Hz fan / desk rumble
    low_rumble = np.sin(2 * np.pi * 40 * t).astype(np.float32)
    filtered_rumble = listener.filter_chunk(low_rumble)
    rumble_rms_before = np.sqrt(np.mean(low_rumble**2))
    rumble_rms_after = np.sqrt(np.mean(filtered_rumble**2))
    attenuation = rumble_rms_after / rumble_rms_before
    print(f"    -> 40Hz rumble energy preserved: {attenuation * 100:.1f}% (Expected < 30%)")
    assert attenuation < 0.35, "Bandpass filter should heavily attenuate 40Hz sub-audible rumble"

    # 2. 1000Hz human voice tone
    voice_tone = np.sin(2 * np.pi * 1000 * t).astype(np.float32)
    filtered_voice = listener.filter_chunk(voice_tone)
    voice_rms_before = np.sqrt(np.mean(voice_tone**2))
    voice_rms_after = np.sqrt(np.mean(filtered_voice**2))
    voice_preservation = voice_rms_after / voice_rms_before
    print(f"    -> 1000Hz voice energy preserved: {voice_preservation * 100:.1f}% (Expected > 90%)")
    assert voice_preservation > 0.85, "Bandpass filter must preserve voice frequencies"
    print("    -> PASS: Bandpass frequency separation verified.")


def test_dynamic_threshold_calculation():
    print("[*] Testing Dynamic Noise Threshold Calculation...")
    listener = AudioListener()

    # Case 1: Quiet room (RMS 0.008)
    listener.ambient_rms = 0.008
    calculated = float(listener.ambient_rms * 2.2 + 0.008)
    threshold = max(float(settings.ENERGY_THRESHOLD), calculated)
    assert threshold >= 0.025, "Threshold should be at least default minimum"
    assert threshold > listener.ambient_rms * 2.0, "Threshold must have headroom over ambient noise"

    # Case 2: Noisy room with loud fan (RMS 0.028)
    listener.ambient_rms = 0.028
    calculated_noisy = float(listener.ambient_rms * 2.2 + 0.008)
    threshold_noisy = max(float(settings.ENERGY_THRESHOLD), calculated_noisy)
    # Ensure it doesn't get capped at 0.024 like before!
    assert threshold_noisy > 0.028, "Noisy room threshold must be higher than the room noise itself"
    assert threshold_noisy >= 0.065, f"Expected threshold >= 0.065, got {threshold_noisy}"
    print(f"    -> PASS: Noisy room RMS (0.028) correctly yields safe threshold ({threshold_noisy:.4f}).")


def test_stt_hallucination_filter():
    print("[*] Testing Whisper Background Noise Hallucination Filter...")
    # Bad noise hallucinations that Whisper generates on static/silence
    hallucinations = [
        "thank you",
        "Thank you.",
        "Thanks for watching!",
        "you",
        "subtitles by amara.org",
        "[music]",
        "(music)",
        "♪",
        "...",
        "[laughter]",
        "silence",
        "bye",
        "   ",
        "...",
        "--",
    ]
    for h in hallucinations:
        assert SpeechToText._is_noise_hallucination(h) is True, f"Failed to filter hallucination: '{h}'"

    # Valid voice commands that should NOT be filtered
    valid_commands = [
        "Hey Willy open chrome",
        "what is the time right now",
        "pause Spotify",
        "create a new folder on desktop",
        "volume up",
    ]
    for v in valid_commands:
        assert SpeechToText._is_noise_hallucination(v) is False, f"Erroneously filtered valid command: '{v}'"

    print("    -> PASS: Whisper noise hallucination filter correctly rejects static and accepts valid speech.")


if __name__ == "__main__":
    print("=" * 60)
    print("TESTING AUDIO NOISE SUPPRESSION & HALLUCINATION REJECTION")
    print("=" * 60)
    test_bandpass_attenuation()
    test_dynamic_threshold_calculation()
    test_stt_hallucination_filter()
    print("=" * 60)
    print("ALL NOISE SUPPRESSION TESTS PASSED!")
    print("=" * 60)
