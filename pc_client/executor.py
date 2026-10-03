"""
Willy PC Client Task Executor.
Standalone executor running physical Windows system tasks (PowerShell, App Launch, UI, Power, Volume, Screenshot)
sent from the Central Server or Mobile App.
Zero AI models on PC: purely executes structured deterministic commands.

execute_action() is synchronous; the client runs it in a worker thread so slow
actions never delay heartbeats or other commands.
"""

import os
import sys
import time
import base64
import tempfile
import threading
import subprocess
from pathlib import Path
from typing import Callable, Dict, Any, Optional

from pc_client.tools.app_launcher import launch_windows_app
from pc_client.tools.process_tools import close_app
from pc_client.tools.system_tools import (
    execute_powershell,
    open_url,
    volume_control,
    get_system_status,
)
from pc_client.tools.window_tools import (
    minimize_all_windows,
    minimize_window,
    maximize_current_window,
    close_current_window,
    focus_window,
    list_open_windows,
)
from pc_client.tools.power_tools import (
    lock_workstation,
    unlock_or_wake,
    shutdown_system,
    restart_system,
    sleep_system,
    cancel_shutdown,
)
from pc_client.tools.vision_tools import take_screenshot
from pc_client.tools.network_tools import check_network_status
from pc_client.tools.registry import dispatch_pc_command
from pc_client.telemetry import collect_pc_telemetry, collect_static_specs, get_collector
from pc_client.ui.toast import show_desktop_toast

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

# Killing these would crash or log off Windows.
PROTECTED_PROCESSES = {
    "system", "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe", "services.exe",
    "lsass.exe", "svchost.exe", "dwm.exe", "fontdrvhost.exe", "memcompression", "registry",
}

KNOWN_FOLDERS = {
    "home": "~",
    "user": "~",
    "desktop": "~/Desktop",
    "documents": "~/Documents",
    "docs": "~/Documents",
    "downloads": "~/Downloads",
    "download": "~/Downloads",
    "pictures": "~/Pictures",
    "photos": "~/Pictures",
    "screenshots": "~/Pictures/Screenshots",
    "music": "~/Music",
    "videos": "~/Videos",
}

Notifier = Callable[[str, str, Optional[str]], None]


# ------------------------------------------------------------------ clipboard

def set_clipboard_text(text: str) -> bool:
    try:
        import win32clipboard
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
        finally:
            win32clipboard.CloseClipboard()
        return True
    except Exception:
        pass
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Set-Clipboard -Value $input"],
            input=text, text=True, capture_output=True, timeout=5, creationflags=CREATE_NO_WINDOW,
        )
        return True
    except Exception:
        return False


def get_clipboard_text() -> str:
    try:
        import win32clipboard
        win32clipboard.OpenClipboard()
        try:
            if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
                return win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT) or ""
            return ""
        finally:
            win32clipboard.CloseClipboard()
    except Exception:
        pass
    try:
        p = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
            capture_output=True, text=True, timeout=5, creationflags=CREATE_NO_WINDOW,
        )
        return p.stdout.strip()
    except Exception:
        return ""


# ---------------------------------------------------------------------- audio

def _play_mp3_blocking(path: str) -> bool:
    """Plays an MP3 through the Windows MCI API (no extra dependencies)."""
    try:
        import ctypes
        mci = ctypes.windll.winmm.mciSendStringW
        alias = f"willy_{int(time.time() * 1000)}"
        if mci(f'open "{path}" type mpegvideo alias {alias}', None, 0, None) != 0:
            return False
        try:
            mci(f"play {alias} wait", None, 0, None)
        finally:
            mci(f"close {alias}", None, 0, None)
        return True
    except Exception:
        return False


def speak_text(text: str, audio_base64: Optional[str] = None, blocking: bool = False) -> None:
    """Speaks on the PC speakers: server neural voice when provided, else offline Windows SAPI.
    `blocking` returns only after playback ends ("Hey Willy" waits so it doesn't hear itself)."""
    def _run():
        if audio_base64:
            try:
                fd, path = tempfile.mkstemp(suffix=".mp3", prefix="willy_tts_")
                with os.fdopen(fd, "wb") as f:
                    f.write(base64.b64decode(audio_base64))
                try:
                    if _play_mp3_blocking(path):
                        return
                finally:
                    try:
                        os.remove(path)
                    except OSError:
                        pass
            except Exception:
                pass
        if text:
            safe = text.replace("'", "''")[:1000]
            subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Add-Type -AssemblyName System.Speech; "
                 f"(New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak('{safe}')"],
                capture_output=True, timeout=60, creationflags=CREATE_NO_WINDOW,
            )

    if blocking:
        _run()
    else:
        threading.Thread(target=_run, daemon=True).start()


class LocalExecutor:
    def __init__(self, notifier: Optional[Notifier] = None):
        # GUI hosts pass a notifier that renders toasts on their own UI thread.
        self.notifier = notifier

    def execute_action(self, action: str, payload: dict) -> Dict[str, Any]:
        """
        Executes the requested action locally on the physical Windows PC.
        All AI reasoning happens exclusively on the Server.
        """
        t0 = time.time()
        payload = payload or {}
        try:
            res = self._dispatch((action or "").lower().strip(), payload)
        except Exception as e:
            res = {"success": False, "error": str(e)}
        if isinstance(res, dict):
            res.setdefault("duration_sec", round(time.time() - t0, 3))
        return res

    def _dispatch(self, action_clean: str, payload: dict) -> Dict[str, Any]:
        # 1. Application Launcher
        if action_clean in ("launch_application", "launch_app", "open_app"):
            target = payload.get("target", "")
            args = payload.get("args", "")
            res = launch_windows_app(target=target, args=args)
            msg = res.get("message") or f"Launched {target}"
            self._notify_user(title="App Launched", message=msg)
            return res

        # 2. Close Application
        elif action_clean in ("close_application", "close_app", "kill_app"):
            target = payload.get("target", "")
            res = close_app(target=target)
            msg = res.get("message") or f"Closed {target}"
            self._notify_user(title="App Closed", message=msg)
            return res

        # 3. Safe PowerShell Execution
        elif action_clean in ("execute_powershell", "powershell", "run_powershell"):
            cmd = payload.get("command", "")
            res = execute_powershell(command=cmd)
            self._notify_user(title="PowerShell Executed", message=cmd[:50])
            return res

        # 4. Open Website / URL
        elif action_clean in ("open_url", "browse_url"):
            url = payload.get("url", "")
            res = open_url(url=url)
            self._notify_user(title="Opening Webpage", message=url)
            return res

        # 5. Power & Session Actions
        elif action_clean in ("power_action", "power"):
            sub = payload.get("action", "").lower().strip()
            delay = payload.get("delay_sec", 30)
            if sub == "lock":
                res = lock_workstation()
            elif sub == "unlock":
                res = unlock_or_wake(pin=payload.get("pin"))
            elif sub == "shutdown":
                res = shutdown_system(delay_sec=delay)
            elif sub == "restart":
                res = restart_system(delay_sec=delay)
            elif sub == "sleep":
                res = sleep_system()
            elif sub in ("cancel", "abort", "cancel_shutdown"):
                res = cancel_shutdown()
            else:
                res = {"success": False, "error": f"Unknown power action: {sub}"}

            self._notify_user(title="Power Action", message=f"Executed: {sub}")
            return res

        # 6. Audio Volume Control (exact levels via Core Audio)
        elif action_clean in ("volume_control", "volume"):
            sub = payload.get("action", "").lower().strip()
            level = payload.get("level")
            res = volume_control(action=sub, level=level)
            if not payload.get("quiet"):  # sliders send quiet=True so a drag isn't a toast storm
                self._notify_user(title="Volume", message=res.get("message") or f"Volume {sub}")
            return res

        # 7. Window Management
        elif action_clean in ("window_action", "window"):
            sub = payload.get("action", "").lower().strip()
            target = payload.get("target")

            if sub in ("minimize_all", "show_desktop"):
                res = minimize_all_windows()
            elif sub == "minimize":
                res = minimize_window(target)
            elif sub == "maximize":
                res = maximize_current_window()
            elif sub == "focus" and target:
                res = focus_window(target)
            elif sub == "close":
                res = close_current_window()
            elif sub in ("move_to_monitor", "move"):
                from pc_client.tools.media_tools import move_window

                res = move_window(str(target or ""), int(payload.get("monitor") or 2))
            else:
                res = {"success": False, "error": f"Unknown window action: {sub}"}

            self._notify_user(title="Window Manager", message=f"Action: {sub}")
            return res

        elif action_clean == "list_windows":
            return list_open_windows()

        # 8. Take Screenshot
        elif action_clean in ("take_screenshot", "screenshot"):
            res = take_screenshot()
            self._notify_user(title="Screenshot Saved", message=res.get("message") or "Desktop screenshot captured")
            return res

        # 9. Hardware & System Status
        elif action_clean in ("get_pc_status", "status", "system_status"):
            sys_status = get_system_status()
            net_status = check_network_status()
            telemetry = collect_pc_telemetry()
            return {
                "success": True,
                "system": sys_status.get("status", {}),
                "network": net_status,
                "telemetry": telemetry,
                "timestamp": time.time(),
            }

        elif action_clean in ("system_info", "specs"):
            return {
                "success": True,
                "specs": collect_static_specs(),
                "telemetry": collect_pc_telemetry(),
                "timestamp": time.time(),
            }

        # 10. Schedule Meeting / Calendar
        elif action_clean in ("schedule_meeting", "meeting", "create_calendar_event"):
            import urllib.parse
            import webbrowser
            title = payload.get("title", "New Meeting")
            platform = payload.get("platform", "google_calendar")

            if platform == "teams":
                url = "https://teams.microsoft.com"
            elif platform == "zoom":
                url = "https://zoom.us/start/videomeeting"
            else:
                encoded_title = urllib.parse.quote(title)
                url = f"https://calendar.google.com/calendar/render?action=TEMPLATE&text={encoded_title}"

            webbrowser.open(url)
            self._notify_user(title="Meeting Setup", message=f"Opened calendar for '{title}'")
            return {
                "success": True,
                "action": "schedule_meeting",
                "title": title,
                "url": url,
                "message": f"Opened calendar setup for '{title}'.",
            }

        # 11. Send Message (WhatsApp / Email)
        elif action_clean in ("send_message", "message"):
            import urllib.parse
            import webbrowser
            recipient = payload.get("recipient", "")
            msg_text = payload.get("message", "")
            channel = payload.get("channel", "whatsapp").lower()

            if channel == "email" or "@" in recipient:
                encoded_body = urllib.parse.quote(msg_text)
                url = f"mailto:{recipient}?subject=Message%20from%20Willy&body={encoded_body}"
            else:
                # WhatsApp
                clean_phone = "".join([c for c in recipient if c.isdigit()])
                encoded_msg = urllib.parse.quote(msg_text)
                if clean_phone:
                    url = f"https://web.whatsapp.com/send?phone={clean_phone}&text={encoded_msg}"
                else:
                    url = f"https://web.whatsapp.com/send?text={encoded_msg}"

            webbrowser.open(url)
            self._notify_user(title="Message Sent", message=f"Opened {channel} to {recipient}")
            return {
                "success": True,
                "action": "send_message",
                "channel": channel,
                "recipient": recipient,
                "message": f"Opened {channel} composer for {recipient}.",
            }

        # 12. Live Telemetry
        elif action_clean == "telemetry":
            return {
                "success": True,
                "telemetry": collect_pc_telemetry(),
                "timestamp": time.time(),
            }

        # 13. Processes
        elif action_clean in ("list_processes", "processes", "top_processes"):
            sort_by = "memory" if str(payload.get("sort_by", "cpu")).lower().startswith("mem") else "cpu"
            limit = max(1, min(int(payload.get("limit") or 10), 50))
            procs = get_collector().top_processes(limit=limit, sort_by=sort_by)
            return {"success": True, "sort_by": sort_by, "processes": procs, "timestamp": time.time()}

        elif action_clean in ("kill_process", "end_process"):
            return self._kill_process(payload)

        # 14. Ring PC ("Find My Laptop")
        elif action_clean in ("ring_device", "ring_pc", "find_my_pc"):
            def _play_beacon():
                try:
                    import winsound
                    for _ in range(5):
                        winsound.Beep(1400, 200)
                        winsound.Beep(1800, 300)
                        time.sleep(0.1)
                except Exception:
                    pass
            threading.Thread(target=_play_beacon, daemon=True).start()
            self._notify_user(title="🔔 FIND MY LAPTOP", message=payload.get("message") or "Mobile phone triggered beacon alarm!")
            return {"success": True, "message": "Audible beacon chiming on PC."}

        elif action_clean == "stop_ring":
            return {"success": True, "message": "Beacon stops on its own."}

        # 15. Set Clipboard
        elif action_clean in ("set_clipboard", "clipboard_sync"):
            text = payload.get("text", "")
            ok = set_clipboard_text(text)
            self._notify_user(title="Clipboard Synced", message=f"Received: {text[:60]}")
            return {"success": ok, "message": "Copied to PC clipboard." if ok else "Clipboard is busy.", "text_length": len(text)}

        # 16. Get Clipboard
        elif action_clean == "get_clipboard":
            return {"success": True, "text": get_clipboard_text()}

        # 17. QuickDrop URL / Text
        elif action_clean == "quickdrop":
            import webbrowser
            url = payload.get("url")
            text = payload.get("text")
            title = payload.get("title") or "QuickDrop"
            if url:
                webbrowser.open(url)
                self._notify_user(title=f"🔗 {title}", message=f"Opened link: {url}")
                return {"success": True, "message": "Link opened on PC."}
            if text:
                set_clipboard_text(text)
                self._notify_user(title=f"📝 {title}", message=f"{text[:120]}\n(copied to clipboard)")
                return {"success": True, "message": "Note shown on PC and copied to its clipboard."}
            return {"success": False, "error": "Nothing to drop."}

        # 18. Live Screen Snapshot (quality / size tunable for live mode)
        elif action_clean in ("get_screen_snapshot", "screen_snapshot"):
            from pc_client.tools.vision_tools import capture_screen_image
            quality = max(20, min(int(payload.get("quality") or 60), 90))
            max_w = max(320, min(int(payload.get("max_width") or 800), 1920))
            monitor = payload.get("monitor") or "active"
            b64_data, w, h, _, _, label = capture_screen_image(
                target_monitor=monitor, max_size=(max_w, max_w), quality=quality,
            )
            if b64_data:
                return {"success": True, "image_base64": b64_data, "format": "jpeg",
                        "width": w, "height": h, "label": label, "captured_at": time.time()}
            return {"success": False, "error": "Failed to capture desktop image."}

        # 19. Media Control
        elif action_clean in ("media_control", "media"):
            import ctypes
            sub = payload.get("action", "").lower().strip()
            vk_map = {
                "play_pause": 0xB3,
                "play": 0xB3,
                "pause": 0xB3,
                "next": 0xB0,
                "prev": 0xB1,
                "previous": 0xB1,
                "stop": 0xB2,
                "mute": 0xAD,
                "volume_up": 0xAF,
                "volume_down": 0xAE,
            }
            code = vk_map.get(sub)
            if code:
                ctypes.windll.user32.keybd_event(code, 0, 0, 0)
                ctypes.windll.user32.keybd_event(code, 0, 2, 0)
                self._notify_user(title="Media Control", message=f"Executed: {sub}")
                return {"success": True, "action": sub}
            elif sub == "volume_set" and payload.get("level") is not None:
                return volume_control(action="set", level=payload.get("level"))
            return {"success": False, "error": f"Unknown media action: {sub}"}

        # 20. Screen brightness (laptop panels)
        elif action_clean in ("set_brightness", "brightness"):
            from pc_client.telemetry import _read_brightness, set_brightness

            change = payload.get("change")
            if payload.get("level") is None and change is not None:
                current = _read_brightness()
                if current is None:
                    return {"success": False, "error": "This display doesn't support software brightness control."}
                level = current + int(change)
            else:
                level = int(payload.get("level", 50))
            level = max(0, min(100, level))
            ok = set_brightness(level)
            if not ok:  # WMI via COM unavailable: same call through PowerShell's CIM cmdlets
                ok = execute_powershell(
                    "Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods | "
                    f"Invoke-CimMethod -MethodName WmiSetBrightness -Arguments @{{Timeout=1; Brightness=[byte]{level}}}"
                ).get("success", False)
            if ok:
                get_collector().note_brightness(level)
                if not payload.get("quiet"):
                    self._notify_user(title="Brightness", message=f"Set to {level}%")
                return {"success": True, "level": level, "message": f"Brightness set to {level}%."}
            return {"success": False, "error": "This display doesn't support software brightness control."}

        # 21. Notifications & speech
        elif action_clean in ("notify", "show_notification", "show_message"):
            message = str(payload.get("message") or payload.get("text") or "").strip()
            if not message:
                return {"success": False, "error": "No message to show."}
            self._notify_user(title=payload.get("title") or "Message", message=message)
            return {"success": True, "message": "Notification shown on PC."}

        # Files and folders on this PC.
        elif action_clean in ("manage_file", "file_operation"):
            from pc_client.tools.file_ops import manage_file

            return manage_file(str(payload.get("op") or ""), str(payload.get("path") or ""),
                               str(payload.get("destination") or ""), str(payload.get("content") or ""),
                               bool(payload.get("overwrite")))

        elif action_clean in ("git", "git_action"):
            from pc_client.tools.git_ops import git_action

            return git_action(str(payload.get("action") or "status"), str(payload.get("repo") or ""),
                              str(payload.get("branch") or ""), payload.get("files") or None,
                              str(payload.get("message") or ""), str(payload.get("url") or ""),
                              bool(payload.get("stash")), int(payload.get("lines") or 15))

        elif action_clean == "git_repos":
            from pc_client.tools.git_ops import repos

            return repos(refresh=bool(payload.get("refresh")))

        elif action_clean in ("list_dir", "list_folder"):
            from pc_client.tools.file_ops import list_dir

            return list_dir(str(payload.get("path") or ""))

        elif action_clean in ("open_file", "play_file"):
            from pc_client.tools.media_tools import open_file_smart

            monitor = payload.get("monitor")
            res = open_file_smart(str(payload.get("path") or ""), str(payload.get("name") or ""),
                                  str(payload.get("folder") or ""), str(payload.get("app") or ""),
                                  int(monitor) if monitor else None, bool(payload.get("fullscreen")),
                                  str(payload.get("kind") or ""))
            if res.get("success"):
                self._notify_user(title="Opened", message=res["message"])
            return res

        elif action_clean == "find_files":
            from pc_client.tools.media_tools import find_files

            return find_files(str(payload.get("name") or ""), str(payload.get("folder") or ""),
                              str(payload.get("kind") or ""))

        elif action_clean == "file_to_hub":
            # Upload a PC file to the hub so a browser or the phone can download it.
            from pc_client.tools import file_relay
            from pc_client.tools.file_ops import resolve

            try:
                source = resolve(str(payload.get("path") or ""), must_exist=True)
            except (OSError, ValueError) as e:
                return {"success": False, "error": str(e)}
            if not source.is_file():
                return {"success": False, "error": f"{source.name} is a folder; zip it first to download it."}
            return file_relay.send_file(source, target="")

        elif action_clean in ("set_wallpaper", "wallpaper"):
            from pc_client.tools.file_ops import set_wallpaper

            return set_wallpaper(str(payload.get("path") or ""))

        elif action_clean == "app_volume":
            from pc_client.tools.file_ops import app_volume

            mute = payload.get("mute")
            return app_volume(str(payload.get("app") or ""), payload.get("level"),
                              None if mute is None else bool(mute))

        # Files between the PC and the phone (relayed through the hub).
        elif action_clean in ("send_file_to_phone", "send_file"):
            from pc_client.tools import file_relay

            file, error = file_relay.find_file(str(payload.get("path") or ""), str(payload.get("which") or ""))
            if file is None:
                return {"success": False, "error": error}
            res = file_relay.send_file(file, target=str(payload.get("target") or "phone"))
            if res.get("success") and not payload.get("quiet"):
                self._notify_user(title="Sent to phone", message=res["message"])
            return res

        elif action_clean == "receive_file":
            from pc_client.tools import file_relay

            res = file_relay.receive_file(payload)
            if res.get("success"):
                sender = str(payload.get("from") or "your phone")
                self._notify_user(title=f"File from {sender}", message=res["message"])
            return res

        elif action_clean in ("show_note", "note"):
            # A note from the phone / chat: pop it up, put it on the clipboard, and open long
            # text in Notepad (saved under Documents\Willy Notes) so nothing is lost.
            text = str(payload.get("text") or "").strip()
            if not text:
                return {"success": False, "error": "The note is empty."}
            title = str(payload.get("title") or "Note from Willy").strip()
            copied = set_clipboard_text(text)
            self._notify_user(title=title, message=text[:220] + ("…" if len(text) > 220 else ""))
            path = None
            if len(text) > 220 or "\n" in text:
                folder = Path.home() / "Documents" / "Willy Notes"
                folder.mkdir(parents=True, exist_ok=True)
                path = folder / f"{time.strftime('%Y-%m-%d %H%M%S')} {''.join(c for c in title if c.isalnum() or c in ' -_')[:40].strip()}.txt"
                path.write_text(text, encoding="utf-8")
                subprocess.Popen(["notepad.exe", str(path)], creationflags=CREATE_NO_WINDOW)
            where = " and opened it in Notepad" if path else ""
            return {"success": True, "path": str(path) if path else None,
                    "message": f"Shown on your PC{', copied to its clipboard' if copied else ''}{where}."}

        elif action_clean in ("speak", "say", "speak_text"):
            text = str(payload.get("text") or "").strip()
            if not text and not payload.get("audio_base64"):
                return {"success": False, "error": "Nothing to say."}
            speak_text(text, payload.get("audio_base64"))
            return {"success": True, "message": "Speaking on PC speakers."}

        # 22. Folders, typing and hotkeys
        elif action_clean in ("open_folder", "open_path"):
            return self._open_folder(str(payload.get("path") or ""))

        elif action_clean == "type_text":
            # Drafts text into an app (focusing or opening it first). It is pasted, never
            # typed key by key, and Enter is never pressed: the user reviews and sends it.
            text = str(payload.get("text") or "")
            if not text:
                return {"success": False, "error": "No text to type."}
            app = str(payload.get("app") or "").strip()
            if app:
                focused = focus_window(app)
                if not (isinstance(focused, dict) and focused.get("success")):
                    launch_windows_app(target=app)
                    time.sleep(3.0)
                    focus_window(app)
                time.sleep(0.6)
            import pyautogui
            previous = get_clipboard_text()
            if not set_clipboard_text(text):
                return {"success": False, "error": "The clipboard is busy; try again."}
            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.3)
            set_clipboard_text(previous)
            where = f" into {app}" if app else ""
            return {"success": True, "message": f"Drafted the text{where}. Review it and press Enter or Send yourself."}

        elif action_clean in ("read_window_text", "read_window", "read_screen_text"):
            from pc_client.tools.window_reader import read_window_text
            return read_window_text(payload.get("app") or payload.get("target"), int(payload.get("max_chars") or 4000))

        elif action_clean in ("press_keys", "hotkey"):
            keys = [k.strip().lower() for k in str(payload.get("keys") or "").split("+") if k.strip()]
            if not keys:
                return {"success": False, "error": "No keys given (e.g. 'ctrl+c')."}
            import pyautogui
            pyautogui.hotkey(*keys)
            return {"success": True, "message": f"Pressed {'+'.join(keys)}."}

        # 23. Legacy Command Fallback (Regex-based)
        elif action_clean == "command":
            query = payload.get("query", "")
            res = dispatch_pc_command(query)
            reply = res.get("reply", "Command executed.")
            self._notify_user(title="Willy Action", message=reply, query=query)
            if payload.get("speak_on_pc"):
                speak_text(reply)
            return {
                "success": res.get("success", True),
                "query": query,
                "reply": reply,
                "tool_used": res.get("tool"),
                "tool_result": res.get("result"),
            }

        return {"success": False, "error": f"Unknown action type: {action_clean}"}

    def _kill_process(self, payload: dict) -> Dict[str, Any]:
        import psutil
        pid = payload.get("pid")
        name = str(payload.get("name") or "").strip()
        if pid is None and name:
            return close_app(target=name)
        try:
            proc = psutil.Process(int(pid))
            proc_name = proc.name()
        except (psutil.NoSuchProcess, TypeError, ValueError):
            return {"success": False, "error": f"No process with PID {pid}."}
        if proc_name.lower() in PROTECTED_PROCESSES or int(pid) <= 4 or int(pid) == os.getpid():
            return {"success": False, "error": f"Refusing to end protected process {proc_name}."}
        try:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except psutil.TimeoutExpired:
                proc.kill()
        except psutil.AccessDenied:
            return {"success": False, "error": f"Access denied ending {proc_name} (it may need admin rights)."}
        except psutil.NoSuchProcess:
            pass
        self._notify_user(title="Process Ended", message=f"{proc_name} (PID {pid})")
        return {"success": True, "pid": int(pid), "name": proc_name, "message": f"Ended {proc_name}."}

    def _open_folder(self, raw: str) -> Dict[str, Any]:
        key = raw.strip().lower().rstrip("/\\")
        path = Path(os.path.expanduser(KNOWN_FOLDERS.get(key, raw.strip() or "~")))
        if not path.exists():
            return {"success": False, "error": f"Folder not found: {raw}"}
        try:
            os.startfile(str(path))
        except Exception as e:
            return {"success": False, "error": str(e)}
        self._notify_user(title="Folder Opened", message=str(path))
        return {"success": True, "path": str(path), "message": f"Opened {path.name or path}."}

    def _notify_user(self, title: str, message: str, query: Optional[str] = None):
        """Displays non-blocking floating dark HUD desktop toast."""
        try:
            if self.notifier:
                self.notifier(f"⚡ {title}", message, query)
            else:
                show_desktop_toast(title=f"⚡ {title}", message=message, query=query)
        except Exception:
            pass


executor = LocalExecutor()
