"""
Which AI the hub's brain uses, chosen by the person who runs the hub (any provider, any key).

Providers: Groq, Google Gemini, OpenAI, OpenRouter, or "custom" = any OpenAI-compatible
endpoint (Ollama, LM Studio, Together, DeepSeek, Mistral, ...). The choice and the keys are
saved in <data dir>/ai.json (readable only by the hub's user) and override the .env values,
so the owner can change them from the dashboard without touching files. Keys are never sent
back to any screen (only a masked tail).
"""

import asyncio
import json
import os
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from server.config import DATA_DIR, settings

AI_FILE = DATA_DIR / "ai.json"

PROVIDERS: Dict[str, Dict[str, Any]] = {
    "groq": {"label": "Groq (free tier, fast)", "model": settings.GROQ_MODEL, "base_url": None,
             "key_url": "https://console.groq.com/keys", "needs_key": True},
    "gemini": {"label": "Google Gemini", "model": settings.GEMINI_MODEL,
               "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
               "key_url": "https://aistudio.google.com/apikey", "needs_key": True},
    "openai": {"label": "OpenAI", "model": settings.OPENAI_MODEL, "base_url": None,
               "key_url": "https://platform.openai.com/api-keys", "needs_key": True},
    "openrouter": {"label": "OpenRouter (many models)", "model": "openai/gpt-4o-mini",
                   "base_url": "https://openrouter.ai/api/v1", "key_url": "https://openrouter.ai/keys", "needs_key": True},
    "custom": {"label": "Other (OpenAI-compatible, e.g. Ollama)", "model": "", "base_url": "",
               "key_url": "", "needs_key": False},
}

_lock = threading.Lock()


@dataclass(frozen=True)
class AIConfig:
    provider: str
    model: str
    api_key: str
    base_url: Optional[str]

    @property
    def ready(self) -> bool:
        spec = PROVIDERS.get(self.provider, {})
        if self.provider == "custom":
            return bool(self.base_url and self.model)
        return bool(self.api_key) or not spec.get("needs_key", True)


def _env_keys() -> Dict[str, str]:
    return {"groq": settings.GROQ_API_KEY, "gemini": settings.GEMINI_API_KEY, "openai": settings.OPENAI_API_KEY}


def _read() -> Dict[str, Any]:
    try:
        data = json.loads(AI_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def current() -> AIConfig:
    saved = _read()
    provider = str(saved.get("provider") or settings.LLM_PROVIDER or "groq").lower()
    if provider not in PROVIDERS:
        provider = "groq"
    keys = {**{k: v for k, v in _env_keys().items() if v}, **{k: v for k, v in (saved.get("keys") or {}).items() if v}}
    spec = PROVIDERS[provider]
    model = str(saved.get("model") or "") if saved.get("provider") == provider else ""
    base = saved.get("base_url") if saved.get("provider") == provider else None
    return AIConfig(provider=provider, model=model or spec["model"], api_key=keys.get(provider, ""),
                    base_url=(base or spec["base_url"]) or None)


def key_for(provider: str) -> str:
    """Any saved or configured key for this provider (voice transcription uses Groq's or OpenAI's)."""
    saved = (_read().get("keys") or {}).get(provider)
    return saved or _env_keys().get(provider, "")


def save(provider: str, model: str = "", api_key: str = "", base_url: str = "") -> AIConfig:
    provider = provider.lower().strip()
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown AI provider '{provider}'.")
    with _lock:
        data = _read()
        keys = dict(data.get("keys") or {})
        if api_key.strip():
            keys[provider] = api_key.strip()
        data.update(provider=provider, model=model.strip(), base_url=base_url.strip(), keys=keys)
        AI_FILE.parent.mkdir(parents=True, exist_ok=True)
        AI_FILE.write_text(json.dumps(data), encoding="utf-8")
        try:
            AI_FILE.chmod(0o600)
        except OSError:
            pass
    return current()


def save_key(provider: str, api_key: str) -> None:
    """Stores a key for a provider without making it the brain (e.g. a Groq key for voice while Gemini thinks)."""
    provider = provider.lower().strip()
    if provider not in PROVIDERS or not api_key.strip():
        raise ValueError("Enter a key for a known provider.")
    with _lock:
        data = _read()
        keys = dict(data.get("keys") or {})
        keys[provider] = api_key.strip()
        data["keys"] = keys
        AI_FILE.parent.mkdir(parents=True, exist_ok=True)
        AI_FILE.write_text(json.dumps(data), encoding="utf-8")
        try:
            AI_FILE.chmod(0o600)
        except OSError:
            pass


def mask(key: str) -> str:
    return "" if not key else ("…" + key[-4:] if len(key) > 8 else "…")


def public_view() -> Dict[str, Any]:
    cfg = current()
    return {
        "provider": cfg.provider, "model": cfg.model, "base_url": cfg.base_url or "", "key": mask(cfg.api_key),
        "ready": cfg.ready,
        "providers": [{"id": pid, "label": spec["label"], "default_model": spec["model"], "base_url": spec["base_url"] or "",
                       "key_url": spec["key_url"], "needs_key": spec["needs_key"], "key": mask(key_for(pid))}
                      for pid, spec in PROVIDERS.items()],
    }


def make_client(cfg: AIConfig, timeout: float = 30):
    """An async chat client for the config (None when the provider can't be used yet)."""
    if not cfg.ready:
        return None
    if cfg.provider == "groq":
        from groq import AsyncGroq

        # No SDK retries: on a rate limit the SDK would sleep up to a minute before retrying the
        # same model; the orchestrator switches to a fallback model instead.
        return AsyncGroq(api_key=cfg.api_key, max_retries=0, timeout=timeout)
    from openai import AsyncOpenAI

    kwargs: Dict[str, Any] = {"api_key": cfg.api_key or "not-needed", "timeout": timeout}
    if cfg.base_url:
        kwargs["base_url"] = cfg.base_url
    return AsyncOpenAI(**kwargs)


def explain_error(e: Exception, cfg: AIConfig) -> str:
    text = str(e)
    low = text.lower()
    code = getattr(e, "status_code", None)
    if code in (401, 403) or "invalid api key" in low or "incorrect api key" in low or "unauthorized" in low:
        return "That API key was rejected. Check it was copied completely and is for this provider."
    if code == 404 or "model" in low and ("not found" in low or "does not exist" in low):
        return f"The provider doesn't know the model '{cfg.model}'. Pick another model name."
    if code == 429:
        return "The key works but is rate-limited or out of quota right now."
    if "connect" in low or "resolve" in low or "timed out" in low:
        return f"Couldn't reach {cfg.base_url or PROVIDERS[cfg.provider]['label']}. Check the address and your internet."
    return f"The provider answered with an error: {text[:160]}"


async def test(cfg: AIConfig) -> Dict[str, Any]:
    """One tiny request to prove the provider, model and key work."""
    if not cfg.ready:
        return {"ok": False, "error": "Enter an API key (and for 'Other', the address and model name)."}
    client = make_client(cfg, timeout=20)
    try:
        resp = await asyncio.wait_for(client.chat.completions.create(
            model=cfg.model, messages=[{"role": "user", "content": "Reply with the word OK."}], max_tokens=8), 25)
        return {"ok": True, "reply": (resp.choices[0].message.content or "").strip()[:40], "model": cfg.model}
    except Exception as e:  # provider errors vary widely
        return {"ok": False, "error": explain_error(e, cfg)}
    finally:
        try:
            await client.close()
        except Exception:
            pass


def candidates(provider: str, model: str = "", api_key: str = "", base_url: str = "") -> AIConfig:
    """The config that would result from these form values (without saving), reusing a saved key if none typed."""
    provider = provider.lower().strip()
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown AI provider '{provider}'.")
    spec = PROVIDERS[provider]
    return AIConfig(provider=provider, model=model.strip() or spec["model"],
                    api_key=api_key.strip() or key_for(provider), base_url=(base_url.strip() or spec["base_url"]) or None)
