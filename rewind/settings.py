"""Load/save local AI settings; expose a browser-safe copy with a masked API key.

Precedence is data/settings.json > environment/.env > defaults. save() accepts
partial settings, preserving an omitted key; an explicit empty key clears it.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from rewind import config

_FIELDS = frozenset({"mode", "provider", "model", "api_key", "base_url", "max_input_chars",
                     "chat_policy", "preprocess_model"})
_PROVIDERS = frozenset({"anthropic", "openai", "gemini", "openai_compatible"})


def _path() -> Path:
    return config.DATA_DIR / "settings.json"


def _validate(values: dict) -> dict:
    if not isinstance(values, dict) or set(values) - _FIELDS:
        raise ValueError("AI settings must be an object containing supported setting fields only.")
    result = dict(values)
    for name in ("mode", "provider", "model", "api_key", "base_url", "chat_policy", "preprocess_model"):
        if not isinstance(result[name], str):
            raise ValueError(f"AI setting {name} must be text.")
        result[name] = result[name].strip()
    result["mode"] = result["mode"].lower()
    result["provider"] = result["provider"].lower()
    result["chat_policy"] = result["chat_policy"].lower()
    result["base_url"] = result["base_url"].rstrip("/")
    if result["mode"] not in {"api", "package", "none"}:
        raise ValueError("AI setting mode must be api, package, or none.")
    if result["provider"] not in _PROVIDERS:
        raise ValueError("AI setting provider must be anthropic, openai, gemini, or openai_compatible.")
    if result["chat_policy"] not in {"strict", "labelled"}:
        raise ValueError("AI setting chat_policy must be strict or labelled.")
    if result["base_url"]:
        try:
            url = urlsplit(result["base_url"])
            _ = url.port  # Also validate malformed/out-of-range ports.
            valid = url.scheme in {"http", "https"} and url.hostname and not (
                url.username or url.password or url.query or url.fragment
            )
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("AI setting base_url must be an HTTP(S) URL without credentials, query, or fragment.")
    limit = result["max_input_chars"]
    if limit is not None and (type(limit) is not int or limit <= 0):
        raise ValueError("AI setting max_input_chars must be a positive integer or null for automatic.")
    if "…" in result["api_key"] or result["api_key"] == "••••":
        raise ValueError("Supply a real api_key or omit api_key to keep the current key.")
    return result


def load() -> dict:
    """Return effective settings including the private key; do not send this to a browser."""
    saved = {}
    path = _path()
    if path.exists():
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, UnicodeError):
            raise ValueError("data/settings.json is not valid JSON.") from None
        if not isinstance(saved, dict) or set(saved) - _FIELDS:
            raise ValueError("data/settings.json must contain supported setting fields only.")
    provider = saved["provider"] if "provider" in saved else config.get_provider()
    if not isinstance(provider, str):
        raise ValueError("AI setting provider must be text.")
    provider = provider.strip().lower()
    key, model, base_url = config.get_api_key(provider), config.get_model(provider), config.get_base_url()
    configured = bool(model and key)
    raw_limit = os.environ.get("LLM_MAX_INPUT_CHARS", "").strip()
    try:
        limit = saved.get("max_input_chars") if "max_input_chars" in saved else (int(raw_limit) if raw_limit else None)
    except ValueError:
        raise ValueError("LLM_MAX_INPUT_CHARS must be a positive integer.") from None
    result = {
        "mode": os.environ.get("LLM_MODE", "").strip() or ("api" if configured else "none"),
        "provider": provider, "model": model, "api_key": key, "base_url": base_url,
        "max_input_chars": limit,
        "chat_policy": os.environ.get("LLM_CHAT_POLICY", "").strip() or "strict",
        "preprocess_model": os.environ.get("LLM_MODEL_PREPROCESS", ""),
    }
    result.update(saved)
    if "mode" not in saved and not os.environ.get("LLM_MODE", "").strip():
        result["mode"] = "api" if result["model"] and result["api_key"] else "none"
    return _validate(result)


def save(values: dict):
    """Validate and atomically persist a partial update. Return None; never return the key."""
    if not isinstance(values, dict) or set(values) - _FIELDS:
        raise ValueError("AI settings must be an object containing supported setting fields only.")
    current = load()
    updated = {**current, **values}
    # An omitted key explicitly means keep current, as required by the HTTP contract.
    updated = _validate(updated)
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".settings-", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(updated, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def public() -> dict:
    """Return effective settings with the key masked, including for short custom keys."""
    result = load()
    key = result["api_key"]
    result["api_key"] = (key[:3] + "…" + key[-4:]) if len(key) > 8 else ("••••" if key else "")
    result["preset"] = None
    if result["provider"] == "openai_compatible" and result["base_url"]:
        url = urlsplit(result["base_url"])
        if "openrouter.ai" in result["base_url"].lower():
            result["preset"] = "openrouter"
        elif url.port == 11434 or "ollama" in (url.hostname or ""):
            result["preset"] = "ollama"
        elif url.port == 1234 or "lmstudio" in (url.hostname or ""):
            result["preset"] = "lmstudio"
    return result
