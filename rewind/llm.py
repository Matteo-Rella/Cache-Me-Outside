"""Provider-independent JSON requests using lazy official SDK imports.

ask_json(system, user, max_tokens) -> parsed JSON (dict or list).
LLM_PROVIDER selects Anthropic, OpenAI or Gemini. available() checks configuration
without importing SDKs. Invalid JSON is retried once with the parse error.
"""
from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from typing import Any

from rewind import config

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
    """Whether a supported provider has a key and model, without a network call."""
    config.get_provider()
    return bool(config.get_api_key() and config.get_model())


@lru_cache(maxsize=3)
def _client_for(provider: str, api_key: str):
    if provider == "anthropic":
        import anthropic

        return anthropic.Anthropic(api_key=api_key)
    if provider == "openai":
        from openai import OpenAI

        return OpenAI(api_key=api_key)
    from google import genai

    return genai.Client(api_key=api_key)


def _get_client():
    return _client_for(config.get_provider(), config.get_api_key())


def _complete(system: str, messages: list[dict], max_tokens: int) -> str:
    """One provider request; return text or raise on refusal/truncation/API errors."""
    provider = config.get_provider()
    client = _get_client()
    model = config.get_model()
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


def ask_json(system: str, user: str, max_tokens: int = 16000) -> Any:
    """Ask the model for JSON and return the parsed value. Retries once on invalid JSON."""
    if not available():
        raise RuntimeError(
            "LLM is not configured. Set LLM_API_KEY and LLM_MODEL in .env "
            "(or ANTHROPIC_API_KEY and ANTHROPIC_MODEL for Anthropic)."
        )
    provider = config.get_provider()
    messages: list[dict] = [{"role": "user", "content": user}]
    for attempt in range(2):
        text = _complete(system, messages, max_tokens)
        try:
            result = parse_json(text)
            if provider == "openai":
                if not isinstance(result, dict) or set(result) != {"result"}:
                    raise json.JSONDecodeError("Expected an object with only the result key", text, 0)
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


# Import is the startup boundary. Repeated searches/classifications stay quiet.
if not available():
    logging.getLogger(__name__).warning(
        "LLM is not configured: using similarity-only search and keyword-only homework "
        "detection. Set LLM_PROVIDER, LLM_API_KEY and LLM_MODEL in .env to enable LLM checks."
    )
