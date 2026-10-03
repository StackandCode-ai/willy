"""
Willy Desktop Voice UI: Floating assistant widget with live audio visualizer.
"""

import sys
import os
import time
import queue
import threading
import tkinter as tk
from tkinter import ttk
import numpy as np

# Suppress Pygame warnings
os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"

from willy.config import settings, get_wake_word_list
from willy.audio.listener import AudioListener
from willy.audio.stt import SpeechToText
from willy.audio.tts import TextToSpeech
from willy.audio.wake_word import WakeWordDetector
from willy.agent.orchestrator import AgentOrchestrator


class WillyVoiceApp:
    """
    A sleek, modern floating desktop UI for Willy with a real-time
    microphone volume visualizer and live conversational transcript.
    """

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Willy - Windows Voice Assistant")
        self.root.geometry("420x560+80+80")
        self.root.configure(bg="#0B0F19")
        self.root.resizable(False, False)
        
        # Keep window on top so user can see it while commanding Windows
        self.root.attributes("-topmost", True)

        # State
        self.volume_queue = queue.Queue(maxsize=10)
        self.current_volume = 0.0
        self.smoothed_volume = 0.0
        self.status_text = "Initializing..."
        self.is_running = True

        # Initialize Willy Core
        self.listener = AudioListener()
        self.stt = SpeechToText()
        self.tts = TextToSpeech()
        self.wake_detector = WakeWordDetector()
        self.orchestrator = AgentOrchestrator()

        self._build_ui()
        self._start_audio_thread()
        self._animate_visualizer()

    def _build_ui(self):
        # 1. Header Frame
        header = tk.Frame(self.root, bg="#111827", height=50)
        header.pack(fill=tk.X)

        title_lbl = tk.Label(
            header,
            text="WILLY",
            font=("Segoe UI", 16, "bold"),
            fg="#00F2FE",
            bg="#111827"
        )
        title_lbl.pack(side=tk.LEFT, padx=(16, 6), pady=10)

        subtitle_lbl = tk.Label(
            header,
            text="Voice Assistant",
            font=("Segoe UI", 10),
            fg="#94A3B8",
            bg="#111827"
        )
        subtitle_lbl.pack(side=tk.LEFT, pady=12)

        # Pin / On-Top Toggle Indicator
        self.top_btn = tk.Label(
            header,
            text="📌 On Top",
            font=("Segoe UI", 8),
            fg="#38BDF8",
            bg="#1E293B",
            padx=6,
            pady=2,
            cursor="hand2"
        )
        self.top_btn.pack(side=tk.RIGHT, padx=12, pady=12)

        # 2. Status Badge Bar
        status_bar = tk.Frame(self.root, bg="#0B0F19")
        status_bar.pack(fill=tk.X, padx=16, pady=(12, 6))

        self.status_lbl = tk.Label(
            status_bar,
            text="● Ready (Say 'Hey Willy')",
            font=("Segoe UI", 10, "bold"),
            fg="#10B981",
            bg="#0B0F19"
        )
        self.status_lbl.pack(side=tk.LEFT)

        self.vol_lbl = tk.Label(
            status_bar,
            text="Mic: 0%",
            font=("Segoe UI", 9),
            fg="#64748B",
            bg="#0B0F19"
        )
        self.vol_lbl.pack(side=tk.RIGHT)

        # 3. Audio Waveform Visualizer Canvas
        vis_frame = tk.Frame(self.root, bg="#111827", highlightthickness=1, highlightbackground="#1F2937")
        vis_frame.pack(fill=tk.X, padx=16, pady=6)

        self.canvas_width = 384
        self.canvas_height = 80
        self.canvas = tk.Canvas(
            vis_frame,
            width=self.canvas_width,
            height=self.canvas_height,
            bg="#0D1322",
            highlightthickness=0
        )
        self.canvas.pack(padx=2, pady=2)

        # 4. Conversation History / Bubbles
        dialogue_frame = tk.Frame(self.root, bg="#0B0F19")
        dialogue_frame.pack(fill=tk.BOTH, expand=True, padx=16, pady=8)

        # User Heard Card
        u_header = tk.Label(dialogue_frame, text="YOU SAID", font=("Segoe UI", 8, "bold"), fg="#38BDF8", bg="#0B0F19")
        u_header.pack(anchor="w")

        self.user_box = tk.Text(
            dialogue_frame,
            height=2,
            wrap=tk.WORD,
            bg="#1E293B",
            fg="#F8FAFC",
            font=("Segoe UI", 10),
            relief=tk.FLAT,
            padx=10,
            pady=6,
            highlightthickness=1,
            highlightbackground="#334155"
        )
        self.user_box.insert(tk.END, "Say 'Hey Willy' or tap 'Speak Now' below...")
        self.user_box.configure(state=tk.DISABLED)
        self.user_box.pack(fill=tk.X, pady=(2, 10))

        # Willy Response Card
        w_header = tk.Label(dialogue_frame, text="WILLY REPLIES", font=("Segoe UI", 8, "bold"), fg="#A855F7", bg="#0B0F19")
        w_header.pack(anchor="w")

        self.willy_box = tk.Text(
            dialogue_frame,
            height=6,
            wrap=tk.WORD,
            bg="#171C2E",
            fg="#E2E8F0",
            font=("Segoe UI", 10),
            relief=tk.FLAT,
            padx=10,
            pady=8,
            highlightthickness=1,
            highlightbackground="#3B4261"
        )
        self.willy_box.insert(tk.END, "I'm ready to control Windows or chat with you!")
        self.willy_box.configure(state=tk.DISABLED)
        self.willy_box.pack(fill=tk.BOTH, expand=True, pady=(2, 6))

        # 5. Bottom Action Controls
        ctrl_frame = tk.Frame(self.root, bg="#0B0F19")
        ctrl_frame.pack(fill=tk.X, padx=16, pady=(4, 16))

        self.speak_btn = tk.Button(
            ctrl_frame,
            text="🎙️ Speak Now",
            font=("Segoe UI", 10, "bold"),
            bg="#0284C7",
            fg="#FFFFFF",
            activebackground="#0369A1",
            activeforeground="#FFFFFF",
            relief=tk.FLAT,
            padx=10,
            pady=8,
            cursor="hand2",
            command=self._on_manual_speak
        )
        self.speak_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))

        self.pause_btn = tk.Button(
            ctrl_frame,
            text="⏸️ Pause",
            font=("Segoe UI", 10, "bold"),
            bg="#DC2626",
            fg="#FFFFFF",
            activebackground="#B91C1C",
            activeforeground="#FFFFFF",
            relief=tk.FLAT,
            padx=12,
            pady=8,
            cursor="hand2",
            command=self._toggle_pause
        )
        self.pause_btn.pack(side=tk.LEFT, padx=4)

        self.test_btn = tk.Button(
            ctrl_frame,
            text="🔊 Test",
            font=("Segoe UI", 10),
            bg="#334155",
            fg="#F1F5F9",
            activebackground="#475569",
            activeforeground="#FFFFFF",
            relief=tk.FLAT,
            padx=10,
            pady=8,
            cursor="hand2",
            command=self._test_voice
        )
        self.test_btn.pack(side=tk.RIGHT, padx=(4, 0))

    def _test_voice(self):
        """Tests the speaker output immediately."""
        self._update_status("🔊 Testing PC Speaker...", "#A855F7")
        threading.Thread(
            target=lambda: (
                self.tts.speak("Hello! Your speakers are working properly with Willy.", block=True),
                self._update_status("● Ready (Say 'Hey Willy')", "#10B981")
            ),
            daemon=True
        ).start()

    def _toggle_pause(self):
        """Manually toggles Willy between Paused (standby security) and Resumed."""
        if getattr(self.wake_detector, "is_paused", False):
            # Currently paused -> Resume
            self.wake_detector.resume()
            self.listener.abort()  # Unblock listener immediately to switch modes
            self._update_pause_ui(is_paused=False)
            self._update_status("● Active Session (Listening...)", "#10B981")
            self._update_willy_text("Resumed! I'm listening for your command.")
        else:
            # Currently active -> Pause
            self.tts.stop()  # Instantly silence any speech output
            self.wake_detector.pause()
            self.listener.abort()  # Stop active recording
            self.listener.pre_buffer.clear()
            self._update_pause_ui(is_paused=True)
            self._update_status("⏸️ Paused (Mic Standby)", "#94A3B8")
            self._update_willy_text("Paused for privacy/security. Click '▶️ Resume' to wake me.")

    def _update_pause_ui(self, is_paused: bool):
        """Thread-safe update for the manual pause/resume button appearance."""
        def _apply():
            if is_paused:
                self.pause_btn.configure(
                    text="▶️ Resume",
                    bg="#10B981",
                    activebackground="#059669",
                    fg="#FFFFFF",
                )
            else:
                self.pause_btn.configure(
                    text="⏸️ Pause",
                    bg="#DC2626",
                    activebackground="#B91C1C",
                    fg="#FFFFFF",
                )
        self.root.after(0, _apply)

    def _on_manual_speak(self):
        """Triggers direct listening without waiting for wake word."""
        self.tts.stop()
        self.wake_detector.resume()
        self.listener.abort()
        self._update_pause_ui(is_paused=False)
        self._update_status("🎙️ Listening for your command...", "#EF4444")

    def _update_status(self, text: str, color: str = "#10B981"):
        """Thread-safe status update."""
        def _apply():
            self.status_lbl.configure(text=text, fg=color)
        self.root.after(0, _apply)

    def _update_user_text(self, text: str):
        def _apply():
            self.user_box.configure(state=tk.NORMAL)
            self.user_box.delete("1.0", tk.END)
            self.user_box.insert(tk.END, text)
            self.user_box.configure(state=tk.DISABLED)
        self.root.after(0, _apply)

    def _update_willy_text(self, text: str):
        def _apply():
            self.willy_box.configure(state=tk.NORMAL)
            self.willy_box.delete("1.0", tk.END)
            self.willy_box.insert(tk.END, text)
            self.willy_box.configure(state=tk.DISABLED)
        self.root.after(0, _apply)

    def _animate_visualizer(self):
        """Draws dynamic audio waveform bars at 30 FPS based on microphone level."""
        if not self.is_running:
            return

        if getattr(self.wake_detector, "is_paused", False):
            self.vol_lbl.configure(text="Mic: Paused 🔒", fg="#94A3B8")
            self.canvas.delete("all")
            center_y = self.canvas_height / 2
            self.canvas.create_line(
                20, center_y, self.canvas_width - 20, center_y,
                fill="#334155", width=2, dash=(4, 4)
            )
            self.canvas.create_text(
                self.canvas_width / 2, center_y - 12,
                text="STANDBY (PAUSED)",
                fill="#64748B",
                font=("Segoe UI", 9, "bold")
            )
            self.root.after(100, self._animate_visualizer)
            return

        if self.tts.is_speaking:
            self.vol_lbl.configure(text="Willy Speaking 🔊", fg="#A855F7")
            self.canvas.delete("all")
            num_bars = 28
            bar_width = 8
            spacing = 5
            center_y = self.canvas_height / 2
            t_now = time.time()
            for i in range(num_bars):
                dist = abs(i - (num_bars / 2)) / (num_bars / 2)
                h = max(6, int(28 * np.sin(t_now * 8 + i * 0.45) * (1.0 - dist * 0.4) + 18))
                h = min(self.canvas_height - 6, h)
                x = 10 + i * (bar_width + spacing)
                y1 = center_y - (h / 2)
                y2 = center_y + (h / 2)
                ratio = i / num_bars
                r = int(168 + ratio * 60)
                g = int(85 - ratio * 30)
                b = int(247 - ratio * 20)
                color = f"#{min(255, max(0, r)):02x}{min(255, max(0, g)):02x}{min(255, max(0, b)):02x}"
                self.canvas.create_rectangle(x, y1, x + bar_width, y2, fill=color, outline="", width=0)
            self.root.after(33, self._animate_visualizer)
            return

        # Fetch latest volume from queue
        while not self.volume_queue.empty():
            try:
                self.current_volume = self.volume_queue.get_nowait()
            except queue.Empty:
                break

        # Smooth easing
        self.smoothed_volume += (self.current_volume - self.smoothed_volume) * 0.35
        vol_pct = min(100, int(self.smoothed_volume * 2500))
        self.vol_lbl.configure(text=f"Mic: {vol_pct}%", fg="#64748B")

        self.canvas.delete("all")
        num_bars = 28
        bar_width = 8
        spacing = 5
        center_y = self.canvas_height / 2

        # Draw animated bars with gradient coloring
        for i in range(num_bars):
            # Mirror from center
            dist_from_center = abs(i - (num_bars / 2)) / (num_bars / 2)
            wave_factor = np.cos(dist_from_center * np.pi * 0.5)
            # Add slight idle pulsation
            idle_noise = 0.08 * np.sin(time.time() * 5 + i * 0.4)
            height = max(4, int((self.smoothed_volume * 1200 * wave_factor) + (idle_noise * 8) + 4))
            height = min(self.canvas_height - 6, height)

            x = 10 + i * (bar_width + spacing)
            y1 = center_y - (height / 2)
            y2 = center_y + (height / 2)

            # Gradient color from Cyan to Purple
            ratio = i / num_bars
            r = int(0 + ratio * 168)
            g = int(242 - ratio * 157)
            b = int(254 - ratio * 10)
            color = f"#{r:02x}{g:02x}{b:02x}"

            self.canvas.create_rectangle(
                x, y1, x + bar_width, y2,
                fill=color,
                outline="",
                width=0
            )

        self.root.after(33, self._animate_visualizer)

    def _start_audio_thread(self):
        """Runs the continuous audio listening loop in a background thread."""
        t = threading.Thread(target=self._audio_loop, daemon=True)
        t.start()

    def _audio_loop(self):
        import sounddevice as sd
        chunk_size = self.listener.chunk_size
        sample_rate = self.listener.sample_rate

        self._update_status("● Calibrating room noise...", "#F59E0B")
        try:
            self.listener.calibrate_ambient_noise(1.0)
        except Exception:
            pass

        self._update_status("● Ready (Say 'Hey Willy')", "#10B981")

        def on_volume(rms: float):
            if not self.is_running:
                return
            try:
                self.volume_queue.put_nowait(rms)
            except queue.Full:
                pass

        last_pause_state = None

        while self.is_running:
            try:
                is_currently_paused = getattr(self.wake_detector, "is_paused", False)
                if is_currently_paused != last_pause_state:
                    last_pause_state = is_currently_paused
                    self._update_pause_ui(is_currently_paused)

                # In paused, active session, speaking, or ready
                if is_currently_paused:
                    self._update_status("⏸️ Paused (Click 'Resume' or say 'Hey Willy')", "#94A3B8")
                elif self.tts.is_speaking:
                    self._update_status("🔊 Willy Speaking...", "#A855F7")
                elif self.wake_detector.is_in_active_session():
                    self._update_status("● Active Session (Listening...)", "#F59E0B")
                else:
                    self._update_status("● Ready (Say 'Hey Willy')", "#10B981")

                def on_speech():
                    self._update_status("🎙️ Hearing speech...", "#EF4444")

                def on_barge_in():
                    self.tts.stop()
                    self._update_status("🎙️ Interrupted! Listening...", "#EF4444")

                wav_bytes = self.listener.listen_and_record_utterance(
                    on_speech_start=on_speech,
                    on_volume_chunk=on_volume,
                    on_barge_in=on_barge_in
                )
                if not wav_bytes:
                    continue

                # Transcribe
                self._update_status("⚡ Transcribing...", "#38BDF8")
                transcript = self.stt.transcribe(wav_bytes)
                if not transcript:
                    continue

                # Immediately filter out acoustic echo of Willy's own speech
                if self.wake_detector.is_self_speech(transcript):
                    print(f"[Echo Filter UI] Ignored self-speech transcript: '{transcript}'")
                    continue

                self._update_user_text(f'"{transcript}"')

                is_triggered, command = self.wake_detector.process_transcript(transcript)
                if not is_triggered:
                    continue

                start_time = time.time()
                last_tool = {"name": None, "args": None, "result": None}

                # If user commanded pause/sleep
                if command == "__PAUSE__":
                    reply = "Paused. Call my name when you need me."
                    self.tts.stop()
                    self._update_status("⏸️ Paused (Click 'Resume' or say 'Hey Willy')", "#94A3B8")
                    self._update_pause_ui(is_paused=True)
                    self._update_willy_text(reply)
                    try:
                        from willy.ui.toast import show_desktop_toast
                        show_desktop_toast(title="Willy Assistant", message=reply, query=transcript)
                    except Exception:
                        pass
                    self.tts.speak(reply, block=True, listener=self.listener)
                    self.listener.pre_buffer.clear()
                    continue

                # If user woke Willy from pause with just the wake word
                if command == "__RESUME__":
                    reply = "I'm back! How can I help you?"
                    self._update_status("🔊 Willy Speaking...", "#A855F7")
                    self._update_pause_ui(is_paused=False)
                    self._update_willy_text(reply)
                    try:
                        from willy.ui.toast import show_desktop_toast
                        show_desktop_toast(title="Willy Assistant", message=reply, query=transcript)
                    except Exception:
                        pass
                    self.tts.speak(reply, block=True, listener=self.listener)
                    self.listener.pre_buffer.clear()
                    self.wake_detector.refresh_session()
                    continue

                # If just wake word with no follow-up
                if not command or len(command.strip()) == 0:
                    reply = "Hey! I'm listening."
                    self._update_status("🔊 Willy Speaking...", "#A855F7")
                    self._update_willy_text(reply)
                    try:
                        from willy.ui.toast import show_desktop_toast
                        show_desktop_toast(title="Willy Assistant", message=reply, query=transcript)
                    except Exception:
                        pass
                    self.tts.speak(reply, block=True, listener=self.listener)
                    self.listener.pre_buffer.clear()
                    self.wake_detector.refresh_session()

                    from willy.logger import logger
                    logger.log_interaction(
                        heard=transcript,
                        willy_spoke=reply,
                        duration_sec=time.time() - start_time
                    )
                    continue

                # Process command
                self._update_status("🧠 Thinking & Executing...", "#8B5CF6")

                def on_tool_start(name, args):
                    last_tool["name"] = name
                    last_tool["args"] = args
                    self._update_status(f"⚡ Running tool: {name}", "#06B6D4")

                def on_tool_done(name, result):
                    last_tool["result"] = result

                reply = self.orchestrator.process_query(
                    command,
                    on_tool_start=on_tool_start,
                    on_tool_done=on_tool_done
                )

                self._update_status("🔊 Willy Speaking...", "#A855F7")
                self._update_willy_text(reply)
                try:
                    from willy.ui.toast import show_desktop_toast
                    show_desktop_toast(title="Willy Assistant", message=reply, query=command)
                except Exception:
                    pass
                self.tts.speak(reply, block=True, listener=self.listener)
                self.listener.pre_buffer.clear()
                self.wake_detector.refresh_session()

                from willy.logger import logger
                logger.log_interaction(
                    heard=transcript,
                    willy_spoke=reply,
                    tool_name=last_tool["name"],
                    tool_args=last_tool["args"],
                    tool_result=last_tool["result"],
                    duration_sec=time.time() - start_time
                )

                self.listener.pre_buffer.clear()

            except Exception as e:
                err_msg = str(e)
                print(f"[-] UI Audio loop error: {err_msg}")
                if "-9988" in err_msg or "stream pointer" in err_msg.lower():
                    try:
                        import sounddevice as sd
                        sd._terminate()
                        sd._initialize()
                    except Exception:
                        pass
                time.sleep(0.5)

    def run(self):
        def on_close():
            self.is_running = False
            self.root.destroy()
            sys.exit(0)

        self.root.protocol("WM_DELETE_WINDOW", on_close)
        self.root.mainloop()


def launch_ui():
    app = WillyVoiceApp()
    app.run()


if __name__ == "__main__":
    launch_ui()
