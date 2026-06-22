from collections.abc import Generator

import pytest
from pydantic import ValidationError

from app.ai.llm_provider import LLMProviderName
from app.core.config import Settings, get_settings


@pytest.fixture(autouse=True)
def clear_settings_cache() -> Generator[None, None, None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def load_settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)


def test_default_llm_provider_is_fake(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    settings = load_settings(monkeypatch)
    assert settings.llm_provider == LLMProviderName.FAKE


def test_llm_enabled_defaults_to_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_ENABLED", raising=False)
    settings = load_settings(monkeypatch)
    assert settings.llm_enabled is True


def test_bedrock_provider_requires_model_id(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError, match="BEDROCK_MODEL_ID"):
        load_settings(monkeypatch, LLM_PROVIDER="bedrock")


def test_bedrock_provider_accepts_model_id(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_settings(
        monkeypatch,
        LLM_PROVIDER="bedrock",
        BEDROCK_MODEL_ID="anthropic.claude-3-haiku-20240307-v1:0",
    )
    assert settings.llm_provider == LLMProviderName.BEDROCK
    assert settings.bedrock_model_id == "anthropic.claude-3-haiku-20240307-v1:0"


def test_fake_provider_does_not_require_bedrock_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = load_settings(monkeypatch, LLM_PROVIDER="fake")
    assert settings.llm_provider == LLMProviderName.FAKE
    assert settings.bedrock_model_id == ""


def test_llm_max_primary_attempts_defaults_to_two(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_MAX_PRIMARY_ATTEMPTS", raising=False)
    settings = load_settings(monkeypatch)
    assert settings.llm_max_primary_attempts == 2


def test_llm_max_primary_attempts_rejects_values_below_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValidationError):
        load_settings(monkeypatch, LLM_MAX_PRIMARY_ATTEMPTS="0")


def test_llm_max_primary_attempts_rejects_values_above_three(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValidationError):
        load_settings(monkeypatch, LLM_MAX_PRIMARY_ATTEMPTS="4")


def test_llm_fallback_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_FALLBACK_ENABLED", raising=False)
    settings = load_settings(monkeypatch)
    assert settings.llm_fallback_enabled is False
    assert settings.llm_fallback_provider is None


def test_llm_fallback_enabled_requires_fallback_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValidationError, match="LLM_FALLBACK_PROVIDER"):
        load_settings(monkeypatch, LLM_FALLBACK_ENABLED="true")


def test_llm_primary_provider_overrides_llm_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_settings(
        monkeypatch,
        LLM_PROVIDER="fake",
        LLM_PRIMARY_PROVIDER="bedrock",
        BEDROCK_MODEL_ID="anthropic.claude-3-haiku-20240307-v1:0",
    )
    assert settings.resolved_llm_primary_provider == LLMProviderName.BEDROCK


def test_llm_max_fallback_attempts_rejects_values_above_two(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValidationError):
        load_settings(
            monkeypatch,
            LLM_FALLBACK_ENABLED="true",
            LLM_FALLBACK_PROVIDER="fake",
            LLM_MAX_FALLBACK_ATTEMPTS="3",
        )
