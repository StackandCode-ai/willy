"""
Tests for Willy's acoustic echo rejection and self-speech filtering.
Verifies that Willy ignores its own spoken words and does not interrupt itself.
"""

import sys
import os
import time
import numpy as np

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from willy.audio.wake_word import WakeWordDetector, record_willy_speech, is_willy_self_speech
from willy.audio.listener import AudioListener
from willy.config import settings


def test_self_speech_exact_match():
    print("[*] Testing exact match self-speech rejection...")
    phrase = "The current time is 9:30 PM."
    record_willy_speech(phrase)
    
    assert is_willy_self_speech("The current time is 9:30 PM.") is True
    assert is_willy_self_speech("the current time is 930 pm") is True
    assert is_willy_self_speech("What is the weather outside?") is False
    print("    -> PASS: Exact match detected.")


def test_self_speech_substring_and_fragments():
    print("[*] Testing substring and sentence fragment echo rejection...")
    long_response = "I have opened Google Chrome and navigated to YouTube for you."
    record_willy_speech(long_response)

    # Microphone only captures trailing or leading part of Willy's response
    assert is_willy_self_speech("navigated to YouTube for you") is True
    assert is_willy_self_speech("opened Google Chrome and") is True
    assert is_willy_self_speech("I have opened Google Chrome") is True
    print("    -> PASS: Substring fragments rejected.")


def test_self_speech_word_overlap_and_fuzzy():
    print("[*] Testing word overlap and fuzzy phonetic match...")
    response = "Willy is online and ready to assist you with Windows tasks."
    record_willy_speech(response)

    # Whisper slightly mishears its own output
    slight_variation = "Willy is online and ready to assist you"
    assert is_willy_self_speech(slight_variation) is True

    # Real human command should NOT match
    real_command = "Open my Downloads folder"
    assert is_willy_self_speech(real_command) is False
    print("    -> PASS: Word overlap and fuzzy match verified.")


def test_active_session_does_not_trigger_on_self_speech():
    print("[*] Testing that active session ignores Willy's own speech...")
    detector = WakeWordDetector()
    detector.refresh_session()
    assert detector.is_in_active_session() is True

    # Willy says something
    willy_words = "Here is your system hardware report."
    detector.record_spoken_phrase(willy_words)

    # Microphone feeds Willy's words back into process_transcript
    triggered, cmd = detector.process_transcript("Here is your system hardware report.")
    assert triggered is False
    assert cmd is None

    # But user giving a fresh command DOES trigger
    triggered, cmd = detector.process_transcript("close that window")
    assert triggered is True
    assert cmd == "close that window"
    print("    -> PASS: Active session safely ignores self-speech and accepts user commands.")


def test_listener_ignores_speaker_active():
    print("[*] Testing AudioListener acoustic speaker gate...")
    listener = AudioListener()
    listener.set_speaker_active(True)
    assert listener.is_speaker_active is True

    # Listener pre_buffer is cleared when speaker is active
    listener.pre_buffer.append(np.ones(100))
    listener.set_speaker_active(True)
    assert len(listener.pre_buffer) == 0

    listener.set_speaker_active(False)
    assert listener.is_speaker_active is False
    print("    -> PASS: AudioListener speaker gate verified.")


if __name__ == "__main__":
    print("=" * 60)
    print("RUNNING ACOUSTIC ECHO & SELF-SPEECH FILTER TESTS")
    print("=" * 60)
    test_self_speech_exact_match()
    test_self_speech_substring_and_fragments()
    test_self_speech_word_overlap_and_fuzzy()
    test_active_session_does_not_trigger_on_self_speech()
    test_listener_ignores_speaker_active()
    print("=" * 60)
    print("ALL ACOUSTIC ECHO TESTS PASSED SUCCESSFULLY!")
    print("=" * 60)
