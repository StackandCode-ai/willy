"""
Structured interaction logger for Willy: logs hearing, speaking, tools, and training dataset.
"""

import os
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional

LOGS_DIR = Path("logs")
LOGS_DIR.mkdir(exist_ok=True)

INTERACTION_LOG_FILE = LOGS_DIR / "interactions.jsonl"
TRAINING_DATASET_FILE = LOGS_DIR / "training_dataset.jsonl"


class InteractionLogger:
    """
    Records every interaction lifecycle:
    - User speech (Heard transcript)
    - LLM thoughts & tool calls (Action)
    - Tool execution output
    - Willy spoken response (Speak)
    - Formats ready-to-train conversational JSONL (OpenAI / Fine-Tuning format)
    """

    def __init__(self):
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")

    def log_interaction(
        self,
        heard: str,
        willy_spoke: str,
        tool_name: Optional[str] = None,
        tool_args: Optional[Dict[str, Any]] = None,
        tool_result: Optional[Dict[str, Any]] = None,
        duration_sec: float = 0.0,
    ):
        timestamp = datetime.now().isoformat()

        record = {
            "session_id": self.session_id,
            "timestamp": timestamp,
            "heard": heard,
            "action": {
                "tool_name": tool_name,
                "tool_args": tool_args or {},
                "tool_result": tool_result or {},
            } if tool_name else None,
            "spoke": willy_spoke,
            "duration_sec": round(duration_sec, 2),
        }

        # 1. Append to human-readable interactions.jsonl
        try:
            with open(INTERACTION_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as e:
            print(f"[-] Failed to write interaction log: {e}")

        # 2. Append to LLM fine-tuning dataset format
        try:
            messages = [
                {
                    "role": "system",
                    "content": "You are Willy, an autonomous voice-driven Windows AI assistant and system controller."
                },
                {"role": "user", "content": heard}
            ]

            if tool_name:
                messages.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "type": "function",
                            "function": {
                                "name": tool_name,
                                "arguments": json.dumps(tool_args or {}, ensure_ascii=False)
                            }
                        }
                    ]
                })
                messages.append({
                    "role": "tool",
                    "name": tool_name,
                    "content": json.dumps(tool_result or {}, ensure_ascii=False)
                })

            messages.append({"role": "assistant", "content": willy_spoke})

            dataset_entry = {"messages": messages}
            with open(TRAINING_DATASET_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(dataset_entry, ensure_ascii=False) + "\n")
        except Exception as e:
            print(f"[-] Failed to write training dataset: {e}")


# Global logger instance
logger = InteractionLogger()
