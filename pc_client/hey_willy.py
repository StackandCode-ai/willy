"""
"Hey Willy" on the PC: an offline wake word, then the spoken command goes to the hub.

    idle      the microphone runs through a small offline Vosk model limited to a few
              words, so only "hey willy" (and look-alikes, which are rejected) can match.
              Nothing leaves the PC in this state.
    listening after the wake word: a chime, then the command is recorded until a pause.
              The ~1.5 s before the trigger is kept, so "hey willy open chrome" said in
              one breath still works.
    thinking  the recording goes to the hub's voice endpoint (speech-to-text, the brain,
              the reply voice), exactly like the phone's voice call.
    speaking  the reply plays on the PC speakers; the mic is ignored meanwhile so Willy
              doesn't hear itself. Then a few seconds of follow-up listening without
              the wake word, and back to idle.
"""

import array
import math
import io
import json
import os
import queue
import secrets
import threading
import time
import wave
import zipfile
from collections import deque
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from pc_client import config

RATE = 16000
BLOCK = 1600  # 100 ms
MODEL_NAME = "vosk-model-small-en-us-0.15"
MODEL_URL = f"https://alphacephei.com/vosk/models/{MODEL_NAME}.zip"
MODELS_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "WillyPC" / "models"
# Only the two-word phrase wakes Willy: "willy" alone turns up in ordinary talk far too often.
# ("willie" is left out: it sounds exactly like "willy", and having both splits the
# recognizer's confidence between them.)
WAKE_PHRASES = ("hey willy", "hey wiley", "ok willy", "okay willy")
# Decoys that sound alike: with them in the grammar, "really", "hey billy" or "will we" are
# recognised as themselves instead of being forced onto the wake phrase.
GRAMMAR = ["hey willy", "hey wiley", "ok willy", "okay willy", "willy",
           "hey", "hello", "okay", "billy", "lily", "silly", "really", "will", "we", "hey billy",
           "hey really", "hey lily", "very", "body", "baby", "[unk]"]
MIN_NAME_CONF = 0.5     # Vosk's confidence for the "willy" word
MIN_HEY_CONF = 0.5      # ... and for "hey" / "ok" (the hub double-checks the name anyway)
MAX_UTTERANCE_SEC = 12.0
START_TIMEOUT_SEC = 5.0      # after the chime, how long to wait for speech
END_SILENCE_SEC = 0.9        # pause that ends a command
MAX_COMMAND_SEC = 12.0
FOLLOW_UP_SEC = 8.0          # after a reply: listen this long for a follow-up without the wake word
# Saying one of these ends the conversation (Willy answers, then goes back to waiting for its name).
END_RE = __import__("re").compile(
    r"\b(?:thanks|thank you|bye|goodbye|good night|stop|that'?s all|that is all|nothing else|never ?mind|"
    r"go to sleep|cancel|that'?s it)\b", __import__("re").I)

StateFn = Callable[[str, str], None]          # (state, detail)
ReplyFn = Callable[[Dict[str, Any]], None]    # hub reply


def _rms(chunk: bytes) -> float:
    """Loudness of a 16-bit mono chunk."""
    samples = array.array("h", chunk[: len(chunk) - len(chunk) % 2])
    return math.sqrt(sum(x * x for x in samples) / len(samples)) if samples else 0.0


def model_path() -> Path:
    return MODELS_DIR / MODEL_NAME


def ensure_model(progress: Optional[Callable[[str], None]] = None) -> Path:
    """Downloads the ~40 MB offline model once (from Vosk's own site)."""
    path = model_path()
    if (path / "am").exists():
        return path
    import urllib.request

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = MODELS_DIR / f"{MODEL_NAME}.zip.part"
    if progress:
        progress("Downloading the offline wake-word model (40 MB)…")
    with urllib.request.urlopen(MODEL_URL, timeout=60) as res, open(tmp, "wb") as out:
        while True:
            chunk = res.read(1 << 20)
            if not chunk:
                break
            out.write(chunk)
    with zipfile.ZipFile(tmp) as zf:
        zf.extractall(MODELS_DIR)
    tmp.unlink(missing_ok=True)
    return path


def wake_position(result: Dict[str, Any], profile: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """For a FINAL recognizer result: None (no wake phrase), "alone" ("hey willy" by itself:
    the command comes next) or "with_command" ("hey willy [unk] [unk]": said in one breath).
    `profile` holds thresholds learned from the user's own voice ("Train Hey Willy")."""
    hey_min = (profile or {}).get("hey_conf", MIN_HEY_CONF)
    name_min = (profile or {}).get("name_conf", MIN_NAME_CONF)
    words = result.get("result") or []
    tokens = [w.get("word", "") for w in words]
    for i in range(len(tokens) - 1):
        pair = f"{tokens[i]} {tokens[i + 1]}"
        if pair in WAKE_PHRASES and words[i].get("conf", 0) >= hey_min \
                and words[i + 1].get("conf", 0) >= name_min:
            return "with_command" if any(t == "[unk]" for t in tokens[i + 2:]) else "alone"
    return None


def is_wake(result: Dict[str, Any], profile: Optional[Dict[str, Any]] = None) -> bool:
    """A final recognizer result that holds the wake phrase, said clearly enough."""
    return wake_position(result, profile) is not None


def speech_level(pcm: bytes) -> float:
    """How loud the speech in a clip is: the 90th percentile of its 100 ms loudness."""
    step = BLOCK * 2
    levels = sorted(_rms(pcm[i:i + step]) for i in range(0, len(pcm) - step + 1, step))
    return levels[int(len(levels) * 0.9)] if levels else 0.0


def learn_profile(samples: list) -> Dict[str, Any]:
    """Wake thresholds from the user's recordings of "Hey Willy".
    samples: [{"hey_conf", "name_conf", "level"}] for the clips where the phrase was found."""
    names = sorted(x["name_conf"] for x in samples)
    heys = sorted(x["hey_conf"] for x in samples)
    levels = sorted(x["level"] for x in samples)
    return {
        # a little under the user's weakest clear try, never so low that anything passes
        "name_conf": round(max(0.3, names[0] - 0.08), 2),
        "hey_conf": round(max(0.25, heys[0] - 0.1), 2),
        # the user's voice at the mic; much quieter speech (TV, people across the room) is ignored
        "min_level": round(levels[len(levels) // 2] * 0.4, 1),
        "samples": len(samples),
        "trained_at": time.time(),
    }


def wav_bytes(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)
    return buf.getvalue()


def post_voice(pcm: bytes, session_id: str = "voice_pc", wake: bool = False) -> Dict[str, Any]:
    """Sends one utterance to the hub's voice endpoint; returns its JSON reply. With `wake`,
    the hub drops the clip (reply {"ignored": true}) unless its own speech-to-text also hears
    the name - a second check against false wake-ups."""
    from pc_client.tools.file_relay import _connect, _headers, _hub

    body_audio = wav_bytes(pcm)
    boundary = "----willy" + secrets.token_hex(10)
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"command.wav\"\r\n"
            "Content-Type: audio/wav\r\n\r\n").encode() + body_audio + f"\r\n--{boundary}--\r\n".encode()
    _, _, _, base = _hub()
    conn = _connect(timeout=60)
    try:
        conn.request("POST", f"{base}/api/v1/call/interact?session_id={session_id}" + ("&wake=1" if wake else ""),
                     body=body, headers={
            **_headers(), "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body))})
        res = conn.getresponse()
        data = res.read()
    finally:
        conn.close()
    try:
        out = json.loads(data)
    except ValueError:
        out = {"success": False, "error": f"The hub answered {res.status}."}
    if res.status != 200 and "error" not in out:
        out["error"] = f"The hub answered {res.status}."
    return out


class HeyWilly:
    def __init__(self, on_state: StateFn, on_reply: ReplyFn, log: Callable[[str], None] = print):
        self.on_state = on_state
        self.on_reply = on_reply
        self.log = log
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._audio: "queue.Queue[bytes]" = queue.Queue(maxsize=600)
        self.muted = False
        self.paused = False          # the PC is asleep, or Willy is speaking
        self._noise = 300.0          # running estimate of the room's background level (RMS)
        self._manual = False
        self.profile: Optional[Dict[str, Any]] = None  # learned from the user's voice
        self._train_request = 0
        self.on_trained: Optional[Callable[[Dict[str, Any]], None]] = None
        self.state = "off"

    # --------------------------------------------------------------- control
    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="hey-willy", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
        self._thread = None
        self._set("off")

    def set_muted(self, muted: bool) -> None:
        self.muted = muted
        self._set("muted" if muted else "idle")

    def trigger(self) -> None:
        """Start listening for a command now, as if the wake word was heard (tray / hotkey)."""
        self._manual = True

    def train(self, count: int = 5) -> None:
        """Record the user saying "Hey Willy" `count` times and learn their thresholds."""
        self._train_request = count

    # --------------------------------------------------------------- internals
    def _set(self, state: str, detail: str = "") -> None:
        if state != self.state or state in ("error", "loading"):
            print(time.strftime("%H:%M:%S") + f" [Voice] {state}" + (f": {detail}" if detail and state != "speaking" else ""),
                  flush=True)
        self.state = state
        try:
            self.on_state(state, detail)
        except Exception:
            pass

    def _callback(self, indata, _frames, _time, _status) -> None:
        if self.muted or self.paused:
            return
        try:
            self._audio.put_nowait(bytes(indata))
        except queue.Full:
            pass

    def _drain(self) -> None:
        while True:
            try:
                self._audio.get_nowait()
            except queue.Empty:
                return

    def _run(self) -> None:
        try:
            import sounddevice as sd
            import vosk
        except Exception as e:  # noqa: BLE001
            self._set("error", f"Voice libraries are missing: {e}")
            return
        try:
            path = ensure_model(lambda msg: self._set("loading", msg))
            vosk.SetLogLevel(-1)
            model = vosk.Model(str(path))
        except Exception as e:  # noqa: BLE001
            self._set("error", f"Couldn't load the wake-word model: {e}")
            return
        while not self._stop.is_set():
            try:
                with sd.RawInputStream(samplerate=RATE, blocksize=BLOCK, dtype="int16", channels=1,
                                       callback=self._callback):
                    self._set("muted" if self.muted else "idle")
                    self._listen_loop(model)
            except Exception as e:  # noqa: BLE001 - no microphone, device unplugged...
                self._set("error", f"Microphone unavailable: {e}")
                if self._stop.wait(5):
                    return

    def _recognizer(self, vosk_model):
        import vosk

        rec = vosk.KaldiRecognizer(vosk_model, RATE, json.dumps(GRAMMAR))
        rec.SetWords(True)
        return rec

    def _listen_loop(self, model) -> None:
        rec = self._recognizer(model)
        # Audio of the phrase being heard right now (since the recognizer's last final result),
        # so "hey willy, open chrome" said in one breath goes to the hub whole.
        utterance: deque = deque(maxlen=int(MAX_UTTERANCE_SEC * RATE / BLOCK))
        pending_final = 0  # chunks to wait after the name appears in a partial result
        while not self._stop.is_set():
            if self._train_request:
                count, self._train_request = self._train_request, 0
                outcome = self._train(model, count)
                if self.on_trained:
                    try:
                        self.on_trained(outcome)
                    except Exception:
                        pass
                utterance.clear()
                rec = self._recognizer(model)
                self._set("muted" if self.muted else "idle")
                continue
            if self._manual:
                self._manual = False
                self._converse(None, with_command=False, manual=True)
                utterance.clear()
                rec = self._recognizer(model)
                continue
            try:
                chunk = self._audio.get(timeout=0.5)
            except queue.Empty:
                continue
            utterance.append(chunk)
            self._noise = 0.98 * self._noise + 0.02 * min(_rms(chunk), 3000)
            if rec.AcceptWaveform(chunk):
                result = json.loads(rec.Result())
                pending_final = 0
            else:
                # Partials carry no confidences, but waiting for a pause can take long in a
                # noisy room: once the name shows up, let it finish (0.3 s) and judge that.
                if not pending_final:
                    if "willy" in json.loads(rec.PartialResult()).get("partial", ""):
                        pending_final = 3
                    continue
                pending_final -= 1
                if pending_final:
                    continue
                result = json.loads(rec.FinalResult())
            heard = [(w.get("word"), round(w.get("conf", 0), 2)) for w in result.get("result") or []]
            if any(word in ("willy", "wiley", "hey", "ok", "okay") for word, _ in heard):
                print(time.strftime("%H:%M:%S") + f" [Voice] heard {heard}", flush=True)
            where = wake_position(result, self.profile)
            spoken = b"".join(utterance)
            utterance.clear()
            if where and self.profile and speech_level(spoken) < self.profile.get("min_level", 0):
                print(time.strftime("%H:%M:%S") + f" [Voice] too quiet to be you "
                      f"({speech_level(spoken):.0f} < {self.profile['min_level']:.0f})", flush=True)
                where = None
            if where:
                self._converse(spoken, with_command=(where == "with_command"))
                rec = self._recognizer(model)

    def _train(self, model, count: int) -> Dict[str, Any]:
        found, missed = [], 0
        for i in range(count):
            if self._stop.is_set():
                break
            self._set("training", f"Say \u201cHey Willy\u201d ({i + 1} of {count})")
            self._chime(True)
            self._drain()
            pcm = self._record([], 6.0)
            if pcm is None:
                missed += 1
                continue
            rec = self._recognizer(model)
            words = []
            for j in range(0, len(pcm), BLOCK * 2):
                if rec.AcceptWaveform(pcm[j:j + BLOCK * 2]):
                    words += json.loads(rec.Result()).get("result") or []
            words += json.loads(rec.FinalResult()).get("result") or []
            heard = [(w.get("word"), round(w.get("conf", 0), 2)) for w in words]
            print(time.strftime("%H:%M:%S") + f" [Voice] training {i + 1}: {heard}", flush=True)
            for k in range(len(words) - 1):
                if f"{words[k].get('word')} {words[k + 1].get('word')}" in WAKE_PHRASES:
                    found.append({"hey_conf": words[k].get("conf", 0), "name_conf": words[k + 1].get("conf", 0),
                                  "level": speech_level(pcm)})
                    break
            else:
                missed += 1
        if len(found) < 3:
            return {"success": False, "found": len(found), "count": count,
                    "error": f"I recognised \u201cHey Willy\u201d only {len(found)} of {count} times. Try again a "
                             "little closer to the microphone, in a quieter moment."}
        self.profile = learn_profile(found)
        return {"success": True, "profile": self.profile, "found": len(found), "count": count,
                "message": f"Trained on your voice ({len(found)} of {count} clear). Say \u201cHey Willy\u201d any time."}

    def _record(self, preroll: list, start_timeout: float, min_level: float = 0.0) -> Optional[bytes]:
        """Records until a pause; None when nobody spoke. `min_level` raises the bar for what
        counts as speech (follow-ups: about as loud as the user's own trained voice)."""
        threshold = max(450.0, self._noise * 2.6, min_level)
        frames = list(preroll)
        started = False
        silent_for = 0.0
        began = time.monotonic()
        while not self._stop.is_set():
            try:
                chunk = self._audio.get(timeout=0.5)
            except queue.Empty:
                chunk = b""
            if chunk:
                frames.append(chunk)
                loud = _rms(chunk) > threshold
                if loud:
                    started, silent_for = True, 0.0
                elif started:
                    silent_for += BLOCK / RATE
            elapsed = time.monotonic() - began
            if not started and elapsed > start_timeout:
                return None
            if started and (silent_for >= END_SILENCE_SEC or elapsed > MAX_COMMAND_SEC):
                break
        return b"".join(frames) if started else None

    def _chime(self, rising: bool = True) -> None:
        try:
            import winsound

            for freq in ((880, 1320) if rising else (1320, 880)):
                winsound.Beep(freq, 70)
        except Exception:
            pass

    def _converse(self, spoken: Optional[bytes], with_command: bool, manual: bool = False) -> None:
        """`spoken`: the audio of the phrase that held the wake word (None when the tray
        started listening). With `with_command` the command was said in the same breath;
        otherwise it is recorded after the chime and sent together with the wake phrase, so
        the hub can confirm the name was really said."""
        self._chime(True)
        self._drain()
        pcm: Optional[bytes] = spoken
        if not with_command:
            self._set("listening")
            command = self._record([], START_TIMEOUT_SEC)
            if command is None:
                self._chime(False)
                self._set("muted" if self.muted else "idle")
                return
            pcm = (spoken or b"") + command
        check_name = not manual
        while pcm and not self._stop.is_set():
            self._set("thinking")
            try:
                reply = post_voice(pcm, wake=check_name)
            except Exception as e:  # noqa: BLE001
                reply = {"success": False, "error": f"Couldn't reach the hub: {e}"}
            if reply.get("ignored"):
                print(time.strftime("%H:%M:%S") + f" [Voice] hub didn't hear the name in: {reply.get('transcript')!r}",
                      flush=True)
                break  # the hub didn't hear "Willy": a false wake-up, stay quiet
            try:
                self.on_reply(reply)
            except Exception:
                pass
            if reply.get("reply") or reply.get("audio_base64"):
                self._speak(reply)
            elif reply.get("error"):
                self._chime(False)
                break
            # A conversation: keep listening without the wake word until the user goes quiet
            # or says they're done. Follow-ups must be about as loud as the user's own voice
            # (learned in training), so talk across the room doesn't continue it.
            heard = str(reply.get("transcript") or reply.get("query") or "")
            if END_RE.search(heard):
                break
            self._set("listening", "follow-up")
            level = (self.profile or {}).get("min_level", 0.0)
            pcm = self._record([], FOLLOW_UP_SEC, min_level=level)
            check_name = False
        self._set("muted" if self.muted else "idle")

    def _speak(self, reply: Dict[str, Any]) -> None:
        from pc_client.executor import speak_text

        self._set("speaking", str(reply.get("reply") or "")[:200])
        self.paused = True
        try:
            speak_text(str(reply.get("reply") or ""), reply.get("audio_base64"), blocking=True)
        finally:
            time.sleep(0.25)  # the speakers' tail
            self._drain()
            self.paused = False
