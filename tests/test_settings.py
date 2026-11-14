"""Test settings precedence, partial saves, validation and API-key masking in isolation."""
from __future__ import annotations

import json

import pytest

from rewind import config, settings


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    for name in ("LLM_PROVIDER", "LLM_API_KEY", "LLM_MODEL", "LLM_BASE_URL", "LLM_MODE",
                 "LLM_MAX_INPUT_CHARS", "LLM_MODEL_PREPROCESS", "LLM_CHAT_POLICY", "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL"):
        monkeypatch.delenv(name, raising=False)


def write_raw(values):
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    (config.DATA_DIR / "settings.json").write_text(json.dumps(values), encoding="utf-8")


def test_defaults_need_no_key_and_create_no_file():
    assert settings.load() == {
        "mode": "none", "provider": "anthropic", "api_key": "", "model": "", "base_url": "",
        "max_input_chars": None,
        "chat_policy": "strict", "preprocess_model": "",
    }
    assert not config.DATA_DIR.exists()


def test_json_over_env_over_defaults(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("LLM_MODEL", "env-model")
    monkeypatch.setenv("LLM_API_KEY", "env-key")
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1/")
    write_raw({"mode": "package", "model": "file-model", "api_key": "file-key"})
    value = settings.load()
    assert value["mode"] == "package"
    assert value["model"] == "file-model"
    assert value["api_key"] == "file-key"
    assert value["provider"] == "openai_compatible"
    assert value["base_url"] == "http://localhost:11434/v1"


def test_infer_api_mode_for_environment_credentials(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "key")
    monkeypatch.setenv("LLM_MODEL", "model")
    assert settings.load()["mode"] == "api"
    monkeypatch.setenv("LLM_MODE", "none")
    assert settings.load()["mode"] == "none"


def test_infer_api_for_keyless_local_and_file_credentials(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("LLM_MODEL", "local")
    assert settings.load()["mode"] == "none"
    monkeypatch.setenv("LLM_MODE", "api")
    assert settings.load()["mode"] == "api"
    write_raw({"provider": "openai", "api_key": "file-key", "model": "file-model", "base_url": ""})
    assert settings.load()["mode"] == "api"


def test_legacy_anthropic_fallback_not_used_for_saved_other_provider(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "legacy-key")
    monkeypatch.setenv("ANTHROPIC_MODEL", "legacy-model")
    assert settings.load()["api_key"] == "legacy-key"
    write_raw({"provider": "gemini"})
    assert settings.load()["api_key"] == ""
    assert settings.load()["model"] == ""


def test_atomic_partial_save_keeps_omitted_key_and_explicit_empty_clears():
    assert settings.save({"mode": "api", "provider": "openai", "model": "model",
                          "api_key": "sk-very-long-test-key-a1b2"}) is None
    settings.save({"model": "new-model"})
    assert settings.load()["api_key"] == "sk-very-long-test-key-a1b2"
    assert settings.load()["model"] == "new-model"
    assert not list(config.DATA_DIR.glob(".settings-*.tmp"))
    settings.save({"api_key": ""})
    assert settings.load()["api_key"] == ""


@pytest.mark.parametrize("key", ["", "x", "short", "12345678", "sk-very-long-key-a1b2"])
def test_public_never_exposes_full_key(key):
    settings.save({"api_key": key})
    exposed = settings.public()
    if key:
        assert exposed["api_key"] != key
        assert key not in exposed["api_key"]
        if len(key) > 8:
            assert key not in json.dumps(exposed, ensure_ascii=False)
    else:
        assert exposed["api_key"] == ""
    if len(key) > 8:
        assert exposed["api_key"] == "sk-…a1b2"
    assert settings.load()["api_key"] == key


def test_masked_key_cannot_overwrite_secret():
    settings.save({"api_key": "sk-very-long-key-a1b2"})
    with pytest.raises(ValueError, match="real api_key"):
        settings.save({"api_key": settings.public()["api_key"]})
    assert settings.load()["api_key"] == "sk-very-long-key-a1b2"


@pytest.mark.parametrize("update", [
    {"mode": "bad"}, {"provider": "bad"}, {"provider": None}, {"model": 10},
    {"api_key": None}, {"base_url": "file:///etc/passwd"},
    {"base_url": "https://user:secret@example.com/v1"},
    {"base_url": "https://example.com/v1?api_key=secret"},
    {"base_url": "http://localhost:999999/v1"},
    {"max_input_chars": 0}, {"max_input_chars": True}, {"max_input_chars": "1000"},
    {"unexpected": "field"}, [],
])
def test_invalid_settings_rejected_without_overwriting(update):
    settings.save({"model": "keep"})
    before = (config.DATA_DIR / "settings.json").read_bytes()
    with pytest.raises(ValueError):
        settings.save(update)
    assert (config.DATA_DIR / "settings.json").read_bytes() == before


def test_env_input_limit_and_file_override(monkeypatch):
    monkeypatch.setenv("LLM_MAX_INPUT_CHARS", "5000")
    assert settings.load()["max_input_chars"] == 5000
    settings.save({"max_input_chars": 7000})
    monkeypatch.setenv("LLM_MAX_INPUT_CHARS", "invalid")
    assert settings.load()["max_input_chars"] == 7000


def test_corrupt_file_error_does_not_echo_contents():
    config.DATA_DIR.mkdir(parents=True)
    (config.DATA_DIR / "settings.json").write_text('{"api_key": "secret-value', encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON") as err:
        settings.load()
    assert "secret-value" not in str(err.value)


def test_saved_provider_overrides_invalid_environment(monkeypatch):
    write_raw({"provider": "gemini", "mode": "none"})
    monkeypatch.setenv("LLM_PROVIDER", "invalid")
    assert settings.load()["provider"] == "gemini"


@pytest.mark.parametrize("provider, url, preset", [
    ("openai_compatible", "https://openrouter.ai/api/v1", "openrouter"),
    ("openai_compatible", "http://localhost:11434/v1", "ollama"),
    ("openai_compatible", "http://127.0.0.1:1234/v1", "lmstudio"),
    ("openai_compatible", "http://university.example/v1", None),
    ("openai", "https://openrouter.ai/api/v1", None),
])
def test_public_presets_are_derived_without_exposing_a_key(provider, url, preset):
    settings.save({"provider": provider, "base_url": url, "api_key": "sk-private-test-value-a1b2"})
    assert settings.public()["preset"] == preset
    assert "preset" not in settings.load()  # Derived only, not a persisted input.


def test_chat_policy_and_preprocess_precedence(monkeypatch):
    monkeypatch.setenv("LLM_CHAT_POLICY", "labelled")
    monkeypatch.setenv("LLM_MODEL_PREPROCESS", "env-preprocess")
    assert settings.load()["chat_policy"] == "labelled"
    assert settings.load()["preprocess_model"] == "env-preprocess"
    settings.save({"chat_policy": "strict", "preprocess_model": "file-preprocess"})
    assert settings.public()["chat_policy"] == "strict"
    assert settings.public()["preprocess_model"] == "file-preprocess"
    settings.save({"preprocess_model": ""})
    assert settings.load()["preprocess_model"] == ""


@pytest.mark.parametrize("update", [{"chat_policy": "external"}, {"chat_policy": None}, {"preprocess_model": 8}])
def test_invalid_chat_settings_preserve_file(update):
    settings.save({"model": "keep"})
    before = (config.DATA_DIR / "settings.json").read_bytes()
    with pytest.raises(ValueError):
        settings.save(update)
    assert (config.DATA_DIR / "settings.json").read_bytes() == before


def test_blank_environment_chat_policy_uses_strict_default(monkeypatch):
    monkeypatch.setenv("LLM_CHAT_POLICY", "   ")
    assert settings.load()["chat_policy"] == "strict"
