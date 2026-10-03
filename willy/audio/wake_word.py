"""
Wake-word detection, conversation session manager, and pause/standby controller for Willy.
"""

import time
import re
import difflib
import threading
import collections
from typing import Tuple, Optional
from willy.config import settings, get_wake_word_list

# Global thread-safe record of Willy's recent spoken phrases for acoustic echo rejection
_RECENT_SPOKEN_PHRASES = collections.deque(maxlen=15)  # stores (normalized_text, timestamp)
_SPEECH_LOCK = threading.Lock()


def record_willy_speech(text: str):
    """Registers text spoken by Willy to ensure microphone echo is ignored."""
    if not text or not text.strip():
        return
    normalized = re.sub(r'[^\w\s]', '', text.lower()).strip()
    if normalized:
        with _SPEECH_LOCK:
            _RECENT_SPOKEN_PHRASES.append((normalized, time.time()))


def clear_willy_speech():
    """Clears registered spoken phrases."""
    with _SPEECH_LOCK:
        _RECENT_SPOKEN_PHRASES.clear()


def is_willy_self_speech(text: str, ttl_seconds: float = 12.0) -> bool:
    """
    Returns True if the transcribed text matches or substantially overlaps with
    speech recently output by Willy through the speakers.
    """
    if not text or not text.strip():
        return False

    cleaned = re.sub(r'[^\w\s]', '', text.lower()).strip()
    if not cleaned:
        return False

    now = time.time()
    tokens_transcribed = set(re.findall(r'\w+', cleaned))

    with _SPEECH_LOCK:
        recent_phrases = list(_RECENT_SPOKEN_PHRASES)

    for stored_text, timestamp in recent_phrases:
        if (now - timestamp) > ttl_seconds:
            continue

        # 1. Exact match
        if cleaned == stored_text:
            return True

        # 2. Substring match (e.g. mic caught the start or end of Willy's sentence)
        if len(cleaned) >= 8 and (cleaned in stored_text or stored_text in cleaned):
            return True

        # 3. High word-overlap: if >= 60% of words in the transcribed phrase match the spoken sentence (or 100% for 2 words)
        tokens_stored = set(re.findall(r'\w+', stored_text))
        if tokens_transcribed and tokens_stored:
            overlap = tokens_transcribed & tokens_stored
            if len(tokens_transcribed) >= 3 and (len(overlap) / len(tokens_transcribed)) >= 0.60:
                return True
            if len(tokens_transcribed) == 2 and len(overlap) == 2:
                return True

        # 4. Fuzzy SequenceMatcher similarity (handles slight Whisper transcription differences)
        if len(cleaned) >= 8 and len(stored_text) >= 8:
            ratio = difflib.SequenceMatcher(None, cleaned, stored_text).ratio()
            if ratio >= 0.65:
                return True

    return False


class WakeWordDetector:
    """
    Manages wake-word recognition, pause/standby mode, and conversational sessions.
    - Continuous mic: always processes transcripts.
    - Willy Pause: enters standby until 'Willy' or 'Hey Willy' is spoken.
    - Active Session: stays open for follow-ups without repeating wake word.
    - Self-Speech Filter: ignores acoustic echo of Willy's own speech.
    """

    def __init__(self, timeout_seconds: float = None):
        self.wake_words = get_wake_word_list()
        self.timeout_seconds = timeout_seconds or settings.CONVERSATION_TIMEOUT
        self.last_active_time = 0.0
        self.is_session_active = False
        self.is_paused = False

    def record_spoken_phrase(self, text: str):
        """Registers a spoken phrase in the self-speech filter."""
        record_willy_speech(text)

    def is_self_speech(self, text: str, ttl_seconds: float = 12.0) -> bool:
        """Checks if the given text matches Willy's recent spoken responses."""
        return is_willy_self_speech(text, ttl_seconds=ttl_seconds)

    def pause(self):
        """Puts Willy into quiet standby mode."""
        self.is_paused = True
        self.end_session()

    def resume(self):
        """Wakes Willy up from standby mode."""
        self.is_paused = False
        self.refresh_session()

    def is_in_active_session(self) -> bool:
        """Returns True if Willy is currently in an ongoing conversation session."""
        if self.is_paused:
            return False
        if not self.is_session_active:
            return False
        if (time.time() - self.last_active_time) <= self.timeout_seconds:
            return True
        # Session expired
        self.is_session_active = False
        return False

    def refresh_session(self):
        """Refreshes the active session timer."""
        self.is_session_active = True
        self.last_active_time = time.time()

    def end_session(self):
        """Manually ends the conversation session."""
        self.is_session_active = False
        self.last_active_time = 0.0

    def process_transcript(self, text: str) -> Tuple[bool, Optional[str]]:
        """
        Evaluates transcribed text.

        Returns:
            (is_triggered, extracted_command)
            Special commands:
              "__PAUSE__": User requested Willy to pause/sleep
              "__RESUME__": User woke Willy from pause with just the wake word
        """
        if not text:
            return False, None

        # Ignore acoustic echo of Willy's own speech
        if self.is_self_speech(text):
            print(f"[Echo Filter] Ignored transcript matching Willy's own speech: '{text.strip()}'")
            return False, None

        normalized = text.lower().strip()
        cleaned = re.sub(r'[^\w\s]', '', normalized)

        # Common phonetic variations of "Willy" and wake triggers that STT models generate (English)
        phonetic_willy_pattern = (
            r'(?:(?:^|\s|\b)(?:hey|hi|hello|ok|okay|yo|listen|hay)\s+)?'
            r'(?:willy|willie|wili|willi|wheelie|billy|villy|wiley|wellie|whily|will\s+he|wilde|woolly)(?:\s+(?:wake\s*up))?(?:\s|\b|$)'
            r'|(?:^|\s|\b)(?:wake\s*up)(?:\s|\b|$)'
        )

        # -------------------------------------------------------------
        # 1. Check for Pause / Sleep command (English)
        # -------------------------------------------------------------
        pause_explicit = re.search(
            r'(?:^|\s|\b)(?:willy|willie|wili|heavily)\s+(?:pause|stop listening|go to sleep|take a break|be quiet|stay quiet)(?:\s|\b|$)',
            cleaned
        )
        pause_standby = re.search(
            r'^(?:pause|stop listening|go to sleep|take a break|be quiet|stay quiet|shut up)$',
            cleaned
        )

        # If user says "willy pause" or if active and user says "pause"
        if pause_explicit or (self.is_in_active_session() and pause_standby):
            self.pause()
            return True, "__PAUSE__"

        # -------------------------------------------------------------
        # 2. Standby / Paused Mode Handling
        # -------------------------------------------------------------
        if self.is_paused:
            # While paused, Willy MUST keep completely quiet unless "Willy" / "Hey Willy" is called
            match = re.search(phonetic_willy_pattern, cleaned)
            matched_wake = False
            end_idx = 0
            if match:
                matched_wake = True
                end_idx = match.end()
            else:
                for ww in self.wake_words:
                    pattern = rf'(?:^|\s|\b){re.escape(ww)}(?:\s|\b|$)'
                    m = re.search(pattern, cleaned)
                    if m:
                        matched_wake = True
                        end_idx = m.end()
                        break

            if not matched_wake:
                # Keep completely quiet while paused
                return False, None

            # Wake word was spoken! Unpause and resume
            self.resume()
            command = cleaned[end_idx:].strip()
            command = re.sub(r'^(?:please|can you|could you|would you)\s+', '', command).strip()
            if command and self.is_self_speech(command):
                return True, "__RESUME__"
            return True, command if command else "__RESUME__"

        # -------------------------------------------------------------
        # 3. Active Session Mode Handling
        # -------------------------------------------------------------
        if self.is_in_active_session():
            # Strip any repeated wake word
            cleaned_cmd = re.sub(phonetic_willy_pattern, '', cleaned).strip()
            if self.is_self_speech(cleaned_cmd):
                print(f"[Echo Filter] Ignored active session command matching Willy's speech: '{cleaned_cmd}'")
                return False, None
            # If user just repeated the wake word during active session, return empty so it greets rather than queries LLM
            if not cleaned_cmd:
                self.refresh_session()
                return True, ""
            # Discard tiny single-character noise artifacts
            letters = [c for c in cleaned_cmd if c.isalpha()]
            if len(letters) < 2:
                return False, None
            self.refresh_session()
            return True, cleaned_cmd

        # -------------------------------------------------------------
        # 4. Normal Dormant Wake Word Detection
        # -------------------------------------------------------------
        match = re.search(phonetic_willy_pattern, cleaned)
        if match:
            end_idx = match.end()
            command = cleaned[end_idx:].strip()
            # Strip any repeated wake words from the remaining command ("Willy, Willy" -> "")
            command = re.sub(phonetic_willy_pattern, '', command).strip()
            command = re.sub(r'^(?:\b(?:please|can you|could you|would you|and|wake\s*up)\s+)+', '', command).strip()
            if command and self.is_self_speech(command):
                print(f"[Echo Filter] Ignored command matching Willy's speech: '{command}'")
                return False, None
            self.refresh_session()
            return True, command if command else ""

        # Fallback check against configured wake_words list
        for ww in self.wake_words:
            pattern = rf'(?:^|\s|\b){re.escape(ww)}(?:\s|\b|$)'
            match = re.search(pattern, cleaned)
            if match:
                self.refresh_session()
                end_idx = match.end()
                command = cleaned[end_idx:].strip()
                command = re.sub(phonetic_willy_pattern, '', command).strip()
                command = re.sub(r'^(?:\b(?:please|can you|could you|would you|and|wake\s*up)\s+)+', '', command).strip()
                return True, command if command else ""

        return False, None
