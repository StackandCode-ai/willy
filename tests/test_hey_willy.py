""""Hey Willy" on the PC (pc_client/hey_willy.py) without a microphone."""

import array
import io
import wave

from pc_client import hey_willy
from pc_client.hey_willy import HeyWilly, is_wake, wav_bytes, BLOCK


def _chunk(level: int) -> bytes:
    return array.array("h", [level, -level] * (BLOCK // 2)).tobytes()


def _final(*pairs):
    return {"text": " ".join(w for w, _ in pairs), "result": [{"word": w, "conf": c} for w, c in pairs]}


def test_wake_phrase_needs_both_words_said_clearly():
    from pc_client.hey_willy import wake_position
    assert wake_position(_final(("hey", 1.0), ("willy", 0.95))) == "alone"
    assert wake_position(_final(("hey", 0.9), ("willy", 0.97), ("[unk]", 1), ("[unk]", 1))) == "with_command"
    assert wake_position(_final(("willy", 1.0))) is None                      # the name alone
    assert wake_position(_final(("hey", 1.0), ("willy", 0.35))) is None       # mumbled
    assert wake_position(_final(("really", 1.0), ("silly", 1.0))) is None
    assert wake_position({"partial": "hey willy"}) is None                     # no partial triggers
    assert not is_wake(_final(("hey", 1.0), ("billy", 1.0)))


def test_wav_bytes_is_16k_mono():
    with wave.open(io.BytesIO(wav_bytes(_chunk(500) * 3))) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (16000, 1, 2)


def test_recording_stops_after_a_pause_and_ignores_silence():
    hw = HeyWilly(lambda *a: None, lambda r: None)
    for c in [_chunk(4000)] * 5 + [_chunk(10)] * 12:  # speech, then a pause
        hw._audio.put(c)
    pcm = hw._record([], start_timeout=2)
    assert pcm is not None and len(pcm) >= 5 * BLOCK * 2
    for c in [_chunk(10)] * 30:  # nobody speaks
        hw._audio.put(c)
    assert hw._record([], start_timeout=0.5) is None


def _voice(monkeypatch, replies_from_hub, recordings):
    states, shown, spoken, sent = [], [], [], []
    hw = HeyWilly(lambda s, d: states.append(s), shown.append)
    hub = iter(replies_from_hub)
    monkeypatch.setattr(hey_willy, "post_voice", lambda pcm, wake=False: sent.append((pcm, wake)) or next(hub))
    monkeypatch.setattr(hw, "_speak", lambda reply: spoken.append(reply["reply"]))
    monkeypatch.setattr(hw, "_chime", lambda rising=True: None)
    rec = iter(recordings)
    monkeypatch.setattr(hw, "_record", lambda preroll, timeout, min_level=0.0: next(rec))
    return hw, states, shown, spoken, sent


def test_wake_word_alone_records_the_command_and_speaks_the_reply(monkeypatch):
    hw, states, shown, spoken, sent = _voice(monkeypatch, [{"reply": "It's 8 PM.", "audio_base64": "QQ=="}],
                                             [b"what time is it", None])  # then silence
    hw._converse(b"hey willy ", with_command=False)
    assert sent == [(b"hey willy what time is it", True)] and spoken == ["It's 8 PM."]
    assert states[0] == "listening" and states[-1] == "idle"


def test_command_in_one_breath_is_checked_by_the_hub_and_false_wakes_stay_silent(monkeypatch):
    hw, states, shown, spoken, sent = _voice(monkeypatch, [{"success": True, "ignored": True, "reply": ""}], [])
    hw._converse(b"room chatter", with_command=True)
    assert sent == [(b"room chatter", True)] and spoken == [] and shown == []  # dropped quietly


def test_conversation_continues_until_silence_or_goodbye(monkeypatch):
    replies = [{"reply": "It's 8 PM.", "transcript": "what time is it"},
               {"reply": "Volume is 30.", "transcript": "set volume to 30"},
               {"reply": "Opened Chrome.", "transcript": "open chrome"}]
    hw, states, shown, spoken, sent = _voice(monkeypatch, replies,
                                             [b"what time is it", b"set volume to 30", b"open chrome", None])
    hw._converse(None, with_command=False, manual=True)
    assert [p for p, _ in sent] == [b"what time is it", b"set volume to 30", b"open chrome"]  # 3 turns, no wake word
    assert states[-1] == "idle"
    hw2, _, _, spoken2, sent2 = _voice(monkeypatch, [{"reply": "You're welcome!", "transcript": "thanks Willy"}],
                                       [b"thanks willy", b"room talk"])
    hw2._converse(None, with_command=False, manual=True)
    assert [p for p, _ in sent2] == [b"thanks willy"] and spoken2 == ["You're welcome!"]  # goodbye ends it


def test_training_learns_thresholds_from_the_users_voice():
    from pc_client.hey_willy import learn_profile, wake_position
    profile = learn_profile([{"hey_conf": 1.0, "name_conf": 0.51, "level": 2400},
                             {"hey_conf": 0.9, "name_conf": 0.62, "level": 3000},
                             {"hey_conf": 1.0, "name_conf": 0.58, "level": 2700}])
    assert profile["name_conf"] == 0.43 and profile["min_level"] == 1080.0
    mine = _final(("hey", 1.0), ("willy", 0.47))
    assert wake_position(mine, profile) == "alone"           # this voice now wakes it
    assert wake_position(_final(("hey", 1.0), ("willy", 0.2)), profile) is None


def test_quiet_far_away_speech_does_not_wake_a_trained_willy(monkeypatch):
    hw = HeyWilly(lambda *a: None, lambda r: None)
    hw.profile = {"name_conf": 0.4, "hey_conf": 0.3, "min_level": 2000}
    woke = []
    monkeypatch.setattr(hw, "_converse", lambda spoken, with_command, manual=False: woke.append(spoken))

    class Rec:  # a recognizer that always "hears" hey willy
        def AcceptWaveform(self, _chunk):
            return True

        def Result(self):
            return '{"result": [{"word": "hey", "conf": 1.0}, {"word": "willy", "conf": 0.9}]}'

    monkeypatch.setattr(hw, "_recognizer", lambda model: Rec())
    hw._audio.put(_chunk(300))   # TV across the room
    hw._audio.put(_chunk(5000))  # the user, close to the mic
    calls = {"n": 0}
    real_get = hw._audio.get

    def get(timeout=0.5):
        calls["n"] += 1
        if calls["n"] > 2:
            hw._stop.set()
            raise __import__("queue").Empty
        return real_get(timeout=timeout)

    monkeypatch.setattr(hw._audio, "get", get)
    hw._listen_loop(model=None)
    assert len(woke) == 1  # only the loud, close voice
