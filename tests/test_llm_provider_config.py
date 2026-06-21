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
