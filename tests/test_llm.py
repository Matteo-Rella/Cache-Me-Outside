"""Mock official SDKs to verify provider routing, JSON retries and no-key fallback."""
from __future__ import annotations

import copy
import importlib
import json
import logging
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest

from rewind import config, llm, quiz
from rewind import search


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch):
    for name in ("LLM_PROVIDER", "LLM_API_KEY", "LLM_MODEL", "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL"):
        monkeypatch.delenv(name, raising=False)
    llm._client_for.cache_clear()
    yield
    llm._client_for.cache_clear()


def configure(monkeypatch, provider):
    monkeypatch.setenv("LLM_PROVIDER", provider)
    monkeypatch.setenv("LLM_API_KEY", "test-key-not-real")
    monkeypatch.setenv("LLM_MODEL", "test-model")


def response_for(provider, text):
    if provider == "anthropic":
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=text)])
    if provider == "openai":
        return SimpleNamespace(status="completed", output=[], output_text=text)
    return SimpleNamespace(candidates=[SimpleNamespace(finish_reason="STOP")], text=text)


@pytest.fixture
def sdk(monkeypatch):
    def install(provider, responses):
        records = []
        responses = iter(responses)

        def request(**kwargs):
            records.append(copy.deepcopy(kwargs))
            response = next(responses)
            if isinstance(response, Exception):
                raise response
            if provider != "anthropic":
                return response
            stream = Mock()
            stream.__enter__ = Mock(return_value=stream)
            stream.__exit__ = Mock(return_value=False)
            stream.get_final_message.return_value = response
            return stream

        call = Mock(side_effect=request)
        client = SimpleNamespace(
            messages=SimpleNamespace(stream=call),
            responses=SimpleNamespace(create=call),
            models=SimpleNamespace(generate_content=call),
        )
        factory = Mock(return_value=client)
        module = ModuleType(provider)
        if provider == "anthropic":
            module.Anthropic = factory
            monkeypatch.setitem(sys.modules, "anthropic", module)
        elif provider == "openai":
            module.OpenAI = factory
            monkeypatch.setitem(sys.modules, "openai", module)
        else:
            module.Client = factory
            google = ModuleType("google")
            google.genai = module
            monkeypatch.setitem(sys.modules, "google", google)
            monkeypatch.setitem(sys.modules, "google.genai", module)
        return SimpleNamespace(factory=factory, call=call, records=records)

    return install


@pytest.mark.parametrize("provider", ["anthropic", "openai", "gemini"])
@pytest.mark.parametrize("value", [{"ok": True}, [{"id": "demo01-001"}], None])
def test_provider_json_and_cached_client(monkeypatch, sdk, provider, value):
    configure(monkeypatch, provider)
    text = json.dumps({"result": value} if provider == "openai" else value)
    fake = sdk(provider, [response_for(provider, text), response_for(provider, text)])
    assert llm.available()
    assert llm.ask_json("Extract data", "Una domanda", 123) == value
    assert llm.ask_json("Extract data", "Another question", 123) == value
    fake.factory.assert_called_once_with(api_key="test-key-not-real")
    request = fake.records[0]
    assert request["model"] == "test-model"
    if provider == "anthropic":
        assert request["max_tokens"] == 123
        assert "JSON" in request["system"]
        assert request["messages"] == [{"role": "user", "content": "Una domanda"}]
    elif provider == "openai":
        assert request["max_output_tokens"] == 123
        assert request["text"] == {"format": {"type": "json_object"}}
        assert '"result"' in request["instructions"]
        assert request["store"] is False
        assert request["input"] == [{"role": "user", "content": "Una domanda"}]
    else:
        assert request["config"]["max_output_tokens"] == 123
        assert request["config"]["response_mime_type"] == "application/json"
        assert "Extract data" in request["config"]["system_instruction"]
        assert request["contents"] == [{"role": "user", "parts": [{"text": "Una domanda"}]}]


@pytest.mark.parametrize("provider", ["anthropic", "openai", "gemini"])
def test_invalid_json_retried_with_conversation(monkeypatch, sdk, provider):
    configure(monkeypatch, provider)
    good = '{"result": {"ok": true}}' if provider == "openai" else '{"ok": true}'
    fake = sdk(provider, [response_for(provider, "not JSON"), response_for(provider, good)])
    assert llm.ask_json("JSON", "question") == {"ok": True}
    assert len(fake.records) == 2
    if provider == "gemini":
        messages = fake.records[1]["contents"]
        assert [m["role"] for m in messages] == ["user", "model", "user"]
        assert messages[1]["parts"] == [{"text": "not JSON"}]
        assert "Invalid JSON" in messages[2]["parts"][0]["text"]
    else:
        messages = fake.records[1]["input" if provider == "openai" else "messages"]
        assert [m["role"] for m in messages] == ["user", "assistant", "user"]
        assert messages[1]["content"] == "not JSON"
        assert "Invalid JSON" in messages[2]["content"]


@pytest.mark.parametrize("provider", ["anthropic", "openai", "gemini"])
def test_second_invalid_json_raises(monkeypatch, sdk, provider):
    configure(monkeypatch, provider)
    fake = sdk(provider, [response_for(provider, "bad"), response_for(provider, "still bad")])
    with pytest.raises(json.JSONDecodeError):
        llm.ask_json("JSON", "question")
    assert fake.call.call_count == 2


def test_openai_requires_envelope(monkeypatch, sdk):
    configure(monkeypatch, "openai")
    fake = sdk("openai", [response_for("openai", '{"wrong": true}'),
                          response_for("openai", '{"result": []}')])
    assert llm.ask_json("Return an array", "question") == []
    assert fake.call.call_count == 2


@pytest.mark.parametrize("provider", ["anthropic", "openai", "gemini"])
def test_truncation_raises_without_json_retry(monkeypatch, sdk, provider):
    configure(monkeypatch, provider)
    response = response_for(provider, '{"ok": true}')
    if provider == "anthropic":
        response.stop_reason = "max_tokens"
    elif provider == "openai":
        response.status = "incomplete"
    else:
        response.candidates[0].finish_reason = SimpleNamespace(name="MAX_TOKENS")
    fake = sdk(provider, [response])
    with pytest.raises(RuntimeError, match="max_tokens|did not complete"):
        llm.ask_json("JSON", "question")
    assert fake.call.call_count == 1


@pytest.mark.parametrize("provider", ["anthropic", "openai", "gemini"])
def test_refusal_or_block_raises(monkeypatch, sdk, provider):
    configure(monkeypatch, provider)
    response = response_for(provider, "")
    if provider == "anthropic":
        response.stop_reason = "refusal"
    elif provider == "openai":
        response.output = [SimpleNamespace(content=[SimpleNamespace(type="refusal")])]
    else:
        response.candidates[0].finish_reason = "SAFETY"
    sdk(provider, [response])
    with pytest.raises(RuntimeError, match="declined|did not complete"):
        llm.ask_json("JSON", "question")


@pytest.mark.parametrize("provider", ["anthropic", "openai", "gemini"])
def test_sdk_errors_propagate_without_json_retry(monkeypatch, sdk, provider):
    configure(monkeypatch, provider)
    fake = sdk(provider, [RuntimeError("API unavailable")])
    with pytest.raises(RuntimeError, match="API unavailable"):
        llm.ask_json("JSON", "question")
    assert fake.call.call_count == 1


def test_legacy_settings_and_generic_precedence(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "legacy-key")
    monkeypatch.setenv("ANTHROPIC_MODEL", "legacy-model")
    assert config.get_provider() == "anthropic"
    assert config.get_api_key() == "legacy-key"
    assert config.get_model() == "legacy-model"
    assert llm.available()
    monkeypatch.setenv("LLM_API_KEY", "generic-key")
    monkeypatch.setenv("LLM_MODEL", "generic-model")
    assert config.get_api_key() == "generic-key"
    assert config.get_model() == "generic-model"


@pytest.mark.parametrize("provider", ["openai", "gemini"])
def test_legacy_anthropic_credentials_never_sent_to_other_providers(monkeypatch, provider):
    monkeypatch.setenv("LLM_PROVIDER", provider)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "legacy-key")
    monkeypatch.setenv("ANTHROPIC_MODEL", "legacy-model")
    assert config.get_api_key() == ""
    assert config.get_model() == ""
    assert not llm.available()


def test_settings_whitespace_and_invalid_provider(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", " OpenAI ")
    monkeypatch.setenv("LLM_API_KEY", " key ")
    monkeypatch.setenv("LLM_MODEL", " model ")
    assert config.get_provider() == "openai"
    assert config.get_api_key() == "key"
    assert config.get_model() == "model"
    monkeypatch.setenv("LLM_PROVIDER", "unknown")
    with pytest.raises(ValueError, match="LLM_PROVIDER"):
        llm.available()


@pytest.mark.parametrize("missing", ["LLM_API_KEY", "LLM_MODEL"])
def test_partial_settings_unavailable(monkeypatch, missing):
    configure(monkeypatch, "anthropic")
    monkeypatch.delenv(missing)
    assert not llm.available()


def test_no_key_imports_no_sdk_and_ask_json_errors_clearly(monkeypatch):
    for name in ("anthropic", "openai", "google", "google.genai"):
        monkeypatch.setitem(sys.modules, name, None)
    assert config.get_model() == ""
    assert not llm.available()
    with pytest.raises(RuntimeError, match="LLM is not configured"):
        llm.ask_json("JSON", "question")
    assert llm._client_for.cache_info().currsize == 0


def test_selected_provider_does_not_import_other_sdks(monkeypatch, sdk):
    configure(monkeypatch, "anthropic")
    monkeypatch.setitem(sys.modules, "openai", None)
    monkeypatch.setitem(sys.modules, "google.genai", None)
    sdk("anthropic", [response_for("anthropic", '{"ok": true}')])
    assert llm.ask_json("JSON", "question") == {"ok": True}


def test_no_key_search_keeps_plain_snippets_and_threshold(monkeypatch):
    snippets = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    candidates = [{"snippet": s, "score": score} for s, score in zip(snippets, [0.8, 0.7, 0.6])]
    monkeypatch.setattr(search, "search", lambda *a, **kw: candidates)
    call = Mock(side_effect=AssertionError("No-key search called LLM"))
    monkeypatch.setattr(search, "ask_json", call)
    assert search.answer("question") == {
        "status": "found", "best": snippets[0], "alternatives": snippets[1:], "score": 0.8,
    }
    candidates[0]["score"] = config.SIMILARITY_THRESHOLD - 0.01
    assert search.answer("question") == {
        "status": "not_covered", "best": None, "alternatives": [],
        "score": config.SIMILARITY_THRESHOLD - 0.01,
    }
    candidates.clear()
    assert search.answer("question")["best"] is None
    call.assert_not_called()


def test_no_key_homework_uses_only_keywords(monkeypatch):
    call = Mock(side_effect=AssertionError("No-key classification called LLM"))
    monkeypatch.setattr(llm, "ask_json", call)
    assert quiz.is_homework("Solve this system")
    assert quiz.is_homework("calcola il determinante")
    assert quiz.is_homework("1) determine x")
    assert not quiz.is_homework("What is the row picture?")
    call.assert_not_called()


def test_one_startup_warning_and_no_per_call_warnings(monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger="rewind.llm")
    importlib.reload(llm)
    assert sum("LLM is not configured" in r.message for r in caplog.records) == 1
    caplog.clear()
    monkeypatch.setattr(search, "search", lambda *a, **kw: [{"snippet": {"id": "a"}, "score": 0.8}])
    for _ in range(3):
        assert not llm.available()
        assert search.answer("question")["status"] == "found"
        assert not quiz.is_homework("What is a matrix?")
    assert not caplog.records


def test_parse_json_fences_and_prose():
    assert llm.parse_json('```json\n[{"ok": true}]\n```') == [{"ok": True}]
    assert llm.parse_json('Here is the data: {"ok": true}') == {"ok": True}
