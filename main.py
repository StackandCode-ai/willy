"""
Willy: Autonomous Voice-Driven Windows System Controller Daemon.
"""

import sys
import os
import time
import argparse
import signal
from pathlib import Path

# Ensure working directory and sys.path are always rooted at project directory
PROJECT_ROOT = Path(__file__).resolve().parent
if Path.cwd() != PROJECT_ROOT:
    try:
        os.chdir(PROJECT_ROOT)
    except Exception:
        pass
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Ensure Windows terminal handles UTF-8 safely
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Suppress pygame and pkg_resources deprecation banners
os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"
import warnings
warnings.filterwarnings("ignore", category=UserWarning)

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich import print as rprint
    console = Console(highlight=False)
except ImportError:
    console = None
    def rprint(*args, **kwargs):
        print(*args)

from willy.config import settings, get_wake_word_list
from willy.audio.listener import AudioListener
from willy.audio.stt import SpeechToText
from willy.audio.tts import TextToSpeech
from willy.audio.wake_word import WakeWordDetector
from willy.agent.orchestrator import AgentOrchestrator


def print_banner():
    """Displays Willy's startup banner and active configuration."""
    wake_words = ", ".join(f"'{w}'" for w in get_wake_word_list())
    banner_text = (
        f"[bold cyan]WILLY[/bold cyan] - Autonomous Voice-Driven Windows Controller\n"
        f"[dim]Language:[/dim] English (US)  |  [dim]LLM:[/dim] {settings.LLM_PROVIDER.upper()} ({settings.GROQ_MODEL if settings.LLM_PROVIDER=='groq' else settings.OPENAI_MODEL})\n"
        f"[dim]STT:[/dim] {settings.STT_PROVIDER.upper()} Whisper  |  [dim]TTS:[/dim] {settings.TTS_PROVIDER} ({settings.TTS_VOICE})\n"
        f"[dim]Wake Triggers:[/dim] [yellow]{wake_words}[/yellow]\n"
        f"[dim]Active Session Timeout:[/dim] {settings.CONVERSATION_TIMEOUT}s"
    )
    if console:
        console.print(Panel(banner_text, title="[bold green]WILLY SYSTEM READY[/bold green]", border_style="cyan"))
    else:
        print("=" * 60)
        print("WILLY: Autonomous Voice-Driven Windows Controller")
        print(f"Wake Triggers: {wake_words}")
        print("=" * 60)

    # API Key check
    active_key = settings.GROQ_API_KEY if settings.LLM_PROVIDER == "groq" else settings.OPENAI_API_KEY
    if not active_key or active_key.startswith("your_") or "here" in active_key:
        rprint("[bold yellow][!] ATTENTION: API Key not configured![/bold yellow]")
        rprint(f"    Please paste your [bold]{settings.LLM_PROVIDER.upper()}_API_KEY[/bold] into the [cyan].env[/cyan] file.")
        if settings.LLM_PROVIDER == "groq":
            rprint("    Get a free Groq key in 30 seconds at: [underline blue]https://console.groq.com/keys[/underline blue]\n")




def run_text_mode(orchestrator: AgentOrchestrator, tts: TextToSpeech):
    """Interactive text prompt mode for testing tools and LLM without microphone."""
    print("\n[!] Text Mode Activated. Type your commands or queries. Type 'exit' to quit.\n")
    while True:
        try:
            user_input = input("\nYou > ").strip()
            if not user_input:
                continue
            if user_input.lower() in ("exit", "quit", "q"):
                print("Exiting Willy.")
                break

            rprint("[dim]Thinking & executing...[/dim]")
            reply = orchestrator.process_query(user_input)
            rprint(f"[bold green]Willy:[/bold green] {reply}")
            
            # Vocalize reply
            tts.speak(reply, block=True)

        except (KeyboardInterrupt, EOFError):
            print("\nExiting Willy.")
            break


def run_voice_daemon(
    listener: AudioListener,
    stt: SpeechToText,
    tts: TextToSpeech,
    wake_detector: WakeWordDetector,
    orchestrator: AgentOrchestrator,
    push_to_talk: bool = False,
):
    """Main voice loop."""
    print_banner()

    if not push_to_talk:
        # Dynamic ambient noise calibration
        try:
            listener.calibrate_ambient_noise(1.0)
        except Exception as e:
            rprint(f"[yellow]Notice:[/yellow] Ambient calibration skipped ({e})")

    rprint("\n[bold green][*] Willy is listening...[/bold green] (Say '[yellow]Hey Willy[/yellow]' or ask anything)")
    if push_to_talk:
        rprint("[cyan]Push-To-Talk Mode: Press ENTER to speak.[/cyan]")

    # Initial friendly chime / greeting (blocking to prevent self-waking on boot)
    tts.speak("Willy is online and ready.", block=True, listener=listener)
    listener.pre_buffer.clear()

    while True:
        try:
            if push_to_talk:
                input("\n[Press ENTER and speak...] ")
                rprint("[red][REC] Recording your voice...[/red]")
                wav_bytes = listener.listen_and_record_utterance()
            else:
                if getattr(wake_detector, "is_paused", False):
                    session_status = "[dim yellow](Paused - Standby)[/dim yellow] "
                elif wake_detector.is_in_active_session():
                    session_status = "[bold yellow](Active Session)[/bold yellow] "
                else:
                    session_status = ""
                rprint(f"\r{session_status}[dim]Listening...[/dim] ", end="", flush=True)

                def on_speech():
                    rprint("\n[red][REC] Speech detected, recording...[/red]")

                def on_barge_in():
                    if getattr(settings, "ENABLE_ACOUSTIC_BARGE_IN", False):
                        tts.stop()
                        rprint("\n[bold red][INTERRUPT] User voice detected! Stopping speech...[/bold red]")

                wav_bytes = listener.listen_and_record_utterance(
                    on_speech_start=on_speech,
                    on_barge_in=on_barge_in
                )

            if not wav_bytes:
                continue

            # Transcribe audio
            rprint("[cyan][*] Transcribing...[/cyan]")
            transcript = stt.transcribe(wav_bytes)

            if not transcript:
                continue

            # Reject acoustic echo of Willy's own recent speech
            if wake_detector.is_self_speech(transcript):
                rprint(f'[dim yellow][Echo Filter] Ignored self-speech transcript: "{transcript}"[/dim yellow]')
                continue

            rprint(f'[bold white]Heard:[/bold white] "{transcript}"')

            # Check wake word and extract command
            if push_to_talk:
                is_triggered = True
                command = transcript
            else:
                is_triggered, command = wake_detector.process_transcript(transcript)

            if not is_triggered:
                # Neither wake word nor active session
                continue

            start_time = time.time()
            last_tool = {"name": None, "args": None, "result": None}

            # If user commanded pause/sleep
            if command == "__PAUSE__":
                reply = "Paused. Call my name when you need me."
                rprint(f"[bold yellow]Willy:[/bold yellow] {reply}")
                tts.speak(reply, block=True, listener=listener)
                time.sleep(0.4)
                listener.pre_buffer.clear()
                continue

            # If user woke Willy from pause with just the wake word
            if command == "__RESUME__":
                reply = "I'm back! How can I help you?"
                rprint(f"[bold green]Willy:[/bold green] {reply}")
                tts.speak(reply, block=True, listener=listener)
                listener.pre_buffer.clear()
                wake_detector.refresh_session()
                continue

            # If user just said "Willy" or "Hey Willy" with no command
            if not command or len(command.strip()) == 0:
                greeting = "Hey! I'm listening."
                rprint(f"[bold green]Willy:[/bold green] {greeting}")
                tts.speak(greeting, block=True, listener=listener)
                listener.pre_buffer.clear()
                wake_detector.refresh_session()
                
                # Log greeting interaction
                from willy.logger import logger
                logger.log_interaction(
                    heard=transcript,
                    willy_spoke=greeting,
                    duration_sec=time.time() - start_time
                )
                continue

            # Execute command with LLM & tools
            rprint(f'[bold yellow]Processing query:[/bold yellow] "{command}"')

            def on_tool_start(name, args):
                last_tool["name"] = name
                last_tool["args"] = args
                rprint(f"[bold cyan][RUN] Tool Executing:[/bold cyan] [green]{name}[/green] with {args}")

            def on_tool_done(name, result):
                last_tool["result"] = result
                success = result.get("success", False)
                status_color = "green" if success else "red"
                rprint(f"[{status_color}][OK] Tool Result ({name}):[/{status_color}] {result}")

            reply = orchestrator.process_query(
                command,
                on_tool_start=on_tool_start,
                on_tool_done=on_tool_done,
            )

            rprint(f"[bold green]Willy Speaks:[/bold green] {reply}")
            
            # Show visual toast on Windows desktop
            try:
                from willy.ui.toast import show_desktop_toast
                show_desktop_toast(title="Willy Assistant", message=reply, query=command)
            except Exception:
                pass

            tts.speak(reply, block=True, listener=listener)

            # Log complete interaction (heard, action/tool, spoke)
            from willy.logger import logger
            logger.log_interaction(
                heard=transcript,
                willy_spoke=reply,
                tool_name=last_tool["name"],
                tool_args=last_tool["args"],
                tool_result=last_tool["result"],
                duration_sec=time.time() - start_time
            )

            # Clear pre-buffer to prevent hearing own voice echo
            time.sleep(0.3)
            listener.pre_buffer.clear()

            # Refresh conversation timeout for follow-ups
            wake_detector.refresh_session()

        except KeyboardInterrupt:
            rprint("\n[yellow]Shutting down Willy daemon... Goodbye![/yellow]")
            break
        except Exception as e:
            rprint(f"\n[red]Unexpected daemon error:[/red] {e}")
            time.sleep(1)


def main():
    parser = argparse.ArgumentParser(description="Willy: Autonomous Voice-Driven Windows System Controller")
    parser.add_argument(
        "--mode",
        choices=["ui", "voice", "push-to-talk", "text"],
        default="ui",
        help="Operation mode: 'ui' (floating voice widget with visualizer), 'voice' (console daemon), 'push-to-talk', or 'text'",
    )
    parser.add_argument(
        "--enable-autostart",
        nargs="?",
        const="ui",
        choices=["ui", "voice"],
        help="Configure Willy to auto-start with Windows on boot (default mode: ui)",
    )
    parser.add_argument(
        "--disable-autostart",
        action="store_true",
        help="Disable Willy auto-start with Windows",
    )
    parser.add_argument(
        "--autostart-status",
        action="store_true",
        help="Check whether Willy is configured to auto-start with Windows",
    )
    args = parser.parse_args()

    # Handle autostart CLI actions immediately if specified
    if args.enable_autostart:
        from willy.tools.autostart_tools import enable_autostart
        res = enable_autostart(mode=args.enable_autostart)
        print(res.get("message", str(res)))
        return

    if args.disable_autostart:
        from willy.tools.autostart_tools import disable_autostart
        res = disable_autostart()
        print(res.get("message", str(res)))
        return

    if args.autostart_status:
        from willy.tools.autostart_tools import get_autostart_status
        res = get_autostart_status()
        print(res.get("message", str(res)))
        return

    # Enforce single background instance on Windows to prevent duplicate audio conflicts
    _instance_mutex = None
    if sys.platform == "win32" and args.mode in ("ui", "voice", "push-to-talk"):
        import ctypes
        kernel32 = ctypes.windll.kernel32
        mutex_name = "Global\\WillyVoiceAssistantDaemonMutex"
        _instance_mutex = kernel32.CreateMutexW(None, False, mutex_name)
        if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            print("[!] Willy is already running in the background. Exiting duplicate instance.")
            return

    # Graceful exit handler
    def handle_sigint(sig, frame):
        print("\nShutting down gracefully...")
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_sigint)

    # Automatically start Cloud Relay Bridge in background if enabled in settings
    if getattr(settings, "ENABLE_CLOUD_RELAY", False):
        try:
            from willy.server.agent_bridge import WindowsAgentBridge
            bridge = WindowsAgentBridge()
            bridge.start_background()
            print("[+] Cloud Relay Bridge for Siri & Mobile active in background.")
        except Exception as e:
            print(f"[-] Cloud Relay Bridge notice: {e}")

    if args.mode == "ui":
        from willy.ui.voice_widget import launch_ui
        launch_ui()
        return

    # Initialize modules
    orchestrator = AgentOrchestrator()
    tts = TextToSpeech()

    if args.mode == "text":
        run_text_mode(orchestrator, tts)
    else:
        # Retry audio initialization on boot in case Windows audio service is starting
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                listener = AudioListener()
                stt = SpeechToText()
                wake_detector = WakeWordDetector()
                push_to_talk = (args.mode == "push-to-talk")
                run_voice_daemon(listener, stt, tts, wake_detector, orchestrator, push_to_talk=push_to_talk)
                break
            except Exception as e:
                if attempt < max_retries:
                    rprint(f"[yellow][!] Audio initialization attempt {attempt}/{max_retries} failed ({e}). Retrying in 2s...[/yellow]")
                    time.sleep(2)
                else:
                    rprint(f"[bold red]Audio Initialization Failed:[/bold red] {e}")
                    rprint("[yellow]Tip: If you do not have a microphone connected, you can run in text mode: python main.py --mode text[/yellow]")
                    sys.exit(1)


if __name__ == "__main__":
    main()
