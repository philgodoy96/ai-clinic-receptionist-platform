from __future__ import annotations

from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from app.ai.bedrock_llm_provider import BedrockLLMProvider
from app.ai.fake_llm_provider import FakeLLMProvider
from app.ai.groq_provider import GroqLLMProvider
from app.ai.llm_provider import LLMProviderName
from app.ai.provider_factory import (
    build_llm_provider,
    create_llm_provider_from_settings,
)
from app.api.dependencies import get_llm_provider, get_llm_receptionist_analysis_service
from app.core.config import Settings, get_settings
from app.services.llm_receptionist import LLMReceptionistAnalysisService


@pytest.fixture(autouse=True)
def clear_settings_cache() -> Generator[None, None, None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def load_settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)


def test_default_settings_use_fake_llm_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_settings(monkeypatch)

    provider = get_llm_provider(settings=settings)

    assert isinstance(provider, FakeLLMProvider)


def test_build_llm_provider_bedrock_uses_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_settings(
        monkeypatch,
        LLM_PROVIDER="bedrock",
        BEDROCK_MODEL_ID="anthropic.claude-3-haiku-20240307-v1:0",
        AWS_REGION="eu-west-1",
        BEDROCK_REQUEST_TIMEOUT_SECONDS="15",
        BEDROCK_MAX_RETRIES="1",
        BEDROCK_TEMPERATURE="0.1",
        BEDROCK_MAX_TOKENS="512",
    )
    stub_client = MagicMock()

    with patch(
        "app.ai.bedrock_llm_provider.BedrockLLMProvider._create_client",
        return_value=stub_client,
    ) as create_client:
        provider = build_llm_provider(settings)

    create_client.assert_called_once()
    assert isinstance(provider, BedrockLLMProvider)
    assert provider._client is stub_client
    assert provider._model_id == "anthropic.claude-3-haiku-20240307-v1:0"
    assert provider._region_name == "eu-west-1"
    assert provider._timeout_seconds == 15
    assert provider._max_retries == 1
    assert provider._default_temperature == 0.1
    assert provider._default_max_tokens == 512


def test_bedrock_provider_created_only_when_selected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = load_settings(
        monkeypatch,
        LLM_PROVIDER="bedrock",
        BEDROCK_MODEL_ID="anthropic.claude-3-haiku-20240307-v1:0",
    )
    stub_client = MagicMock()

    with patch(
        "app.ai.bedrock_llm_provider.BedrockLLMProvider._create_client",
        return_value=stub_client,
    ) as create_client:
        provider = build_llm_provider(settings)

    create_client.assert_called_once()
    assert isinstance(provider, BedrockLLMProvider)
    assert provider._client is stub_client


def test_fake_provider_does_not_create_bedrock_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = load_settings(monkeypatch)

    with patch(
        "app.ai.bedrock_llm_provider.BedrockLLMProvider._create_client",
    ) as create_client:
        provider = build_llm_provider(settings)

    create_client.assert_not_called()
    assert isinstance(provider, FakeLLMProvider)


def test_missing_bedrock_model_id_raises_config_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValidationError, match="BEDROCK_MODEL_ID"):
        load_settings(monkeypatch, LLM_PROVIDER="bedrock")


def test_get_llm_receptionist_analysis_service_returns_none_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = load_settings(monkeypatch, LLM_ENABLED="false")

    service = get_llm_receptionist_analysis_service(settings=settings)

    assert service is None


def test_get_llm_receptionist_analysis_service_uses_fake_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = load_settings(monkeypatch)

    service = get_llm_receptionist_analysis_service(settings=settings)

    assert isinstance(service, LLMReceptionistAnalysisService)
    assert isinstance(service.primary_provider, FakeLLMProvider)


def test_llm_disabled_does_not_create_bedrock_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = load_settings(
        monkeypatch,
        LLM_ENABLED="false",
        LLM_PROVIDER="bedrock",
        BEDROCK_MODEL_ID="anthropic.claude-3-haiku-20240307-v1:0",
    )

    with patch(
        "app.ai.bedrock_llm_provider.BedrockLLMProvider._create_client",
    ) as create_client:
        service = get_llm_receptionist_analysis_service(settings=settings)

    assert service is None
    create_client.assert_not_called()


def test_build_llm_provider_logs_provider_name_without_secrets(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    caplog.set_level(logging.INFO, logger="app.llm_provider")
    settings = load_settings(monkeypatch)

    build_llm_provider(settings)

    provider_records = [
        record for record in caplog.records if record.getMessage() == "llm_provider_selected"
    ]
    assert len(provider_records) == 1
    assert getattr(provider_records[0], "provider", None) == "fake"
    assert "BEDROCK_MODEL_ID" not in caplog.text


def test_create_llm_provider_from_settings_uses_explicit_provider_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = load_settings(
        monkeypatch,
        LLM_PROVIDER="fake",
        BEDROCK_MODEL_ID="anthropic.claude-3-haiku-20240307-v1:0",
    )
    stub_client = MagicMock()

    with patch(
        "app.ai.bedrock_llm_provider.BedrockLLMProvider._create_client",
        return_value=stub_client,
    ) as create_client:
        provider = create_llm_provider_from_settings(settings, LLMProviderName.BEDROCK)

    create_client.assert_called_once()
    assert isinstance(provider, BedrockLLMProvider)
    assert provider._model_id == "anthropic.claude-3-haiku-20240307-v1:0"


def test_fallback_enabled_instantiates_primary_and_fallback_providers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = load_settings(
        monkeypatch,
        LLM_PROVIDER="fake",
        LLM_FALLBACK_ENABLED="true",
        LLM_FALLBACK_PROVIDER="fake",
    )

    with patch(
        "app.ai.provider_factory.create_llm_provider_from_settings",
        wraps=create_llm_provider_from_settings,
    ) as create_provider:
        service = get_llm_receptionist_analysis_service(settings=settings)

    assert service is not None
    assert create_provider.call_count == 2
    assert service.fallback_provider is not None


def test_build_llm_provider_groq_uses_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_settings(
        monkeypatch,
        LLM_PRIMARY_PROVIDER="groq",
        GROQ_API_KEY="gsk_test",
        GROQ_MODEL="llama-3.3-70b-versatile",
        GROQ_REQUEST_TIMEOUT_SECONDS="15",
        GROQ_MAX_OUTPUT_TOKENS="512",
        GROQ_TEMPERATURE="0.1",
    )

    provider = build_llm_provider(settings)

    assert isinstance(provider, GroqLLMProvider)
    assert provider._model == "llama-3.3-70b-versatile"
    assert provider._api_key == "gsk_test"
    assert provider._timeout_seconds == 15
    assert provider._default_max_output_tokens == 512
    assert provider._default_temperature == 0.1


def test_groq_provider_created_only_when_selected(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_settings(
        monkeypatch,
        LLM_PRIMARY_PROVIDER="groq",
        GROQ_API_KEY="gsk_test",
        GROQ_MODEL="llama-3.3-70b-versatile",
    )

    provider = build_llm_provider(settings)

    assert isinstance(provider, GroqLLMProvider)


def test_fake_provider_does_not_create_groq_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = load_settings(monkeypatch)

    with patch("app.ai.groq_provider.GroqLLMProvider") as groq_cls:
        provider = build_llm_provider(settings)

    groq_cls.assert_not_called()
    assert isinstance(provider, FakeLLMProvider)


def test_llm_primary_provider_groq_creates_groq_for_receptionist_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = load_settings(
        monkeypatch,
        LLM_PRIMARY_PROVIDER="groq",
        GROQ_API_KEY="gsk_test",
        GROQ_MODEL="llama-3.3-70b-versatile",
    )

    service = get_llm_receptionist_analysis_service(settings=settings)

    assert service is not None
    assert isinstance(service.primary_provider, GroqLLMProvider)
    assert service.primary_provider_name == LLMProviderName.GROQ
    assert service.fallback_provider is None


def test_invalid_llm_provider_name_raises_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValidationError):
        load_settings(monkeypatch, LLM_PROVIDER="openai")
