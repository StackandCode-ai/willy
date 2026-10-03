"""
Willy Morning Briefing & Telemetry Service.
Manages daily wake-up phone calls, ChatGPT thread task extraction,
mobile notifications (emails, WhatsApp, missed calls), and scheduled work/alarms.
"""

import json
import time
import datetime
from typing import Dict, Any, List, Optional
from server.config import settings, DATA_DIR
from server.voice_service import ServerVoiceService
from server import timeutil

CONFIG_FILE = DATA_DIR / "morning_config.json"
TELEMETRY_FILE = DATA_DIR / "mobile_telemetry.json"


class MorningBriefingService:
    def __init__(self, data_dir=None):
        # One per account space; the default (no folder given) is the hub's own data folder.
        data_dir = data_dir or DATA_DIR
        data_dir.mkdir(parents=True, exist_ok=True)
        self.config_file = data_dir / CONFIG_FILE.name
        self.telemetry_file = data_dir / TELEMETRY_FILE.name
        self.config = self._load_config()
        self.telemetry = self._load_telemetry()
        self.voice_service = ServerVoiceService()
        # Set by the app at startup so task extraction reuses the shared LLM client.
        self.orchestrator = None

    def _load_config(self) -> Dict[str, Any]:
        default_config = {
            "call_time": "07:00",
            "call_enabled": True,
            "chatgpt_thread_id": "",
            "chatgpt_api_key": "",
            "chatgpt_thread_summary": "1. Review project requirements\n2. Check server logs and telemetry\n3. Complete daily standup report",
            "cached_tasks": [
                "Review project requirements",
                "Check server logs and telemetry",
                "Complete daily standup report"
            ],
            "reminders": [],
            "alarms": [],
            "last_call_date": ""
        }
        if self.config_file.exists():
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    default_config.update(data)
            except Exception as e:
                print(f"[MorningBriefing] Error reading config: {e}")
        return default_config

    def save_config(self):
        try:
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2)
        except Exception as e:
            print(f"[MorningBriefing] Error saving config: {e}")


    def _load_telemetry(self) -> Dict[str, Any]:
        default_telemetry = {
            "unread_emails_count": 0,
            "email_senders": [],
            "missed_calls_count": 0,
            "missed_calls": [],
            "whatsapp_unread_count": 0,
            "whatsapp_senders": [],
            "total_notifications_count": 0,
            "other_notifications": [],
            "updated_at": 0.0
        }
        if self.telemetry_file.exists():
            try:
                with open(self.telemetry_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    default_telemetry.update(data)
            except Exception as e:
                print(f"[MorningBriefing] Error reading telemetry: {e}")
        return default_telemetry

    def _save_telemetry(self):
        try:
            with open(self.telemetry_file, "w", encoding="utf-8") as f:
                json.dump(self.telemetry, f, indent=2)
        except Exception as e:
            print(f"[MorningBriefing] Error saving telemetry: {e}")

    def update_telemetry(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Updates live mobile notifications, missed calls, and unread WhatsApp counts."""
        self.telemetry["unread_emails_count"] = int(data.get("unread_emails_count", self.telemetry["unread_emails_count"]))
        self.telemetry["email_senders"] = list(data.get("email_senders", self.telemetry["email_senders"]))
        self.telemetry["missed_calls_count"] = int(data.get("missed_calls_count", self.telemetry["missed_calls_count"]))
        self.telemetry["missed_calls"] = list(data.get("missed_calls", self.telemetry["missed_calls"]))
        self.telemetry["whatsapp_unread_count"] = int(data.get("whatsapp_unread_count", self.telemetry["whatsapp_unread_count"]))
        self.telemetry["whatsapp_senders"] = list(data.get("whatsapp_senders", self.telemetry["whatsapp_senders"]))
        self.telemetry["total_notifications_count"] = int(data.get("total_notifications_count", self.telemetry["total_notifications_count"]))
        self.telemetry["other_notifications"] = list(data.get("other_notifications", self.telemetry["other_notifications"]))
        self.telemetry["updated_at"] = time.time()
        self._save_telemetry()
        return self.telemetry

    def update_config(self, new_config: Dict[str, Any]) -> Dict[str, Any]:
        """Updates configuration for morning call, ChatGPT thread, and reminders."""
        for k in ["call_time", "call_enabled", "chatgpt_thread_id", "chatgpt_api_key", "chatgpt_thread_summary"]:
            if k in new_config:
                self.config[k] = new_config[k]

        if "cached_tasks" in new_config and isinstance(new_config["cached_tasks"], list):
            self.config["cached_tasks"] = new_config["cached_tasks"]

        self.save_config()
        return self.config

    async def sync_chatgpt_thread_tasks(self) -> List[str]:
        """
        Fetches or extracts tasks from the user's specified ChatGPT thread.
        Supports OpenAI Threads API or AI extraction from thread summary.
        """
        thread_id = self.config.get("chatgpt_thread_id", "").strip()
        api_key = self.config.get("chatgpt_api_key", "").strip() or settings.OPENAI_API_KEY
        summary_text = self.config.get("chatgpt_thread_summary", "").strip()

        # 1. If OpenAI Assistant Thread ID is configured
        if thread_id and api_key and thread_id.startswith("thread_"):
            try:
                from openai import OpenAI
                client = OpenAI(api_key=api_key)
                messages = client.beta.threads.messages.list(thread_id=thread_id, limit=10)
                thread_texts = []
                for msg in reversed(list(messages.data)):
                    role = msg.role
                    for part in msg.content:
                        if hasattr(part, "text") and hasattr(part.text, "value"):
                            thread_texts.append(f"{role.capitalize()}: {part.text.value}")

                summary_text = "\n\n".join(thread_texts)
            except Exception as e:
                print(f"[MorningBriefing] Error querying OpenAI thread: {e}")

        # 2. Extract tasks using LLM reasoning
        if summary_text:
            extracted = await self._extract_tasks_with_llm(summary_text)
            if extracted:
                self.config["cached_tasks"] = extracted
                self.save_config()
                return extracted

        return self.config.get("cached_tasks", [])

    async def _extract_tasks_with_llm(self, conversation_content: str) -> List[str]:
        """Uses LLM to cleanly parse pending actionable tasks from conversation text."""
        prompt = (
            "Analyze the following conversation from a ChatGPT project thread and extract a clean list "
            "of pending, incomplete, or scheduled tasks/action items for the user.\n\n"
            f"Conversation:\n{conversation_content}\n\n"
            "Return ONLY a JSON array of strings, where each string is a concise task (e.g. ['Finish API integration', 'Deploy server']). "
            "Do not include markdown or explanations."
        )

        try:
            orch = self.orchestrator
            if orch is None:
                from server.orchestrator import ServerOrchestrator
                orch = ServerOrchestrator()
            reply = (await orch.complete_text(prompt)).strip()
            # Clean possible markdown fence
            if "```" in reply:
                reply = reply.split("```")[1]
                if reply.startswith("json"):
                    reply = reply[4:]
                reply = reply.strip()
            tasks = json.loads(reply)
            if isinstance(tasks, list):
                return [str(t).strip() for t in tasks if str(t).strip()]
        except Exception as e:
            print(f"[MorningBriefing] Task extraction error: {e}")

        # Fallback: line-by-line bullet extraction
        lines = [line.lstrip("0123456789.-*• ").strip() for line in conversation_content.splitlines() if line.strip()]
        return lines[:5] if lines else ["Review current project tasks"]

    def add_reminder(self, text: str, remind_time: str = "", date: Optional[str] = None,
                     in_minutes: Optional[float] = None, kind: str = "reminder") -> Dict[str, Any]:
        """Schedules a new reminder, at a clock time or `in_minutes` from now (exact to the
        second, e.g. timers).

        Relative dates ('tomorrow', 'friday') are resolved to a fixed ISO date now, so the
        reminder doesn't slide forward every day.
        """
        now = timeutil.user_now()
        due_at = None
        if in_minutes is not None and float(in_minutes) > 0:
            due = now + datetime.timedelta(seconds=round(float(in_minutes) * 60))
            clock, day, due_at = (due.hour, due.minute), due.date(), due.timestamp()
        else:
            clock = timeutil.parse_clock(remind_time)
            day = timeutil.parse_date(date, now.date())
            if clock and not date:
                # "at 9 AM" said at 10 PM means tomorrow morning.
                at = now.replace(hour=clock[0], minute=clock[1], second=0, microsecond=0)
                if at < now:
                    day = day + datetime.timedelta(days=1)
        reminder = {
            "id": f"rem_{int(time.time()*1000)}",
            "text": text,
            "time": timeutil.format_clock(*clock) if clock else remind_time,
            "date": day.isoformat(),
            "created_at": time.time(),
            "completed": False,
        }
        if due_at is not None:
            reminder["due_at"] = due_at
        if kind != "reminder":
            reminder["kind"] = kind
        self.config.setdefault("reminders", []).append(reminder)
        self.save_config()
        return reminder

    def list_reminders(self) -> List[Dict[str, Any]]:
        return self.config.get("reminders", [])

    def upcoming_reminders(self) -> List[Dict[str, Any]]:
        """Reminders and timers that haven't fired, been completed or been missed yet."""
        return [r for r in self.config.get("reminders", [])
                if not r.get("completed") and not r.get("fired_at") and not r.get("missed")]

    def find_reminders(self, words: str) -> List[Dict[str, Any]]:
        """Upcoming reminders whose text contains every given word (case-insensitive)."""
        wanted = [w for w in (words or "").lower().split() if w not in ("the", "a", "my", "to", "reminder")]
        if not wanted:
            return []
        return [r for r in self.upcoming_reminders() if all(w in str(r.get("text", "")).lower() for w in wanted)]

    def _find(self, key: str, item_id: str) -> Optional[Dict[str, Any]]:
        for item in self.config.get(key, []):
            if item.get("id") == item_id:
                return item
        return None

    def complete_reminder(self, reminder_id: str, completed: bool = True) -> Optional[Dict[str, Any]]:
        rem = self._find("reminders", reminder_id)
        if rem is not None:
            rem["completed"] = completed
            self.save_config()
        return rem

    def delete_reminder(self, reminder_id: str) -> bool:
        before = len(self.config.get("reminders", []))
        self.config["reminders"] = [r for r in self.config.get("reminders", []) if r.get("id") != reminder_id]
        if len(self.config["reminders"]) != before:
            self.save_config()
            return True
        return False

    def add_alarm(self, alarm_time: str, label: str = "Morning Alarm") -> Dict[str, Any]:
        """Schedules a daily alarm."""
        clock = timeutil.parse_clock(alarm_time)
        alarm = {
            "id": f"alarm_{int(time.time()*1000)}",
            "time": timeutil.format_clock(*clock) if clock else alarm_time,
            "label": label,
            "enabled": True,
            "created_at": time.time()
        }
        self.config.setdefault("alarms", []).append(alarm)
        self.save_config()
        return alarm

    def list_alarms(self) -> List[Dict[str, Any]]:
        return self.config.get("alarms", [])

    def set_alarm_enabled(self, alarm_id: str, enabled: bool) -> Optional[Dict[str, Any]]:
        alarm = self._find("alarms", alarm_id)
        if alarm is not None:
            alarm["enabled"] = enabled
            self.save_config()
        return alarm

    def delete_alarm(self, alarm_id: str) -> bool:
        before = len(self.config.get("alarms", []))
        self.config["alarms"] = [a for a in self.config.get("alarms", []) if a.get("id") != alarm_id]
        if len(self.config["alarms"]) != before:
            self.save_config()
            return True
        return False

    async def generate_briefing(self, include_audio: bool = True, pc_online: bool = False) -> Dict[str, Any]:
        """
        Builds the complete spoken daily morning briefing script, structured telemetry summary,
        and pre-synthesized neural speech MP3 audio.
        """
        now = timeutil.user_now()
        greeting_time = now.strftime("%I:%M %p").lstrip("0")
        date_str = now.strftime("%A, %B %d")
        greeting = "Good morning" if now.hour < 12 else ("Good afternoon" if now.hour < 17 else "Good evening")

        tasks = self.config.get("cached_tasks", [])
        unread_emails = self.telemetry.get("unread_emails_count", 0)
        email_senders = self.telemetry.get("email_senders", [])
        missed_calls = self.telemetry.get("missed_calls_count", 0)
        missed_call_list = self.telemetry.get("missed_calls", [])
        whatsapp_unread = self.telemetry.get("whatsapp_unread_count", 0)
        whatsapp_senders = self.telemetry.get("whatsapp_senders", [])
        total_notifications = self.telemetry.get("total_notifications_count", 0)

        # Build natural spoken script
        parts = [
            f"{greeting}! It is {greeting_time} on {date_str}. Here is your morning briefing."
        ]

        # 1. ChatGPT Thread Tasks
        if tasks:
            tasks_formatted = ". ".join(tasks[:4])
            parts.append(f"From your ChatGPT project thread, your key tasks for today are: {tasks_formatted}.")
        else:
            parts.append("You have no pending tasks recorded from your ChatGPT thread.")

        today_iso = now.date().isoformat()
        todays = [
            r for r in self.config.get("reminders", [])
            if not r.get("completed") and r.get("date") == today_iso and r.get("text")
        ]
        if todays:
            listed = ", ".join(f"{r['text']} at {r.get('time', '')}".strip() for r in todays[:3])
            parts.append(f"You have {len(todays)} reminder{'s' if len(todays) > 1 else ''} today: {listed}.")

        # 2. Mails & Mobile Notifications
        mobile_updates = []
        if unread_emails > 0:
            sender_str = f" from {', '.join(email_senders[:2])}" if email_senders else ""
            mobile_updates.append(f"{unread_emails} unread email{'s' if unread_emails > 1 else ''}{sender_str}")

        if missed_calls > 0:
            call_names = [c.get("name", "Unknown") if isinstance(c, dict) else str(c) for c in missed_call_list[:2]]
            name_str = f" from {', '.join(call_names)}" if call_names else ""
            mobile_updates.append(f"{missed_calls} missed call{'s' if missed_calls > 1 else ''}{name_str}")

        if whatsapp_unread > 0:
            wa_names = [w.get("name", "Someone") if isinstance(w, dict) else str(w) for w in whatsapp_senders[:2]]
            wa_str = f" from {', '.join(wa_names)}" if wa_names else ""
            mobile_updates.append(f"{whatsapp_unread} unread WhatsApp message{'s' if whatsapp_unread > 1 else ''}{wa_str}")

        if mobile_updates:
            parts.append("On your phone: " + ", ".join(mobile_updates) + f". You have a total of {total_notifications} notifications.")
        else:
            parts.append("Your mobile notifications and messages are completely clear.")

        # 3. PC Status
        pc_status_str = "Your Windows PC is online and ready for commands." if pc_online else "Your Windows PC is currently in standby."
        parts.append(pc_status_str)

        # 4. Interactive Follow-up Invitation
        parts.append("Would you like me to schedule a reminder, set an alarm, or open any apps on your computer?")

        script = " ".join(parts)

        # Pre-synthesize speech
        audio_b64 = None
        if include_audio:
            try:
                audio_b64 = await self.voice_service.synthesize_speech_base64(script)
            except Exception as e:
                print(f"[MorningBriefing] Synthesis error: {e}")

        return {
            "success": True,
            "script": script,
            "audio_base64": audio_b64,
            "tasks": tasks,
            "telemetry": self.telemetry,
            "config": {
                "call_time": self.config.get("call_time", "07:00"),
                "call_enabled": self.config.get("call_enabled", True),
                "chatgpt_thread_id": self.config.get("chatgpt_thread_id", ""),
            },
            "reminders": self.config.get("reminders", []),
            "alarms": self.config.get("alarms", []),
            "pc_online": pc_online,
            "timestamp": time.time(),
        }


morning_service = MorningBriefingService()
