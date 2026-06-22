from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from app.ai.bedrock_llm_provider import BedrockLLMProvider
from app.ai.fake_llm_provider import FakeLLMProvider
from app.ai.groq_provider import GroqLLMProvider
from app.ai.llm_provider import LLMProviderName
from app.ai.llm_reliability import LLMFailureReason
from app.api.dependencies import get_llm_receptionist_analysis_service
from tests.llm_provider_test_helpers import build_receptionist_analysis_payload
from tests.llm_reliability_test_helpers import (
    AlwaysFailingLLMProvider,
    CountingLLMProvider,
    analysis_request,
    assert_result_reliability_metadata,
    build_orchestration_service,
)
from tests.test_llm_provider_config import load_settings


def test_fallback_disabled_primary_exhaustion_uses_deterministic_fallback() -> None:
    primary = AlwaysFailingLLMProvider()
    fallback = CountingLLMProvider(
        content=build_receptionist_analysis_payload(),
        name="fallback",
    )
    service = build_orchestration_service(
        primary_provider=primary,
        fallback_provider=fallback,
        max_primary_attempts=1,
        max_fallback_attempts=0,
    )

    result = service.analyze_message(analysis_request())

    assert fallback.call_count == 0
    assert result.used_fallback is True
    assert result.used_fallback_provider is False
    assert result.fallback_attempt_count == 0
    assert_result_reliability_metadata(result)


def test_fallback_enabled_primary_failure_then_fallback_success() -> None:
    primary = AlwaysFailingLLMProvider()
    fallback = CountingLLMProvider(
        content=build_receptionist_analysis_payload(),
        name="fallback",
    )
    service = build_orchestration_service(
        primary_provider=primary,
        fallback_provider=fallback,
        primary_provider_name=LLMProviderName.FAKE,
        fallback_provider_name=LLMProviderName.BEDROCK,
        max_primary_attempts=1,
        max_fallback_attempts=1,
    )

    result = service.analyze_message(analysis_request())

    assert primary.call_count == 1
    assert fallback.call_count == 1
    assert result.used_fallback is False
    assert result.used_fallback_provider is True
    assert result.provider == LLMProviderName.BEDROCK.value
    assert result.primary_attempt_count == 1
    assert result.fallback_attempt_count == 1
    assert result.attempt_count == 2
    assert_result_reliability_metadata(result)


def test_fallback_enabled_primary_exhausted_with_retryable_failure_calls_fallback_once() -> None:
    primary = AlwaysFailingLLMProvider()
    fallback = CountingLLMProvider(
        content=build_receptionist_analysis_payload(),
        name="fallback",
    )
    service = build_orchestration_service(
        primary_provider=primary,
        fallback_provider=fallback,
        primary_provider_name=LLMProviderName.FAKE,
        fallback_provider_name=LLMProviderName.BEDROCK,
        max_primary_attempts=2,
        max_fallback_attempts=1,
    )

    result = service.analyze_message(analysis_request())

    assert primary.call_count == 2
    assert fallback.call_count == 1
    assert result.used_fallback_provider is True
    assert_result_reliability_metadata(result)


def test_fallback_provider_exception_leads_to_deterministic_fallback() -> None:
    primary = AlwaysFailingLLMProvider()
    fallback = AlwaysFailingLLMProvider(message="simulated fallback provider failure")
    service = build_orchestration_service(
        primary_provider=primary,
        fallback_provider=fallback,
        max_primary_attempts=1,
        max_fallback_attempts=1,
        fallback_provider_name=LLMProviderName.BEDROCK,
    )

    result = service.analyze_message(analysis_request())

    assert fallback.call_count == 1
    assert result.used_fallback is True
    assert result.used_fallback_provider is False
    assert result.fallback_attempt_count == 1
    assert result.failure_reason == LLMFailureReason.PROVIDER_EXCEPTION
    assert_result_reliability_metadata(result)


def test_fallback_safety_violation_does_not_retry_fallback() -> None:
    payload = build_receptionist_analysis_payload(
        intent="greeting",
        urgency="emergency",
    )
    primary = AlwaysFailingLLMProvider()
    fallback = CountingLLMProvider(content=payload, name="fallback")
    service = build_orchestration_service(
        primary_provider=primary,
        fallback_provider=fallback,
        max_primary_attempts=1,
        max_fallback_attempts=2,
        fallback_provider_name=LLMProviderName.BEDROCK,
    )

    result = service.analyze_message(analysis_request())

    assert fallback.call_count == 1
    assert result.used_fallback is True
    assert result.used_fallback_provider is False
    assert result.failure_reason == LLMFailureReason.SAFETY_VIOLATION
    assert_result_reliability_metadata(result)


def test_safety_violation_on_primary_does_not_call_fallback() -> None:
    payload = build_receptionist_analysis_payload(
        intent="greeting",
        urgency="emergency",
    )
    primary = CountingLLMProvider(content=payload)
    fallback = CountingLLMProvider(
        content=build_receptionist_analysis_payload(),
        name="fallback",
    )
    service = build_orchestration_service(
        primary_provider=primary,
        fallback_provider=fallback,
        max_primary_attempts=2,
        max_fallback_attempts=1,
        fallback_provider_name=LLMProviderName.BEDROCK,
    )

    result = service.analyze_message(analysis_request())

    assert primary.call_count == 1
    assert fallback.call_count == 0
    assert result.used_fallback is True
    assert result.failure_reason == LLMFailureReason.SAFETY_VIOLATION
    assert_result_reliability_metadata(result)


def test_low_confidence_on_primary_does_not_call_fallback() -> None:
    payload = json.dumps(
        {
            "intent": "fallback",
            "confidence": 0.4,
            "urgency": "normal",
        },
    )
    primary = CountingLLMProvider(content=payload)
    fallback = CountingLLMProvider(
        content=build_receptionist_analysis_payload(),
        name="fallback",
    )
    service = build_orchestration_service(
        primary_provider=primary,
        fallback_provider=fallback,
        max_primary_attempts=2,
        max_fallback_attempts=1,
        fallback_provider_name=LLMProviderName.BEDROCK,
    )

    result = service.analyze_message(analysis_request())

    assert primary.call_count == 1
    assert fallback.call_count == 0
    assert result.used_fallback is False
    assert result.failure_reason == LLMFailureReason.LOW_CONFIDENCE
    assert_result_reliability_metadata(result)


def test_fallback_provider_not_instantiated_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = load_settings(monkeypatch, LLM_FALLBACK_ENABLED="false")

    with patch(
        "app.ai.provider_factory.create_llm_provider_from_settings",
    ) as create_provider:
        create_provider.return_value = FakeLLMProvider()
        service = get_llm_receptionist_analysis_service(settings=settings)

    assert service is not None
    assert create_provider.call_count == 1
    create_provider.assert_called_once_with(settings, settings.resolved_llm_primary_provider)
    assert service.fallback_provider is None


def test_metadata_records_used_fallback_provider() -> None:
    primary = AlwaysFailingLLMProvider()
    fallback = CountingLLMProvider(content=build_receptionist_analysis_payload())
    service = build_orchestration_service(
        primary_provider=primary,
        fallback_provider=fallback,
        fallback_provider_name=LLMProviderName.BEDROCK,
        max_primary_attempts=1,
        max_fallback_attempts=1,
    )

    result = service.analyze_message(analysis_request())

    assert result.used_fallback_provider is True
    assert result.primary_provider == LLMProviderName.FAKE.value
    assert result.fallback_provider == LLMProviderName.BEDROCK.value
    assert result.provider == LLMProviderName.BEDROCK.value
    assert_result_reliability_metadata(result)


def test_bedrock_fallback_can_be_configured_with_mocked_provider_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = load_settings(
        monkeypatch,
        LLM_PROVIDER="fake",
        LLM_FALLBACK_ENABLED="true",
        LLM_FALLBACK_PROVIDER="bedrock",
        BEDROCK_MODEL_ID="anthropic.claude-3-haiku-20240307-v1:0",
    )
    stub_client = MagicMock()

    with patch(
        "app.ai.bedrock_llm_provider.BedrockLLMProvider._create_client",
        return_value=stub_client,
    ) as create_client:
        service = get_llm_receptionist_analysis_service(settings=settings)

    assert service is not None
    create_client.assert_called_once()
    assert service.fallback_provider is not None
    assert service.primary_provider_name == LLMProviderName.FAKE
    assert service.fallback_provider_name == LLMProviderName.BEDROCK


def test_groq_fallback_not_instantiated_when_fallback_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = load_settings(
        monkeypatch,
        LLM_FALLBACK_ENABLED="false",
        LLM_FALLBACK_PROVIDER="groq",
    )

    with patch("app.ai.groq_provider.GroqLLMProvider") as groq_cls:
        service = get_llm_receptionist_analysis_service(settings=settings)

    groq_cls.assert_not_called()
    assert service is not None
    assert service.fallback_provider is None


def test_groq_primary_with_bedrock_fallback_config_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = load_settings(
        monkeypatch,
        LLM_PRIMARY_PROVIDER="groq",
        GROQ_API_KEY="gsk_test",
        GROQ_MODEL="llama-3.3-70b-versatile",
        LLM_FALLBACK_ENABLED="true",
        LLM_FALLBACK_PROVIDER="bedrock",
        BEDROCK_MODEL_ID="anthropic.claude-3-haiku-20240307-v1:0",
    )
    stub_client = MagicMock()

    with patch(
        "app.ai.bedrock_llm_provider.BedrockLLMProvider._create_client",
        return_value=stub_client,
    ) as create_client:
        service = get_llm_receptionist_analysis_service(settings=settings)

    assert service is not None
    assert isinstance(service.primary_provider, GroqLLMProvider)
    assert service.fallback_provider is not None
    create_client.assert_called_once()
    assert service.primary_provider_name == LLMProviderName.GROQ
    assert service.fallback_provider_name == LLMProviderName.BEDROCK


def test_bedrock_primary_with_groq_fallback_config_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = load_settings(
        monkeypatch,
        LLM_PRIMARY_PROVIDER="bedrock",
        BEDROCK_MODEL_ID="anthropic.claude-3-haiku-20240307-v1:0",
        LLM_FALLBACK_ENABLED="true",
        LLM_FALLBACK_PROVIDER="groq",
        GROQ_API_KEY="gsk_test",
        GROQ_MODEL="llama-3.3-70b-versatile",
    )
    stub_client = MagicMock()

    with patch(
        "app.ai.bedrock_llm_provider.BedrockLLMProvider._create_client",
        return_value=stub_client,
    ) as create_client:
        service = get_llm_receptionist_analysis_service(settings=settings)

    assert service is not None
    create_client.assert_called_once()
    assert isinstance(service.primary_provider, BedrockLLMProvider)
    assert isinstance(service.fallback_provider, GroqLLMProvider)
    assert service.primary_provider_name == LLMProviderName.BEDROCK
    assert service.fallback_provider_name == LLMProviderName.GROQ
