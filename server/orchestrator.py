"""
Server LLM Orchestrator.
Central AI Brain running on the Server.
Supports Groq, Google Gemini (OpenAI-compatible endpoint) and OpenAI.

- Fully async clients, so a slow model call never stalls heartbeats or other requests.
- Independent conversation per session (dashboard, phone, voice call...).
- Multi-step tool use: parallel tool calls and up to MAX_TOOL_ROUNDS follow-ups,
  e.g. "open chrome and set volume to 30" runs both actions.
- Live device telemetry is injected into the prompt, so status questions are answered
  without a PC round-trip.
"""

import re
import json
import time
import asyncio
from collections import OrderedDict
from typing import List, Dict, Any, Optional, Callable, Awaitable

from server.config import settings
from server import ai_config
from server.prompts import SERVER_SYSTEM_PROMPT
from server.pc_tools_schema import PC_TOOLS_DEFINITIONS, DIRECT_REPLY_TOOLS, select_tools

ToolExecutor = Callable[[str, Dict[str, Any]], Awaitable[Dict[str, Any]]]

MAX_TOOL_ROUNDS = 3
MAX_HISTORY_MESSAGES = 16
MAX_SESSIONS = 64
SESSION_IDLE_RESET_SEC = 30 * 60
TOOL_RESULT_MAX_CHARS = 3500
VOICE_MAX_TOKENS = 260
VOICE_RULES = ("VOICE CALL: your reply is spoken aloud. Answer in one or two short sentences (max ~40 words), "
               "no lists, tables, headings or markdown. Give the key point first; offer more detail only if asked.")
HISTORY_TOOL_RESULT_MAX_CHARS = 500  # older tool results are resent on every call: keep them short
MAX_QUOTA_WAIT_SEC = 20
# The free Groq tier rejects any single request above 8k tokens per minute ("Request too
# large"), counting max_tokens too. Prompts are trimmed to this estimate before each call.
PROMPT_TOKEN_BUDGET = 6600
HISTORY_REPLY_MAX_CHARS = 700  # when every model is briefly rate-limited, wait this long at most
_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.S | re.I)
_BULKY_KEYS = {"image_base64", "audio_base64", "screenshot_base64"}


def clean_reply(text: Optional[str]) -> str:
    """Strips reasoning blocks and markdown clutter that sounds bad when spoken."""
    if not text:
        return ""
    text = _THINK_RE.sub("", text)
    if "<think>" in text.lower():  # unterminated reasoning block
        text = text[: text.lower().index("<think>")]
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    return text.strip()


def compact_tool_result(result: Any) -> str:
    """JSON for the model with base64 blobs removed and size capped."""
    def scrub(value):
        if isinstance(value, dict):
            return {k: ("<omitted>" if k in _BULKY_KEYS else scrub(v)) for k, v in value.items()}
        if isinstance(value, list):
            return [scrub(v) for v in value[:25]]
        return value

    try:
        text = json.dumps(scrub(result), default=str)
    except Exception:
        text = str(result)
    if len(text) > TOOL_RESULT_MAX_CHARS:
        text = text[:TOOL_RESULT_MAX_CHARS] + '... [truncated]"'
    return text


class _Session:
    def __init__(self):
        self.history: List[Dict[str, Any]] = []
        self.lock = asyncio.Lock()
        self.last_used = time.time()


class ServerOrchestrator:
    """
    Central Brain orchestrating multi-turn user conversations and delegating
    device actions to connected Windows PC clients.
    """

    def __init__(self, provider: Optional[str] = None, model: Optional[str] = None):
        self._pinned = (provider, model)  # explicit choice (tests); otherwise follow ai_config
        self.client = None
        self._sessions: "OrderedDict[str, _Session]" = OrderedDict()
        self.last_error: Optional[str] = None
        self._cooldown: Dict[str, float] = {}  # model -> time it may be tried again
        self.last_model: Optional[str] = None
        self.reload()

    def reload(self) -> None:
        """(Re)reads which AI to use (ai_config: dashboard choice or .env) and reconnects."""
        provider, model = self._pinned
        cfg = ai_config.current()
        if provider or model:
            cfg = ai_config.candidates(provider or cfg.provider, model or "", cfg.api_key if (provider or cfg.provider) == cfg.provider
                                       else ai_config.key_for(provider or cfg.provider))
        self.provider = cfg.provider
        self.model = cfg.model
        self.reasoning_effort = settings.LLM_REASONING_EFFORT or None
        # Main model first, then fallbacks (same provider) for when it is rate-limited.
        fallbacks = [m.strip() for m in (settings.LLM_FALLBACK_MODELS or "").split(",") if m.strip()]
        self.models: List[str] = [self.model] + ([m for m in fallbacks if m != self.model] if self.provider == "groq" else [])
        self._cooldown.clear()
        self._init_client(cfg)

    def _init_client(self, cfg: "ai_config.AIConfig") -> None:
        self.client = None
        if not cfg.ready:
            print(f"[Server Brain] No AI set up yet ({cfg.provider}): add a key in the dashboard (Settings > AI) or server/.env.")
            return
        try:
            self.client = ai_config.make_client(cfg)
        except ImportError as e:
            print(f"[Server Brain] Missing package for {cfg.provider}: {e}. Run: pip install -r server/requirements.txt")

    # ---------------------------------------------------------------- sessions

    def _session(self, session_id: str) -> _Session:
        session = self._sessions.get(session_id)
        now = time.time()
        if session is None:
            session = _Session()
            self._sessions[session_id] = session
            while len(self._sessions) > MAX_SESSIONS:
                self._sessions.popitem(last=False)
        elif now - session.last_used > SESSION_IDLE_RESET_SEC:
            session.history.clear()
        self._sessions.move_to_end(session_id)
        session.last_used = now
        return session

    def reset_conversation(self, session_id: Optional[str] = None):
        """Clears one session's context, or every session when no id is given."""
        if session_id is None:
            self._sessions.clear()
        else:
            self._sessions.pop(session_id, None)

    def remember_exchange(self, session_id: str, user_text: str, reply: str) -> None:
        """Adds a turn answered outside the LLM (fast path) so follow-ups keep context."""
        session = self._session(session_id)
        session.history.extend([
            {"role": "user", "content": user_text},
            {"role": "assistant", "content": reply},
        ])
        self._trim(session)

    @staticmethod
    def _trim(session: _Session) -> None:
        if len(session.history) <= MAX_HISTORY_MESSAGES:
            return
        trimmed = session.history[-MAX_HISTORY_MESSAGES:]
        # Never start on an orphaned tool message; begin at a user turn.
        while trimmed and trimmed[0].get("role") != "user":
            trimmed.pop(0)
        session.history[:] = trimmed

    # ------------------------------------------------------------------ prompt

    def recent_exchanges(self, session_id: str, limit: int = 6) -> List[Dict[str, str]]:
        """The last `limit` user/assistant exchanges of a session (tool chatter left out)."""
        session = self._sessions.get(session_id)
        if session is None:
            return []
        pairs: List[Dict[str, str]] = []
        pending_user: Optional[str] = None
        for msg in session.history:
            role, content = msg.get("role"), msg.get("content")
            if role == "user":
                pending_user = str(content or "")
            elif role == "assistant" and content and pending_user is not None:
                pairs.append({"user": pending_user, "assistant": str(content)})
                pending_user = None
        return pairs[-max(1, limit):]

    def _system_prompt(self, pc_online: bool, live_context: Optional[str], now_text: Optional[str]) -> str:
        parts = [SERVER_SYSTEM_PROMPT]
        if now_text:
            parts.append(f"CURRENT USER-LOCAL TIME: {now_text}.")
        parts.append(f"WINDOWS PC: {'ONLINE' if pc_online else 'OFFLINE'}.")
        if live_context:
            parts.append(
                "DEVICES (live, refreshed every few seconds - answer status questions from this; "
                "offline devices show since when, why, and their last-known state):\n" + live_context
            )
        return "\n\n".join(parts)

    # -------------------------------------------------------------------- LLM

    def available_models(self) -> List[str]:
        now = time.time()
        return [m for m in self.models if self._cooldown.get(m, 0) <= now]

    def quota_reset_in(self) -> Optional[float]:
        """Seconds until the first rate-limited model can be used again (None if one is free now)."""
        if self.available_models():
            return None
        return max(0.0, min(self._cooldown.values()) - time.time()) if self._cooldown else None

    async def _complete(self, messages: List[Dict[str, Any]], tools: Optional[str], max_tokens: int,
                        tool_defs: Optional[List[Dict[str, Any]]] = None):
        """`tools`: None (no tools), 'auto', or 'none' (tools declared but calls disallowed).
        `tool_defs`: the tool definitions to declare (default: all of them).
        Tries the main model, then each fallback, skipping models that are rate-limited."""
        last_error: Optional[Exception] = None
        for attempt in range(2):
            if not self.available_models() and len(self.models) > 1:
                # Per-minute token limits clear within seconds ("try again in 12.4s"): wait for
                # the first model to free up rather than failing. A daily quota's long wait
                # fails with the provider's own error instead.
                wait = self.quota_reset_in() or 0.0
                if wait <= MAX_QUOTA_WAIT_SEC:
                    await asyncio.sleep(wait)
                elif attempt:
                    break
            for model in self.available_models() or self.models[:1]:
                kwargs: Dict[str, Any] = {
                    "model": model,
                    "messages": messages,
                    "temperature": 0.5,
                    "max_tokens": max_tokens,
                }
                if tools:
                    kwargs["tools"] = tool_defs or PC_TOOLS_DEFINITIONS
                    kwargs["tool_choice"] = tools
                if self.reasoning_effort:
                    kwargs["reasoning_effort"] = self.reasoning_effort
                elif model.startswith("openai/gpt-oss"):
                    kwargs["reasoning_effort"] = "low"  # its default ("medium") adds 10-20 s per request
                try:
                    try:
                        response = await self.client.chat.completions.create(**kwargs)
                    except Exception as e:
                        if self.reasoning_effort and "reasoning" in str(e).lower():
                            print(f"[Server Brain] Model rejected reasoning_effort; disabling it: {e}")
                            self.reasoning_effort = None
                            kwargs.pop("reasoning_effort", None)
                            response = await self.client.chat.completions.create(**kwargs)
                        elif tools and kwargs["tools"] is not PC_TOOLS_DEFINITIONS and "request.tools" in str(e):
                            # The model wanted a tool this request didn't declare: offer all of them.
                            kwargs["tools"] = PC_TOOLS_DEFINITIONS
                            response = await self.client.chat.completions.create(**kwargs)
                        else:
                            raise
                    self.last_model = model
                    return response
                except Exception as e:
                    last_error = e
                    wait = _unavailable_for(e)
                    if wait is None or len(self.models) == 1:
                        raise
                    self._cooldown[model] = time.time() + wait
                    print(f"[Server Brain] {model} unavailable ({str(e)[:120]}); trying the next model.")
        raise last_error or RuntimeError("No language model available.")

    async def complete_text(self, prompt: str, max_tokens: int = 500) -> str:
        """One-off completion without tools or conversation history."""
        if not self.client:
            return ""
        res = await self._complete([{"role": "user", "content": prompt}], tools=None, max_tokens=max_tokens)
        return clean_reply(res.choices[0].message.content)

    async def process_query(
        self,
        user_text: str,
        tool_executor: Optional[ToolExecutor] = None,
        pc_online: bool = False,
        session_id: str = "default",
        live_context: Optional[str] = None,
        now_text: Optional[str] = None,
        source: str = "",
        voice: bool = False,
    ) -> Dict[str, Any]:
        """
        Processes a query through the central LLM brain.
        When the model calls PC tools they run via `tool_executor` (in parallel when the
        model asks for several at once); results are fed back until it produces a reply.
        """
        if not user_text or not user_text.strip():
            return {"success": True, "reply": "How can I help you?", "tool_used": None, "tools": []}

        clean_text = user_text.strip()
        session = self._session(session_id)

        async with session.lock:
            if not self.client:
                reply = f"Server received: '{clean_text}'. Please configure GROQ_API_KEY in server/.env."
                session.history.extend([
                    {"role": "user", "content": clean_text},
                    {"role": "assistant", "content": reply},
                ])
                self._trim(session)
                return {"success": True, "reply": reply, "tool_used": None, "tools": [], "llm_ms": 0}

            system_text = self._system_prompt(pc_online, live_context, now_text)
            if voice:
                system_text += "\n\n" + VOICE_RULES
            system = {"role": "system", "content": system_text}
            turn: List[Dict[str, Any]] = [{"role": "user", "content": clean_text}]
            tool_defs = self._tools_for(session, clean_text, source)
            tools_run: List[Dict[str, Any]] = []
            llm_ms = 0
            tool_ms = 0

            try:
                for round_idx in range(MAX_TOOL_ROUNDS + 1):
                    t0 = time.perf_counter()
                    history, tool_defs = _fit_budget(system, session.history, turn, tool_defs)
                    response = await self._complete(
                        [system] + history + turn,
                        tools="auto" if round_idx < MAX_TOOL_ROUNDS else "none",
                        max_tokens=VOICE_MAX_TOKENS if voice else 600,
                        tool_defs=tool_defs,
                    )
                    llm_ms += round((time.perf_counter() - t0) * 1000)
                    message = response.choices[0].message
                    tool_calls = getattr(message, "tool_calls", None) or []
                    if not tool_calls and round_idx < MAX_TOOL_ROUNDS:
                        # Some models occasionally write the call as text ("<tool_call>
                        # <function=...>") instead of a real call: run it rather than show it.
                        tool_calls = text_tool_calls(message.content or "")

                    if not tool_calls:
                        reply = clean_reply(message.content) or ("Done." if tools_run else "I'm not sure how to help with that.")
                        turn.append({"role": "assistant", "content": reply})
                        break

                    if not tool_executor:
                        # The executor routes each tool to the hub, the phone or the PC and
                        # reports offline devices itself; without one nothing can run.
                        names = ", ".join(tc.function.name for tc in tool_calls)
                        reply = (f"Your devices are offline right now, so I couldn't run {names}. "
                                 "Start the Willy PC client and try again.")
                        turn.append({"role": "assistant", "content": reply})
                        self._commit(session, turn)
                        first = tool_calls[0]
                        return {
                            "success": False,
                            "reply": reply,
                            "tool_used": first.function.name,
                            "tool_args": _parse_args(first.function.arguments),
                            "tools": [],
                            "pc_online": False,
                            "llm_ms": llm_ms,
                        }

                    turn.append({
                        "role": "assistant",
                        "content": None if not getattr(message, "tool_calls", None) else (message.content or None),
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"},
                            }
                            for tc in tool_calls
                        ],
                    })

                    t1 = time.perf_counter()
                    results = await asyncio.gather(*(
                        _run_tool(tool_executor, tc.function.name, _parse_args(tc.function.arguments))
                        for tc in tool_calls
                    ))
                    tool_ms += round((time.perf_counter() - t1) * 1000)

                    for tc, result in zip(tool_calls, results):
                        args = _parse_args(tc.function.arguments)
                        tools_run.append({
                            "name": tc.function.name,
                            "args": args,
                            "success": bool(result.get("success", True)),
                            "result": result,
                        })
                        turn.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "name": tc.function.name,
                            "content": compact_tool_result(result),
                        })

                    # Actions whose results already carry a spoken confirmation ("Text sent to
                    # Mom.", "Reminder set at 7 PM: Stretch.") need no second LLM round: faster,
                    # and it halves the tokens (free-tier quotas are per day).
                    # Multi-part requests ("... and tell me when ...") keep the second round:
                    # models often do the next step only after seeing the first result.
                    # A confirmation question the hub already worded: ask it straight away (no second
                    # AI call, so it works even when the AI quota is used up).
                    asks = [r["ask"] for r in results if isinstance(r, dict) and r.get("error") == "NEEDS_CONFIRMATION" and r.get("ask")]
                    if asks and len(asks) == len(tool_calls):
                        reply = " ".join(asks)
                        turn.append({"role": "assistant", "content": reply})
                        break
                    direct = [r for tc, r in zip(tool_calls, results)
                              if tc.function.name in DIRECT_REPLY_TOOLS and r.get("success", True) is not False
                              and isinstance(r.get("message"), str) and r["message"].strip()]
                    if len(direct) == len(tool_calls) and not _MULTI_STEP.search(clean_text):
                        reply = " ".join(r["message"].strip() for r in direct)
                        turn.append({"role": "assistant", "content": reply})
                        break
                else:
                    reply = "Done."
                    turn.append({"role": "assistant", "content": reply})

                self._commit(session, turn)
                self.last_error = None
                first = tools_run[0] if tools_run else None
                return {
                    "success": all(t["success"] for t in tools_run) if tools_run else True,
                    "reply": reply,
                    "tool_used": first["name"] if first else None,
                    "tool_args": first["args"] if first else None,
                    "tool_result": first["result"] if first else None,
                    "tools": [{"name": t["name"], "args": t["args"], "success": t["success"]} for t in tools_run],
                    "ui": next((t["result"]["ui"] for t in tools_run
                                if isinstance(t["result"], dict) and t["result"].get("ui")), None),
                    "pc_online": pc_online,
                    "llm_ms": llm_ms,
                    "tool_ms": tool_ms,
                }

            except Exception as e:
                err_msg = str(e)
                self.last_error = err_msg[:300]
                print(f"[Server Brain] LLM Error: {err_msg}")
                fallback = friendly_llm_error(err_msg, self.quota_reset_in())
                # Keep the user turn so context survives, but drop half-finished tool chatter.
                session.history.extend([
                    {"role": "user", "content": clean_text},
                    {"role": "assistant", "content": fallback},
                ])
                self._trim(session)
                return {
                    "success": False,
                    "reply": fallback,
                    "error": err_msg,
                    "tools": [{"name": t["name"], "args": t["args"], "success": t["success"]} for t in tools_run],
                    "llm_ms": llm_ms,
                }

    @staticmethod
    def _tools_for(session: _Session, text: str, source: str) -> List[Dict[str, Any]]:
        """Core tools plus the groups this request (or the exchange before it) needs."""
        context: List[str] = []
        used: set = set()
        for msg in session.history[-6:]:
            if msg.get("role") in ("user", "assistant") and msg.get("content"):
                context.append(str(msg["content"]))
        for msg in session.history:
            for call in msg.get("tool_calls") or []:
                used.add(call["function"]["name"])
        return select_tools(text, "\n".join(context[-2:]), source, used)

    def _commit(self, session: _Session, turn: List[Dict[str, Any]]) -> None:
        for msg in turn:
            content = msg.get("content")
            if msg.get("role") == "tool" and isinstance(content, str) and len(content) > HISTORY_TOOL_RESULT_MAX_CHARS:
                # This turn is done; later calls only need the gist of its tool results.
                msg["content"] = content[:HISTORY_TOOL_RESULT_MAX_CHARS] + '... [truncated]'
            elif msg.get("role") == "assistant" and isinstance(content, str) and len(content) > HISTORY_REPLY_MAX_CHARS:
                msg["content"] = content[:HISTORY_REPLY_MAX_CHARS] + "..."
        session.history.extend(turn)
        self._trim(session)


def _estimate_tokens(value: Any) -> int:
    return len(json.dumps(value, default=str)) // 4


def _fit_budget(system: Dict[str, Any], history: List[Dict[str, Any]], turn: List[Dict[str, Any]],
                tool_defs: List[Dict[str, Any]]):
    """Oldest history first, then tools, until the request fits the budget. Tools the user's
    current words point at are kept longest; unrelated everyday tools go before them."""
    from server.pc_tools_schema import ESSENTIAL_TOOLS, select_tools, CORE_TOOLS

    fixed = _estimate_tokens(system) + _estimate_tokens(turn)
    history = list(history)
    over = lambda: fixed + _estimate_tokens(history) + _estimate_tokens(tool_defs) > PROMPT_TOKEN_BUDGET
    while history and over():
        history.pop(0)
        while history and history[0].get("role") != "user":  # never start on a tool reply
            history.pop(0)
    if over():
        text = next((str(m.get("content") or "") for m in turn if m.get("role") == "user"), "")
        asked = {t["function"]["name"] for t in select_tools(text)} - CORE_TOOLS  # groups this question needs
        used = {c["function"]["name"] for m in turn for c in (m.get("tool_calls") or [])}
        keep = asked | ESSENTIAL_TOOLS | used
        tool_defs = [t for t in tool_defs if t["function"]["name"] in keep]
        if over():
            tool_defs = [t for t in tool_defs if t["function"]["name"] in asked | used | {"web_lookup"}]
    return history, tool_defs


_RETRY_IN = re.compile(r"try again in (?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s)?", re.I)
_MULTI_STEP = re.compile(r"\b(?:and|then|also|after that|plus|as well)\b", re.I)


def _unavailable_for(error: Exception) -> Optional[float]:
    """How long to skip a model after this error: rate limits (429, 'try again in 18m21s'),
    retired/unknown models (404) and provider outages; None for errors a fallback won't fix."""
    text = str(error)
    status = getattr(error, "status_code", None)
    lowered = text.lower()
    if status == 429 or "rate_limit" in lowered or "rate limit" in lowered:
        m = _RETRY_IN.search(text)
        if m and any(m.groups()):
            h, mins, secs = (float(g) if g else 0.0 for g in m.groups())
            return h * 3600 + mins * 60 + secs + 5
        return 60.0
    if status == 404 or "model_not_found" in lowered or "decommissioned" in lowered or "does not exist" in lowered:
        return 24 * 3600.0
    if status in (500, 502, 503, 504) or "overloaded" in lowered or "service unavailable" in lowered:
        return 30.0
    return None


def friendly_llm_error(error: str, reset_in: Optional[float] = None) -> str:
    """User-facing text for brain failures (instead of raw provider errors)."""
    lowered = (error or "").lower()
    if "rate limit" in lowered or "rate_limit" in lowered or "429" in lowered:
        when = ""
        if reset_in:
            minutes = max(1, round(reset_in / 60))
            when = f" for about {minutes} minute{'s' if minutes != 1 else ''}" if minutes < 120 else " until later today"
        return (f"My AI quota is used up{when}. Quick commands still work: reminders, timers, "
                "volume, media, lock, open apps, battery and status.")
    if "api key" in lowered or "401" in lowered or "authentication" in lowered:
        return "The AI service rejected Willy's API key. Check GROQ_API_KEY on the server."
    return f"Sorry, the AI service had a problem ({(error or 'unknown error')[:80]}). Please try again."


_KNOWN_TOOLS = {t["function"]["name"] for t in PC_TOOLS_DEFINITIONS}
_TEXT_FUNCTION = re.compile(r"<function=([\w.-]+)>(.*?)(?:</function>|$)", re.S)
_TEXT_PARAMETER = re.compile(r"<parameter=([\w.-]+)>\s*(.*?)\s*(?:</parameter>|(?=<parameter=)|$)", re.S)
_TEXT_JSON_CALL = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)


def text_tool_calls(content: str) -> List[Any]:
    """Tool calls a model wrote into its reply text (Qwen/Hermes XML or JSON styles)."""
    if "<function=" not in content and "<tool_call>" not in content:
        return []
    from types import SimpleNamespace

    found: List[tuple] = []
    for name, body in _TEXT_FUNCTION.findall(content):
        args: Dict[str, Any] = {}
        for key, raw in _TEXT_PARAMETER.findall(body):
            try:
                args[key] = json.loads(raw)
            except ValueError:
                args[key] = raw
        found.append((name, args))
    if not found:
        for raw in _TEXT_JSON_CALL.findall(content):
            try:
                data = json.loads(raw)
                found.append((data.get("name"), data.get("arguments") or data.get("parameters") or {}))
            except (ValueError, AttributeError):
                continue
    return [SimpleNamespace(id=f"text_call_{i}", type="function",
                            function=SimpleNamespace(name=name, arguments=json.dumps(args)))
            for i, (name, args) in enumerate(found) if name in _KNOWN_TOOLS and isinstance(args, dict)]


def _parse_args(raw: Optional[str]) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


async def _run_tool(executor: ToolExecutor, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        result = await executor(name, args)
        return result if isinstance(result, dict) else {"success": True, "result": result}
    except Exception as e:
        return {"success": False, "error": str(e)}
