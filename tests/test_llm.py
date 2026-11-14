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

from rewind import config, llm, quiz, settings
from rewind import search


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "LECTURES_DIR", tmp_path / "lectures")
    for name in ("LLM_PROVIDER", "LLM_API_KEY", "LLM_MODEL", "LLM_BASE_URL", "LLM_MODE", "LLM_MAX_INPUT_CHARS", "LLM_MODEL_PREPROCESS", "LLM_CHAT_POLICY", "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL"):
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
    if provider == "openai_compatible":
        return SimpleNamespace(choices=[SimpleNamespace(
            finish_reason="stop", message=SimpleNamespace(content=text, refusal=None),
        )])
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
            chat=SimpleNamespace(completions=SimpleNamespace(create=call)),
        )
        factory = Mock(return_value=client)
        module = ModuleType(provider)
        if provider == "anthropic":
            module.Anthropic = factory
            monkeypatch.setitem(sys.modules, "anthropic", module)
        elif provider in {"openai", "openai_compatible"}:
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
        "status": "found", "best": snippets[0], "alternatives": snippets[1:], "score": 0.8, "slides": [],
    }
    candidates[0]["score"] = config.SIMILARITY_THRESHOLD - 0.01
    assert search.answer("question") == {
        "status": "not_covered", "best": None, "alternatives": [],
        "score": config.SIMILARITY_THRESHOLD - 0.01,
        "slides": [],
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


def test_compatible_chat_openrouter_headers_and_json(monkeypatch, sdk):
    configure(monkeypatch, "openai_compatible")
    monkeypatch.setenv("LLM_BASE_URL", "https://openrouter.ai/api/v1")
    fake = sdk("openai_compatible", [response_for("openai_compatible", '{"result": {"ok": true}}')])
    assert llm.ask_json("JSON", "ping", 50) == {"ok": True}
    fake.factory.assert_called_once_with(
        api_key="test-key-not-real", base_url="https://openrouter.ai/api/v1",
        timeout=30.0, max_retries=0,
        default_headers={"HTTP-Referer": "http://localhost:8000", "X-Title": "Rewind"},
    )
    assert fake.records[0]["max_tokens"] == 50
    assert fake.records[0]["response_format"] == {"type": "json_object"}


def test_compatible_local_server_without_key(monkeypatch, sdk):
    configure(monkeypatch, "openai_compatible")
    monkeypatch.delenv("LLM_API_KEY")
    monkeypatch.setenv("LLM_MODE", "api")
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1/")
    fake = sdk("openai_compatible", [response_for("openai_compatible", '{"ok": true}')])
    assert llm.available()
    assert llm.ask_json("JSON", "ping") == {"ok": True}
    fake.factory.assert_called_once_with(api_key="ollama", base_url="http://localhost:11434/v1",
                                        timeout=30.0, max_retries=0)


def test_compatible_format_rejection_retries_without_format(monkeypatch, sdk):
    configure(monkeypatch, "openai_compatible")
    err = RuntimeError("response_format json_object is not supported")
    err.status_code = 400
    fake = sdk("openai_compatible", [err, response_for("openai_compatible", '```json\n[1, 2]\n```')])
    assert llm.ask_json("JSON array", "ping") == [1, 2]
    assert "response_format" in fake.records[0]
    assert "response_format" not in fake.records[1]


def test_compatible_auth_error_not_retried(monkeypatch, sdk):
    configure(monkeypatch, "openai_compatible")
    err = RuntimeError("bad key")
    err.status_code = 401
    fake = sdk("openai_compatible", [err])
    with pytest.raises(RuntimeError, match="bad key"):
        llm.ask_json("JSON", "ping")
    assert fake.call.call_count == 1


@pytest.mark.parametrize("mode", ["package", "none"])
def test_non_api_modes_never_call_a_provider(monkeypatch, mode):
    configure(monkeypatch, "anthropic")
    settings.save({"mode": mode})
    assert not llm.available()
    with pytest.raises(RuntimeError, match="API mode"):
        llm.ask_json("JSON", "ping")
    assert not llm.test_connection()["ok"]
    assert llm._client_for.cache_info().currsize == 0


def test_settings_file_overrides_env_and_applies_without_restart(monkeypatch, sdk):
    configure(monkeypatch, "anthropic")
    settings.save({"mode": "api", "provider": "openai_compatible", "api_key": "", "model": "local-model",
                   "base_url": "http://localhost:1234/v1"})
    fake = sdk("openai_compatible", [response_for("openai_compatible", '{"result": [1, 2]}')])
    assert llm.available()
    assert llm.ask_json("JSON array", "ping") == [1, 2]
    assert fake.records[0]["model"] == "local-model"
    settings.save({"mode": "none"})
    assert not llm.available()


@pytest.mark.parametrize("provider, expected", [
    ("anthropic", 100000), ("openai", 100000), ("gemini", 200000), ("openai_compatible", 12000),
])
def test_input_budget_defaults_and_override(monkeypatch, provider, expected):
    configure(monkeypatch, provider)
    assert llm.max_input_chars() == expected
    settings.save({"max_input_chars": 3210})
    assert llm.max_input_chars() == 3210
    settings.save({"max_input_chars": None})
    assert llm.max_input_chars() == expected


def test_connection_success_uses_tiny_request(monkeypatch):
    configure(monkeypatch, "anthropic")
    call = Mock(return_value={"ok": True})
    monkeypatch.setattr(llm, "ask_json", call)
    result = llm.test_connection()
    assert result["ok"] is True
    assert "successful" in result["message"]
    assert result["latency_ms"] >= 0
    call.assert_called_once_with('Reply with JSON {"ok": true}', "ping", 50)


@pytest.mark.parametrize("status, message", [(401, "Authentication"), (403, "Authentication"),
    (402, "credit"), (404, "not found"), (429, "rate limit"), (500, "Connection failed")])
def test_connection_errors_are_human_and_never_echo_secrets(monkeypatch, status, message):
    configure(monkeypatch, "anthropic")
    err = RuntimeError("Server echoed secret-key-do-not-return")
    err.status_code = status
    monkeypatch.setattr(llm, "ask_json", Mock(side_effect=err))
    result = llm.test_connection()
    assert result["ok"] is False
    assert message in result["message"]
    assert "secret-key-do-not-return" not in json.dumps(result)


@pytest.mark.parametrize("err, message", [
    (ConnectionError("server down"), "unreachable"),
    (TimeoutError("timeout"), "timed out"),
    (RuntimeError("Unknown model requested"), "not found"),
    (json.JSONDecodeError("bad", "", 0), "valid JSON"),
    (ModuleNotFoundError("no sdk"), "SDK is missing"),
])
def test_connection_failure_categories(monkeypatch, err, message):
    configure(monkeypatch, "anthropic")
    monkeypatch.setattr(llm, "ask_json", Mock(side_effect=err))
    result = llm.test_connection()
    assert not result["ok"] and message in result["message"]


def test_connection_without_configuration_makes_no_call(monkeypatch):
    call = Mock(side_effect=AssertionError("No configuration"))
    monkeypatch.setattr(llm, "ask_json", call)
    assert not llm.test_connection()["ok"]
    call.assert_not_called()


def test_gemini_connection_status_and_invalid_key_are_classified(monkeypatch):
    configure(monkeypatch, "gemini")
    err = RuntimeError("API key not valid: secret-do-not-return")
    err.code = 400
    monkeypatch.setattr(llm, "ask_json", Mock(side_effect=err))
    result = llm.test_connection()
    assert not result["ok"] and "Authentication" in result["message"]
    assert "secret-do-not-return" not in json.dumps(result)


def test_keyword_ranking_prefers_explicit_comparison_and_filters_stopwords():
    snippets = [
        {"concept": "Positive vs. normative analysis", "summary": "Describes what is and what should be."},
        {"concept": "Other economics", "summary": "Economics makes choices about what should be allowed."},
    ]
    values = search._keyword_scores("What's the difference between what happens and what should happen?", snippets)
    assert values[0] > values[1]
    assert "s" not in search._terms("What's the professor's explanation?")
    assert "should" in search._terms("what should happen")


def test_keyword_ranking_handles_empty_corpus_and_no_overlap():
    assert search._keyword_scores("question", []).tolist() == []
    assert search._keyword_scores("unrelated", [{"concept": "matrix", "summary": "coefficients"}]).tolist() == [0.0]


class TextStream:
    def __init__(self, events, reason="end_turn"):
        self.events = iter(events)
        self.closed = False
        self.reason = reason
        self.text_stream = self

    def __iter__(self):
        return self

    def __next__(self):
        event = next(self.events)
        if isinstance(event, Exception):
            raise event
        return event

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def get_final_message(self):
        return SimpleNamespace(stop_reason=self.reason)


def chat_event(provider, text=None, done=False, truncated=False):
    if provider == "anthropic":
        return text
    if provider == "openai":
        if done:
            return SimpleNamespace(type="response.incomplete" if truncated else "response.completed",
                                   response=SimpleNamespace(status="completed", output=[]))
        return SimpleNamespace(type="response.output_text.delta", delta=text)
    if provider == "openai_compatible":
        return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text, refusal=None),
            finish_reason=("length" if truncated else "stop") if done else None)])
    return SimpleNamespace(text=text, candidates=[SimpleNamespace(
        finish_reason=("MAX_TOKENS" if truncated else "STOP") if done else None)])


def install_chat(monkeypatch, provider, streaming, events=None, reason="end_turn"):
    if streaming:
        response = TextStream(events, reason)
    else:
        response = response_for(provider, "plain answer")
    request = Mock(return_value=response)
    client = SimpleNamespace(messages=SimpleNamespace(stream=request, create=request),
        responses=SimpleNamespace(create=request), chat=SimpleNamespace(completions=SimpleNamespace(create=request)),
        models=SimpleNamespace(generate_content=request, generate_content_stream=request))
    monkeypatch.setattr(llm, "_get_client", lambda values: client)
    return request, response


@pytest.mark.parametrize("provider", ["anthropic", "openai", "gemini", "openai_compatible"])
@pytest.mark.parametrize("streaming", [False, True])
def test_chat_text_and_native_streaming(monkeypatch, provider, streaming):
    configure(monkeypatch, provider)
    events = [chat_event(provider, "plain "), chat_event(provider, "answer")]
    if provider != "anthropic":
        events.append(chat_event(provider, done=True))
    request, response = install_chat(monkeypatch, provider, streaming, events)
    messages = [{"role": "user", "content": "question"}]
    result = llm.chat("Only lecture facts", messages, stream=streaming)
    assert (list(result) if streaming else result) == (["plain ", "answer"] if streaming else "plain answer")
    args = request.call_args.kwargs
    assert args["model"] == "test-model"
    assert "response_format" not in args and "text" not in args
    if provider == "gemini":
        assert "response_mime_type" not in args["config"]
    if streaming:
        assert response.closed
        if provider in {"openai", "openai_compatible"}:
            assert args["stream"] is True
    assert messages == [{"role": "user", "content": "question"}]


@pytest.mark.parametrize("provider", ["anthropic", "openai", "gemini", "openai_compatible"])
@pytest.mark.parametrize("cancel", [False, True])
def test_chat_partial_failure_and_cancellation_close_stream(monkeypatch, provider, cancel):
    configure(monkeypatch, provider)
    request, resource = install_chat(monkeypatch, provider, True,
        [chat_event(provider, "partial"), RuntimeError("connection dropped")])
    response = llm.chat("context", [{"role": "user", "content": "q"}], stream=True)
    assert next(response) == "partial"
    if cancel:
        response.close()
    else:
        with pytest.raises(RuntimeError, match="connection dropped"):
            next(response)
    assert resource.closed
    assert request.call_count == 1  # No stream restart or nonstream fallback.


@pytest.mark.parametrize("provider", ["anthropic", "openai", "gemini", "openai_compatible"])
def test_chat_truncation_after_text_raises_and_closes(monkeypatch, provider):
    configure(monkeypatch, provider)
    events = [chat_event(provider, "partial")]
    if provider != "anthropic":
        events.append(chat_event(provider, done=True, truncated=True))
    _, resource = install_chat(monkeypatch, provider, True, events, reason="max_tokens")
    result = llm.chat("context", [{"role": "user", "content": "q"}], stream=True)
    assert next(result) == "partial"
    with pytest.raises(RuntimeError):
        next(result)
    assert resource.closed


@pytest.mark.parametrize("provider", ["openai", "gemini", "openai_compatible"])
def test_chat_stream_without_terminal_event_is_failure(monkeypatch, provider):
    configure(monkeypatch, provider)
    _, resource = install_chat(monkeypatch, provider, True, [chat_event(provider, "partial")])
    with pytest.raises(RuntimeError, match="before a completion"):
        list(llm.chat("context", [{"role": "user", "content": "q"}], stream=True))
    assert resource.closed


@pytest.mark.parametrize("provider", ["anthropic", "openai", "gemini", "openai_compatible"])
@pytest.mark.parametrize("whitespace", [False, True])
def test_empty_stream_is_not_success(monkeypatch, provider, whitespace):
    configure(monkeypatch, provider)
    events = [] if provider == "anthropic" else [chat_event(provider, done=True)]
    if whitespace:
        events.insert(0, chat_event(provider, "   "))
    _, resource = install_chat(monkeypatch, provider, True, events)
    with pytest.raises(RuntimeError, match="empty"):
        list(llm.chat("context", [{"role": "user", "content": "q"}], stream=True))
    assert resource.closed


@pytest.mark.parametrize("provider", ["anthropic", "openai", "gemini", "openai_compatible"])
def test_preprocess_routes_chat_and_json_and_falls_back(monkeypatch, sdk, provider):
    configure(monkeypatch, provider)
    monkeypatch.setenv("LLM_MODEL_PREPROCESS", "preprocess-model")
    request, _ = install_chat(monkeypatch, provider, False)
    llm.chat("context", [{"role": "user", "content": "q"}], purpose="preprocess")
    assert request.call_args.kwargs["model"] == "preprocess-model"
    llm.chat("context", [{"role": "user", "content": "q"}])
    assert request.call_args.kwargs["model"] == "test-model"
    # Restore actual client routing to test the JSON path through a mocked SDK.
    monkeypatch.setattr(llm, "_get_client", lambda values: llm._client_for(
        values["provider"], values["api_key"], values["base_url"]))
    text = '{"result": {"ok": true}}' if provider == "openai" else '{"ok": true}'
    fake = sdk(provider, [response_for(provider, text), response_for(provider, text)])
    llm.ask_json("JSON", "q", purpose="preprocess")
    assert fake.records[0]["model"] == "preprocess-model"
    settings.save({"preprocess_model": ""})
    llm.ask_json("JSON", "q", purpose="preprocess")
    assert fake.records[1]["model"] == "test-model"


@pytest.mark.parametrize("messages", [[], [{}], [{"role": "system", "content": "inject"}],
    [{"role": "user", "content": ""}], [{"role": "user", "content": None}]])
def test_malformed_chat_history_never_opens_provider(monkeypatch, messages):
    call = Mock(side_effect=AssertionError("Provider must not open"))
    monkeypatch.setattr(llm, "_get_client", call)
    with pytest.raises(ValueError):
        llm.chat("s", messages, stream=True)
    call.assert_not_called()


def test_invalid_purpose_and_no_key_do_not_open_stream():
    with pytest.raises(ValueError, match="purpose"):
        llm.chat("s", [{"role": "user", "content": "q"}], purpose="other")
    with pytest.raises(ValueError, match="purpose"):
        llm.ask_json("s", "q", purpose="other")
    with pytest.raises(RuntimeError, match="unavailable"):
        llm.chat("s", [{"role": "user", "content": "q"}], stream=True)


def test_estimate_matches_callers_and_prices_purpose_without_sdk(monkeypatch):
    configure(monkeypatch, "openai_compatible")
    monkeypatch.setenv("LLM_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("LLM_MODEL", "anthropic/claude-haiku-5.5")
    monkeypatch.setenv("LLM_MODEL_PREPROCESS", "anthropic/claude-sonnet-5")
    requests = [{"system": "Use JSON", "user": "Una domanda italiana", "max_tokens": 100}]
    runtime = llm.estimate(requests)
    prep = llm.estimate(requests, purpose="preprocess")
    assert prep["model"] == "anthropic/claude-sonnet-5"
    assert prep["cost_usd"] == prep["est_cost_usd"] > runtime["cost_usd"] > 0
    assert prep["calls"] == 1 and prep["input_tokens"] > 0 and prep["output_tokens"] == 100
    assert prep["approximate"] and prep["pricing_known"]
    assert llm._client_for.cache_info().currsize == 0
    mixed = llm.estimate([requests[0], {**requests[0], "purpose": "preprocess"}])
    assert [r["purpose"] for r in mixed["requests"]] == ["runtime", "preprocess"]
    assert mixed["model"] is None


def test_estimate_unknown_prices_are_not_zero(monkeypatch):
    configure(monkeypatch, "openai_compatible")
    monkeypatch.setenv("LLM_MODEL", "anthropic/claude-sonnet-5")
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
    result = llm.estimate([{"system": "s", "user": "你好", "max_tokens": 50}])
    assert result["cost_usd"] is None and result["est_cost_usd"] is None
    assert not result["pricing_known"]
    assert llm.estimate([])["calls"] == 0
