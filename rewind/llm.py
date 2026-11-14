"""Provider-independent JSON requests using lazy official SDK imports.

ask_json(system, user, max_tokens) -> parsed JSON (dict or list).
rewind.settings selects Anthropic, OpenAI, Gemini or an OpenAI-compatible server.
available() checks configuration without SDK imports. Invalid JSON is retried once.
"""
from __future__ import annotations

import json
import logging
import math
import re
import time
from functools import lru_cache
from typing import Any
from urllib.parse import urlsplit

from rewind import config, settings

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*\n?(.*?)\n?\s*```\s*$", re.DOTALL)


def strip_fences(text: str) -> str:
    """Remove a surrounding ```json ... ``` fence if present."""
    m = _FENCE_RE.match(text)
    return m.group(1) if m else text.strip()


def parse_json(text: str) -> Any:
    """Parse model output as JSON, tolerating code fences and leading/trailing prose."""
    cleaned = strip_fences(text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Fall back to the outermost [...] or {...} block.
        starts = [i for i in (cleaned.find("["), cleaned.find("{")) if i != -1]
        if not starts:
            raise
        start = min(starts)
        end = max(cleaned.rfind("]"), cleaned.rfind("}"))
        if end <= start:
            raise
        return json.loads(cleaned[start : end + 1])


def available() -> bool:
    """Whether API mode has a model and key (or a keyless compatible endpoint)."""
    return _available(settings.load())


def _available(values: dict) -> bool:
    return bool(values["mode"] == "api" and values["model"] and (
        values["api_key"] or (values["provider"] == "openai_compatible" and values["base_url"])
    ))


def max_input_chars() -> int:
    """Conservative transcript budget, overridable by max_input_chars in settings."""
    values = settings.load()
    defaults = {"anthropic": 100_000, "openai": 100_000, "gemini": 200_000,
                "openai_compatible": 12_000}
    return values["max_input_chars"] or defaults[values["provider"]]


@lru_cache(maxsize=3)
def _client_for(provider: str, api_key: str, base_url: str = ""):
    if provider == "anthropic":
        import anthropic

        return anthropic.Anthropic(api_key=api_key)
    if provider in {"openai", "openai_compatible"}:
        from openai import OpenAI

        if provider == "openai_compatible":
            options = {"api_key": api_key or "ollama", "timeout": 30.0, "max_retries": 0}
            if base_url:
                options["base_url"] = base_url
            if "openrouter.ai" in base_url.lower():
                options["default_headers"] = {
                    "HTTP-Referer": "http://localhost:8000", "X-Title": "Rewind",
                }
            return OpenAI(**options)
        return OpenAI(api_key=api_key)
    from google import genai

    return genai.Client(api_key=api_key)


def _get_client(values: dict | None = None):
    values = settings.load() if values is None else values
    return _client_for(values["provider"], values["api_key"], values["base_url"])


def _complete(system: str, messages: list[dict], max_tokens: int, values: dict | None = None) -> str:
    """One provider request; return text or raise on refusal/truncation/API errors."""
    values = settings.load() if values is None else values
    provider = values["provider"]
    client = _get_client(values)
    model = values["model"]
    if provider == "openai_compatible":
        request = {
            "model": model, "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system + '\nReturn JSON as an object '
                          'with exactly one key, "result". Put the requested JSON value under "result".'}] + messages,
            "response_format": {"type": "json_object"},
        }
        try:
            response = client.chat.completions.create(**request)
        except Exception as err:
            # Retry only a format capability rejection, not auth/rate/network errors.
            detail = str(err).lower()
            if getattr(err, "status_code", None) not in {400, 404, 422} or not any(
                word in detail for word in ("response_format", "json_object", "json mode", "json schema")
            ):
                raise
            request.pop("response_format")
            response = client.chat.completions.create(**request)
        if not response.choices:
            raise RuntimeError("Compatible server returned no choices.")
        choice = response.choices[0]
        if choice.finish_reason not in {"stop", None}:
            raise RuntimeError("Compatible server response was incomplete or blocked.")
        if getattr(choice.message, "refusal", None) or not choice.message.content:
            raise RuntimeError("Compatible server declined the request or returned no text.")
        return choice.message.content
    if provider == "openai":
        # JSON mode requires an object; the envelope also supports callers that
        # request a top-level array (notably the snippet maker).
        response = client.responses.create(
            model=model,
            instructions=system + '\nReturn JSON as an object with exactly one key, "result". '
            'Put the requested JSON value (object, array, or scalar) under "result".',
            input=messages,
            max_output_tokens=max_tokens,
            text={"format": {"type": "json_object"}},
            store=False,
        )
        if response.status != "completed":
            raise RuntimeError(f"OpenAI response did not complete (status={response.status}).")
        for output in response.output:
            for part in getattr(output, "content", []):
                if part.type == "refusal":
                    raise RuntimeError("OpenAI declined the JSON request.")
        if not response.output_text:
            raise RuntimeError("OpenAI returned no text.")
        return response.output_text
    if provider == "gemini":
        response = client.models.generate_content(
            model=model,
            contents=[
                {"role": "model" if m["role"] == "assistant" else "user",
                 "parts": [{"text": m["content"]}]}
                for m in messages
            ],
            config={
                "system_instruction": system + "\nReturn only JSON.",
                "response_mime_type": "application/json",
                "max_output_tokens": max_tokens,
            },
        )
        for candidate in response.candidates or []:
            reason = candidate.finish_reason
            name = getattr(reason, "name", str(reason).rsplit(".", 1)[-1])
            if reason is not None and name != "STOP":
                raise RuntimeError(f"Gemini response did not complete (finish_reason={name}).")
        if not response.text:
            raise RuntimeError("Gemini returned no text (possibly blocked).")
        return response.text
    with client.messages.stream(
        model=model,
        max_tokens=max_tokens,
        system=system + "\nReturn only JSON.",
        messages=messages,
    ) as stream:
        message = stream.get_final_message()
    if message.stop_reason == "refusal":
        raise RuntimeError("The model declined the request (stop_reason=refusal).")
    text = "".join(b.text for b in message.content if b.type == "text")
    if message.stop_reason == "max_tokens":
        raise RuntimeError(
            f"Model output hit max_tokens={max_tokens} before finishing; raise max_tokens "
            "or send a shorter input."
        )
    return text


def _request_settings(purpose: str) -> dict:
    if purpose not in {"runtime", "preprocess"}:
        raise ValueError("purpose must be runtime or preprocess.")
    values = settings.load()
    if purpose == "preprocess":
        values["model"] = values.get("preprocess_model") or values["model"]
    return values


def ask_json(system: str, user: str, max_tokens: int = 16000, purpose: str = "runtime") -> Any:
    """Ask the model for JSON and return the parsed value. Retries once on invalid JSON."""
    values = _request_settings(purpose)
    if not _available(values):
        raise RuntimeError(
            "LLM is not configured for API mode. Choose API mode and a model in AI settings, "
            "then supply a key or a compatible server URL. .env settings are also supported."
        )
    provider = values["provider"]
    messages: list[dict] = [{"role": "user", "content": user}]
    for attempt in range(2):
        text = _complete(system, messages, max_tokens, values)
        try:
            result = parse_json(text)
            if provider == "openai":
                if not isinstance(result, dict) or set(result) != {"result"}:
                    raise json.JSONDecodeError("Expected an object with only the result key", text, 0)
                return result["result"]
            if provider == "openai_compatible" and isinstance(result, dict) and set(result) == {"result"}:
                return result["result"]
            return result
        except json.JSONDecodeError as err:
            if attempt:
                raise
            messages += [
                {"role": "assistant", "content": text},
                {
                    "role": "user",
                    "content": f"Invalid JSON response ({err}). Reply again with only the "
                    "corrected JSON, no prose and no code fences.",
                },
            ]


def _text(value) -> str:
    if not isinstance(value, str):
        raise RuntimeError("The provider returned a malformed text response.")
    return value


def _anthropic_done(message) -> None:
    if message.stop_reason not in {"end_turn", "stop_sequence"}:
        raise RuntimeError(f"Anthropic did not complete the text response ({message.stop_reason}).")


def _openai_done(response) -> None:
    if response.status != "completed":
        raise RuntimeError(f"OpenAI did not complete the text response ({response.status}).")
    for item in response.output:
        if any(part.type == "refusal" for part in getattr(item, "content", [])):
            raise RuntimeError("OpenAI declined the text request.")


def _gemini_done(response) -> bool:
    feedback = getattr(response, "prompt_feedback", None)
    block = getattr(feedback, "block_reason", None)
    if block and getattr(block, "name", str(block)) not in {"BLOCK_REASON_UNSPECIFIED", "0"}:
        raise RuntimeError("Gemini blocked the text request.")
    complete = False
    for candidate in response.candidates or []:
        reason = candidate.finish_reason
        if reason is not None:
            name = getattr(reason, "name", str(reason).rsplit(".", 1)[-1])
            if name != "STOP":
                raise RuntimeError(f"Gemini did not complete the text response ({name}).")
            complete = True
    return complete


def _gemini_messages(messages: list[dict]) -> list[dict]:
    return [{"role": "model" if m["role"] == "assistant" else "user",
             "parts": [{"text": m["content"]}]} for m in messages]


def _chat_text(values: dict, system: str, messages: list[dict], max_tokens: int) -> str:
    client, provider, model = _get_client(values), values["provider"], values["model"]
    if provider == "anthropic":
        message = client.messages.create(model=model, system=system, messages=messages, max_tokens=max_tokens)
        _anthropic_done(message)
        text = "".join(_text(b.text) for b in message.content if b.type == "text")
    elif provider == "openai":
        response = client.responses.create(model=model, instructions=system, input=messages,
                                           max_output_tokens=max_tokens, store=False)
        _openai_done(response)
        text = _text(response.output_text)
    elif provider == "openai_compatible":
        response = client.chat.completions.create(
            model=model, messages=[{"role": "system", "content": system}] + messages, max_tokens=max_tokens,
        )
        if not response.choices or response.choices[0].finish_reason != "stop":
            raise RuntimeError("Compatible server did not complete the text response.")
        message = response.choices[0].message
        if getattr(message, "refusal", None):
            raise RuntimeError("Compatible server declined the text request.")
        text = _text(message.content)
    else:
        response = client.models.generate_content(model=model, contents=_gemini_messages(messages),
            config={"system_instruction": system, "max_output_tokens": max_tokens})
        if not _gemini_done(response):
            raise RuntimeError("Gemini returned no completion status.")
        text = _text(response.text)
    if not text.strip():
        raise RuntimeError("The provider returned no text.")
    return text


def _chat_stream(values: dict, system: str, messages: list[dict], max_tokens: int):
    client, provider, model = _get_client(values), values["provider"], values["model"]
    emitted, complete = False, False
    if provider == "anthropic":
        with client.messages.stream(model=model, system=system, messages=messages, max_tokens=max_tokens) as stream:
            for chunk in stream.text_stream:
                text = _text(chunk)
                if text:
                    emitted = emitted or bool(text.strip())
                    yield text
            _anthropic_done(stream.get_final_message())
            if not emitted:
                raise RuntimeError("Anthropic returned an empty text stream.")
        return
    if provider == "openai":
        stream = client.responses.create(model=model, instructions=system, input=messages,
                                         max_output_tokens=max_tokens, store=False, stream=True)
    elif provider == "openai_compatible":
        stream = client.chat.completions.create(model=model, max_tokens=max_tokens, stream=True,
            messages=[{"role": "system", "content": system}] + messages)
    else:
        stream = client.models.generate_content_stream(model=model, contents=_gemini_messages(messages),
            config={"system_instruction": system, "max_output_tokens": max_tokens})
    try:
        for event in stream:
            text = ""
            if provider == "openai":
                kind = getattr(event, "type", None)
                if not isinstance(kind, str):
                    raise RuntimeError("OpenAI returned a malformed stream event.")
                if kind in {"error", "response.failed", "response.incomplete",
                            "response.refusal.delta", "response.refusal.done"}:
                    raise RuntimeError("OpenAI text stream failed, was refused, or was truncated.")
                if kind == "response.completed":
                    _openai_done(event.response)
                    complete = True
                elif kind == "response.output_text.delta":
                    if complete:
                        raise RuntimeError("OpenAI sent text after stream completion.")
                    text = _text(event.delta)
            elif provider == "openai_compatible":
                for choice in event.choices:
                    if getattr(choice.delta, "refusal", None):
                        raise RuntimeError("Compatible server declined the streamed request.")
                    if choice.finish_reason is not None:
                        if choice.finish_reason != "stop":
                            raise RuntimeError("Compatible text stream was blocked or truncated.")
                        complete = True
                    content = choice.delta.content
                    text += _text(content) if content is not None else ""
            else:
                complete = _gemini_done(event) or complete
                if event.text is not None:
                    text = _text(event.text)
            if text:
                emitted = emitted or bool(text.strip())
                yield text
        if not complete:
            raise RuntimeError("Provider stream ended before a completion event.")
        if not emitted:
            raise RuntimeError("Provider returned an empty text stream.")
    finally:
        close = getattr(stream, "close", None)
        if callable(close):
            close()


def chat(system, messages, max_tokens=800, stream=False, purpose="runtime"):
    """Return plain text or an iterator of native text deltas; close iterators on cancellation.

    Provider failures and truncations raise, including after partial deltas. No
    success/done marker is fabricated here; the calling chat layer owns its events.
    """
    if not isinstance(system, str) or not isinstance(messages, list) or not messages:
        raise ValueError("chat needs system text and a nonempty message list.")
    if type(max_tokens) is not int or max_tokens <= 0 or type(stream) is not bool:
        raise ValueError("max_tokens must be positive and stream must be a boolean.")
    clean = []
    for message in messages:
        if (not isinstance(message, dict) or message.get("role") not in {"user", "assistant"}
            or not isinstance(message.get("content"), str) or not message["content"].strip()):
            raise ValueError("chat messages must contain user/assistant roles and nonempty text.")
        clean.append({"role": message["role"], "content": message["content"]})
    values = _request_settings(purpose)
    if not _available(values):
        raise RuntimeError("Chat is unavailable: configure a model and API credentials in API mode.")
    return _chat_stream(values, system, clean, max_tokens) if stream else _chat_text(values, system, clean, max_tokens)


# Standard OpenRouter rates verified from these official model pages, 2026-11-14.
# Custom endpoints/native providers are deliberately not assigned these prices.
_OPENROUTER_PRICES = {
    "anthropic/claude-sonnet-5": (2.0, 10.0, "https://openrouter.ai/anthropic/claude-sonnet-5"),
    "anthropic/claude-haiku-5.5": (0.10, 0.50, "https://openrouter.ai/anthropic/claude-haiku-5.5"),
}


def estimate(requests, purpose="runtime") -> dict:
    """Approximate tokens/cost for [{system, user, max_tokens, purpose?}], offline.

    UTF-8 bytes/3 plus 64 framing tokens approximate input; output uses the cap.
    This excludes retries, tools, caching discounts and unsupplied verification
    calls. Unknown prices yield null cost, never a misleading zero-dollar claim.
    """
    defaults = _request_settings(purpose)
    if not isinstance(requests, list):
        raise ValueError("requests must be a list of request dictionaries.")
    values = settings.load()
    openrouter = (values["provider"] == "openai_compatible"
                  and urlsplit(values["base_url"]).hostname == "openrouter.ai")
    details = []
    for request in requests:
        if (not isinstance(request, dict) or not isinstance(request.get("system"), str)
            or not isinstance(request.get("user"), str) or type(request.get("max_tokens")) is not int
            or request["max_tokens"] <= 0):
            raise ValueError("Each estimate request needs system, user and a positive max_tokens.")
        selected_purpose = request.get("purpose", purpose)
        if selected_purpose not in {"runtime", "preprocess"}:
            raise ValueError("purpose must be runtime or preprocess.")
        model = (values.get("preprocess_model") or values["model"]) if selected_purpose == "preprocess" else values["model"]
        tokens = math.ceil(len((request["system"] + request["user"]).encode("utf-8")) / 3) + 64
        rate = _OPENROUTER_PRICES.get(model) if openrouter else None
        cost = (tokens * rate[0] + request["max_tokens"] * rate[1]) / 1_000_000 if rate else None
        details.append({"model": model, "purpose": selected_purpose, "input_tokens": tokens,
                        "output_tokens": request["max_tokens"], "cost_usd": cost,
                        "pricing_known": rate is not None, "price_source": rate[2] if rate else None})
    known = all(item["pricing_known"] for item in details)
    cost = round(sum(item["cost_usd"] for item in details), 8) if known else None
    models = sorted({item["model"] for item in details})
    return {"calls": len(details), "input_tokens": sum(item["input_tokens"] for item in details),
            "output_tokens": sum(item["output_tokens"] for item in details),
            "cost_usd": cost, "est_cost_usd": cost, "currency": "USD", "approximate": True,
            "pricing_known": known, "pricing_checked_at": "2026-11-14" if openrouter and known and details else None,
            "model": models[0] if len(models) == 1 else (defaults["model"] if not models else None),
            "purpose": purpose, "requests": details}


def test_connection() -> dict:
    """Send a tiny JSON probe; return {ok, message, latency_ms} without exposing secrets."""
    started = time.perf_counter()
    ok = False
    try:
        values = settings.load()
        if values["mode"] != "api":
            message = "Select API mode to test a connection."
        elif not values["model"]:
            message = "Choose a model before testing the connection."
        elif not _available(values):
            message = "Enter an API key, or a base URL for a keyless OpenAI-compatible server."
        else:
            result = ask_json('Reply with JSON {"ok": true}', "ping", 50)
            ok = isinstance(result, dict) and result.get("ok") is True
            message = "Connection successful. The model returned valid JSON." if ok else (
                "The server responded, but the model did not return the requested JSON."
            )
    except Exception as err:
        # Never return raw SDK exception text: it can echo request headers/keys.
        status = getattr(err, "status_code", None)
        if status is None and type(getattr(err, "code", None)) is int:
            status = err.code  # Google Gen AI APIError uses code for HTTP status.
        code = str(getattr(err, "code", "")).lower()
        detail = str(err).lower()
        if status in {401, 403} or any(
            phrase in detail for phrase in ("api key not valid", "invalid api key", "invalid api_key")
        ):
            message = "Authentication failed. Check the API key and model access."
        elif status == 402:
            message = "The provider has insufficient credit. Check its billing settings."
        elif status == 404 or code in {"model_not_found", "invalid_model"} or (
            "model" in detail and any(
                phrase in detail for phrase in ("not found", "does not exist", "unknown model", "invalid model")
            )
        ):
            message = "Model or endpoint not found. Check the model ID and base URL."
        elif status == 429:
            message = "The provider rate limit was reached. Try again shortly."
        elif isinstance(err, ImportError):
            message = "The provider SDK is missing. Install the packages in requirements.txt."
        elif isinstance(err, json.JSONDecodeError):
            message = "The model responded, but did not return valid JSON. Try another model."
        elif "connection" in type(err).__name__.lower() or "timeout" in type(err).__name__.lower():
            message = "Server unreachable or timed out. Check the base URL and whether the server is running."
        elif isinstance(err, (ValueError, OSError)) and status is None:
            message = "AI settings could not be loaded. Check their fields and the settings file."
        else:
            message = "Connection failed. Check the key, model, base URL and provider availability."
    return {"ok": ok, "message": message, "latency_ms": round((time.perf_counter() - started) * 1000, 2)}


# Import is the startup boundary. Repeated searches/classifications stay quiet.
try:
    _startup_available = available()
except (ValueError, OSError):
    _startup_available = False
if not _startup_available:
    logging.getLogger(__name__).warning(
        "LLM is not configured: using similarity-only search and keyword-only homework "
        "detection. Set LLM_PROVIDER, LLM_API_KEY and LLM_MODEL in .env to enable LLM checks."
    )
