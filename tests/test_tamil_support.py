"""
Test suite for English-only support in Willy.
Verifies wake word triggers, pause commands, and English TTS voice selection.
"""

import sys
import os

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from willy.audio.wake_word import WakeWordDetector
from willy.audio.tts import TextToSpeech
from willy.config import settings


def test_english_wake_word_detector():
    print("[*] Testing English WakeWordDetector...")
    detector = WakeWordDetector(timeout_seconds=5.0)

    # 1. English Wake Word
    triggered, cmd = detector.process_transcript("Hey Willy open notepad please")
    assert triggered is True, "English wake word 'Hey Willy' should trigger"
    assert "open notepad" in cmd, f"Expected 'open notepad', got '{cmd}'"
    print("    -> PASS: English wake word & command extraction verified.")

    # 2. English Pause / Sleep Command
    detector.end_session()
    triggered_pause, cmd_pause = detector.process_transcript("willy pause")
    assert triggered_pause is True and cmd_pause == "__PAUSE__", "English 'willy pause' must trigger pause"
    assert detector.is_paused is True, "Detector must enter paused state"
    print("    -> PASS: English pause command ('willy pause') verified.")

    # 3. English Wake from Pause
    triggered_resume, cmd_resume = detector.process_transcript("Hey Willy")
    assert triggered_resume is True and cmd_resume == "__RESUME__", "English 'Hey Willy' should resume from pause"
    assert detector.is_paused is False, "Detector must resume"
    print("    -> PASS: English resume from pause verified.")

    # 4. Non-English triggers should NOT trigger wake word
    detector.end_session()
    triggered_ta, _ = detector.process_transcript("வில்லி கால்குலேட்டர் ஓபன் பண்ணு")
    assert triggered_ta is False, "Non-English wake word should not trigger in English-only mode"
    print("    -> PASS: Non-English input correctly rejected in English-only mode.")


def test_english_tts_voice_selection():
    print("[*] Testing English TTS Voice Selection...")
    tts = TextToSpeech()

    english_samples = [
        "Hello! How can I help you today?",
        "Opening Google Chrome for you right now.",
        "System status is normal. CPU usage is at 12%.",
    ]
    for text in english_samples:
        assert tts.is_tamil_text(text) is False

    # Verify voice configuration
    assert tts.voice in (settings.TTS_VOICE, "en-US-ChristopherNeural", "en-US-BrianMultilingualNeural"), f"Unexpected English voice: {tts.voice}"
    print(f"    -> PASS: English voice selection ({tts.voice}) verified.")


if __name__ == "__main__":
    print("=" * 60)
    print("TESTING WILLY ENGLISH-ONLY SUPPORT")
    print("=" * 60)
    test_english_wake_word_detector()
    test_english_tts_voice_selection()
    print("=" * 60)
    print("ALL ENGLISH TESTS PASSED SUCCESSFULLY!")
    print("=" * 60)
