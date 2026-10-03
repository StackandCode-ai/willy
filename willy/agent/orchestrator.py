"""
Autonomous multi-turn LLM orchestrator with native function/tool execution.
"""

import json
from typing import List, Dict, Any, Optional
from willy.config import settings
from willy.agent.prompts import WILLY_SYSTEM_PROMPT
from willy.tools.registry import get_all_tool_definitions, execute_tool


class AgentOrchestrator:
    """
    Orchestrates user queries, LLM reasoning, structured tool calls,
    and returns a clean spoken response.
    """

    def __init__(self, provider: str = None, model: str = None):
        self.provider = (provider or settings.LLM_PROVIDER).lower()
        if self.provider == "groq":
            self.model = model or settings.GROQ_MODEL
        elif self.provider == "gemini":
            self.model = model or getattr(settings, "GEMINI_MODEL", "gemini-2.5-flash")
        else:
            self.model = model or settings.OPENAI_MODEL
        self.client = None
        self.conversation_history: List[Dict[str, Any]] = [
            {"role": "system", "content": WILLY_SYSTEM_PROMPT}
        ]
        self._init_client()

    def _init_client(self):
        if self.provider == "groq":
            try:
                from groq import Groq
                api_key = settings.GROQ_API_KEY
                if not api_key:
                    print("[!] Warning: GROQ_API_KEY is not set. Please set it in .env")
                self.client = Groq(api_key=api_key)
            except ImportError:
                print("[-] 'groq' package not installed. Run: pip install groq")
        elif self.provider == "gemini":
            try:
                from openai import OpenAI
                import os
                api_key = getattr(settings, "GEMINI_API_KEY", "") or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
                if not api_key:
                    print("[!] Warning: GEMINI_API_KEY is not set. Please set it in .env (get free key at https://aistudio.google.com)")
                self.client = OpenAI(
                    api_key=api_key or "missing_key",
                    base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
                )
            except ImportError:
                print("[-] 'openai' package not installed. Run: pip install openai")
        elif self.provider == "openai":
            try:
                from openai import OpenAI
                api_key = settings.OPENAI_API_KEY
                if not api_key:
                    print("[!] Warning: OPENAI_API_KEY is not set. Please set it in .env")
                self.client = OpenAI(api_key=api_key)
            except ImportError:
                print("[-] 'openai' package not installed. Run: pip install openai")
        else:
            raise ValueError(f"Unknown LLM provider: {self.provider}")

    def reset_conversation(self):
        """Clears conversation context back to system prompt."""
        self.conversation_history = [
            {"role": "system", "content": WILLY_SYSTEM_PROMPT}
        ]

    def process_query(self, user_text: str, on_tool_start=None, on_tool_done=None) -> str:
        """
        Executes the full reasoning & tool calling loop for a user query.
        Returns the final spoken text response.
        """
        # 1. Fast Local Offline Intent Router (100% offline, zero internet, zero latency)
        from willy.agent.local_router import route_local_intent
        local_res = route_local_intent(user_text)
        if local_res:
            tool_name = local_res.get("tool_name")
            tool_result = local_res.get("tool_result")
            reply = local_res.get("reply", "Done.")
            if on_tool_start and tool_name:
                on_tool_start(tool_name, {})
            if on_tool_done and tool_name:
                on_tool_done(tool_name, tool_result)
            self.conversation_history.append({"role": "user", "content": user_text})
            self.conversation_history.append({"role": "assistant", "content": reply})
            return reply

        # 2. Check Internet Connectivity for Cloud Reasoning
        from willy.tools.network_tools import is_internet_available
        if not is_internet_available():
            offline_msg = "I am currently in offline mode with no internet connection. I can still control your applications, power, volume, and settings locally, but answering general knowledge queries requires an active internet connection."
            self.conversation_history.append({"role": "user", "content": user_text})
            self.conversation_history.append({"role": "assistant", "content": offline_msg})
            return offline_msg

        # Append user input for cloud reasoning
        self.conversation_history.append({"role": "user", "content": user_text})

        # Keep history compact (system prompt + last 10 messages)
        if len(self.conversation_history) > 12:
            self.conversation_history = [self.conversation_history[0]] + self.conversation_history[-10:]

        max_iterations = 5
        iteration = 0

        # Model and Key fallback lists for Groq resilience
        groq_models = [self.model]
        for fallback in ("qwen/qwen3.8-27b", "openai/gpt-oss-20b", "qwen/qwen3.6-27b", "openai/gpt-oss-120b"):
            if fallback not in groq_models:
                groq_models.append(fallback)

        groq_keys = [k.strip() for k in [getattr(settings, "GROQ_API_KEY", ""), getattr(settings, "GROQ_API_KEY_BACKUP", "")] if k and not k.startswith("your_")]

        while iteration < max_iterations:
            iteration += 1

            response = None
            success = False
            last_err_str = ""

            # Attempt completion with automatic model and key cascading
            if self.provider == "groq":
                from groq import Groq
                for key_idx, key in enumerate(groq_keys):
                    if success:
                        break
                    active_client = self.client if key_idx == 0 else Groq(api_key=key)

                    for candidate_model in groq_models:
                        try:
                            response = active_client.chat.completions.create(
                                model=candidate_model,
                                messages=self.conversation_history,
                                tools=get_all_tool_definitions(),
                                tool_choice="auto",
                                temperature=0.2,
                                max_tokens=600 if any(k in user_text.lower() for k in ("create", "tool", "upgrade", "code", "write")) else 300,
                                timeout=12.0,
                            )
                            success = True
                            break
                        except Exception as e:
                            last_err_str = str(e)
                            if "429" in last_err_str or "rate_limit" in last_err_str.lower():
                                print(f"[!] Groq rate limit on '{candidate_model}' (key #{key_idx + 1}). Cascading to next fallback...")
                                continue
                            else:
                                print(f"[-] Groq API error on '{candidate_model}': {e}")
                                break
            else:
                try:
                    response = self.client.chat.completions.create(
                        model=self.model,
                        messages=self.conversation_history,
                        tools=get_all_tool_definitions(),
                        tool_choice="auto",
                        temperature=0.2,
                        max_tokens=600 if any(k in user_text.lower() for k in ("create", "tool", "upgrade", "code", "write")) else 300,
                        timeout=12.0,
                    )
                    success = True
                except Exception as e:
                    last_err_str = str(e)

            # Fallback 1: Google Gemini (generous free tier & low cost)
            gemini_key = getattr(settings, "GEMINI_API_KEY", "") or os.getenv("GEMINI_API_KEY", "") or os.getenv("GOOGLE_API_KEY", "")
            if not success and gemini_key and not gemini_key.startswith("your_"):
                try:
                    from openai import OpenAI
                    print("[*] Cascading to Google Gemini fallback...")
                    gemini_client = OpenAI(
                        api_key=gemini_key,
                        base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
                    )
                    response = gemini_client.chat.completions.create(
                        model=getattr(settings, "GEMINI_MODEL", "gemini-2.5-flash"),
                        messages=self.conversation_history,
                        tools=get_all_tool_definitions(),
                        tool_choice="auto",
                        temperature=0.2,
                        max_tokens=400,
                    )
                    success = True
                except Exception as e:
                    print(f"[-] Google Gemini fallback error: {e}")

            # Fallback 2: OpenAI (if configured)
            if not success and settings.OPENAI_API_KEY and not settings.OPENAI_API_KEY.startswith("your_"):
                try:
                    from openai import OpenAI
                    print("[*] Cascading to OpenAI fallback...")
                    oai_client = OpenAI(api_key=settings.OPENAI_API_KEY)
                    response = oai_client.chat.completions.create(
                        model="gpt-4o-mini",
                        messages=self.conversation_history,
                        tools=get_all_tool_definitions(),
                        tool_choice="auto",
                        temperature=0.2,
                        max_tokens=300,
                    )
                    success = True
                except Exception as e:
                    print(f"[-] OpenAI fallback error: {e}")

            if not success or response is None:
                friendly_limit_msg = (
                    "My cloud AI reasoning quota is temporarily reached, but all your local PC commands "
                    "(like launching apps, controlling volume, file search, and system settings) continue to work offline! "
                    "You can also add a free Gemini API key (https://aistudio.google.com) or a second Groq key in your .env file."
                )
                print(f"[-] All LLM models/keys exhausted: {last_err_str}")
                return friendly_limit_msg

            choice = response.choices[0]
            message = choice.message

            # Check if model chose to call tools
            if message.tool_calls:
                # Add assistant message with tool calls to history
                self.conversation_history.append(message)

                for tool_call in message.tool_calls:
                    fn_name = tool_call.function.name
                    fn_args_raw = tool_call.function.arguments

                    try:
                        fn_args = json.loads(fn_args_raw) if isinstance(fn_args_raw, str) else fn_args_raw
                    except json.JSONDecodeError:
                        fn_args = {}

                    if on_tool_start:
                        on_tool_start(fn_name, fn_args)
                    else:
                        print(f"[*] Willy executing: {fn_name}({fn_args})")

                    # Execute the tool
                    result = execute_tool(fn_name, fn_args)

                    if on_tool_done:
                        on_tool_done(fn_name, result)

                    # Append tool result to history
                    self.conversation_history.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": fn_name,
                        "content": json.dumps(result),
                    })

                # Loop continues to give LLM a chance to summarize or make follow-up tool calls
                continue

            # Model produced a final conversational response
            final_reply = message.content or "Done."
            self.conversation_history.append({"role": "assistant", "content": final_reply})
            return final_reply

        return "I completed the requested actions."
