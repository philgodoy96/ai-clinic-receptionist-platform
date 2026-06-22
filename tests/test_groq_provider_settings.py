from collections.abc import Generator

import pytest
from pydantic import ValidationError

from app.ai.llm_provider import GroqResponseFormat, LLMProviderName
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


def test_fake_default_does_not_require_groq_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_MODEL", raising=False)
    monkeypatch.delenv("LLM_PRIMARY_PROVIDER", raising=False)

    settings = load_settings(monkeypatch)

    assert settings.llm_provider == LLMProviderName.FAKE
    assert settings.groq_api_key == ""
    assert settings.groq_model == ""


def test_llm_provider_groq_requires_api_key_and_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValidationError, match="GROQ_API_KEY"):
        load_settings(monkeypatch, LLM_PROVIDER="groq")


def test_llm_primary_provider_groq_requires_api_key_and_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValidationError, match="GROQ_API_KEY"):
        load_settings(monkeypatch, LLM_PRIMARY_PROVIDER="groq")

    with pytest.raises(ValidationError, match="GROQ_MODEL"):
        load_settings(
            monkeypatch,
            LLM_PRIMARY_PROVIDER="groq",
            GROQ_API_KEY="gsk_test",
        )


def test_llm_primary_provider_groq_accepts_api_key_and_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = load_settings(
        monkeypatch,
        LLM_PRIMARY_PROVIDER="groq",
        GROQ_API_KEY="gsk_test",
        GROQ_MODEL="llama-3.3-70b-versatile",
    )
    assert settings.resolved_llm_primary_provider == LLMProviderName.GROQ
    assert settings.groq_api_key == "gsk_test"
    assert settings.groq_model == "llama-3.3-70b-versatile"


def test_groq_fallback_requires_api_key_and_model_when_fallback_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValidationError, match="GROQ_API_KEY"):
        load_settings(
            monkeypatch,
            LLM_FALLBACK_ENABLED="true",
            LLM_FALLBACK_PROVIDER="groq",
        )

    with pytest.raises(ValidationError, match="GROQ_MODEL"):
        load_settings(
            monkeypatch,
            LLM_FALLBACK_ENABLED="true",
            LLM_FALLBACK_PROVIDER="groq",
            GROQ_API_KEY="gsk_test",
        )


def test_groq_fallback_accepts_api_key_and_model_when_fallback_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = load_settings(
        monkeypatch,
        LLM_FALLBACK_ENABLED="true",
        LLM_FALLBACK_PROVIDER="groq",
        GROQ_API_KEY="gsk_test",
        GROQ_MODEL="llama-3.3-70b-versatile",
    )
    assert settings.llm_fallback_provider == LLMProviderName.GROQ


@pytest.mark.parametrize("invalid_format", ["json", "text", ""])
def test_groq_invalid_response_format_rejected(
    monkeypatch: pytest.MonkeyPatch,
    invalid_format: str,
) -> None:
    with pytest.raises(ValidationError):
        load_settings(monkeypatch, GROQ_RESPONSE_FORMAT=invalid_format)


def test_groq_response_format_accepts_valid_values(monkeypatch: pytest.MonkeyPatch) -> None:
    for response_format in GroqResponseFormat:
        settings = load_settings(monkeypatch, GROQ_RESPONSE_FORMAT=response_format.value)
        assert settings.groq_response_format == response_format


@pytest.mark.parametrize(
    ("env_key", "invalid_value"),
    [
        ("GROQ_REQUEST_TIMEOUT_SECONDS", "0"),
        ("GROQ_MAX_OUTPUT_TOKENS", "0"),
    ],
)
def test_groq_invalid_timeout_and_token_limits_rejected(
    monkeypatch: pytest.MonkeyPatch,
    env_key: str,
    invalid_value: str,
) -> None:
    with pytest.raises(ValidationError):
        load_settings(monkeypatch, **{env_key: invalid_value})
