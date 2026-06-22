from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from app.ai.fake_llm_provider import FakeLLMProvider
from app.ai.llm_provider import LLMProviderError, LLMProviderName, LLMRequest, LLMResponse
from app.ai.llm_reliability import LLMFailureReason
from app.api.dependencies import get_llm_receptionist_analysis_service
from app.services.llm_receptionist import (
    LLMReceptionistAnalysisService,
    ReceptionistAnalysisRequest,
)
from tests.llm_provider_test_helpers import (
    RaisingLLMProvider,
    build_receptionist_analysis_payload,
)
from tests.test_llm_provider_config import load_settings


class CountingLLMProvider:
    def __init__(self, *, content: str, name: str = "counting") -> None:
        self.content = content
        self.name = name
        self.call_count = 0

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.call_count += 1
        return LLMResponse(
            content=self.content,
            model=f"{self.name}-model",
            input_tokens=12,
            output_tokens=8,
            estimated_cost_micros=42,
        )


class AlwaysFailingPrimaryProvider:
    def complete(self, request: LLMRequest) -> LLMResponse:
        raise LLMProviderError("simulated primary provider failure")


def _analysis_request() -> ReceptionistAnalysisRequest:
    return ReceptionistAnalysisRequest(
        user_message="Hello",
        conversation_context={},
    )


def _build_service(
    *,
    primary_provider: CountingLLMProvider | AlwaysFailingPrimaryProvider | RaisingLLMProvider,
    fallback_provider: CountingLLMProvider | RaisingLLMProvider | None = None,
    max_primary_attempts: int = 1,
    max_fallback_attempts: int = 0,
) -> LLMReceptionistAnalysisService:
    return LLMReceptionistAnalysisService(
        primary_provider=primary_provider,
        fallback_provider=fallback_provider,
        primary_provider_name=LLMProviderName.FAKE,
        fallback_provider_name=(
            LLMProviderName.BEDROCK if fallback_provider is not None else None
        ),
        max_primary_attempts=max_primary_attempts,
        max_fallback_attempts=max_fallback_attempts,
    )


def test_fallback_disabled_primary_exhaustion_uses_deterministic_fallback() -> None:
    primary = AlwaysFailingPrimaryProvider()
    fallback = CountingLLMProvider(content=build_receptionist_analysis_payload())
    service = _build_service(
        primary_provider=primary,
        fallback_provider=fallback,
        max_primary_attempts=1,
        max_fallback_attempts=0,
    )

    result = service.analyze_message(_analysis_request())

    assert fallback.call_count == 0
    assert result.used_fallback is True
    assert result.used_fallback_provider is False
    assert result.fallback_attempt_count == 0


def test_fallback_enabled_primary_failure_then_fallback_success() -> None:
    primary = AlwaysFailingPrimaryProvider()
    fallback = CountingLLMProvider(
        content=build_receptionist_analysis_payload(),
        name="fallback",
    )
    service = _build_service(
        primary_provider=primary,
        fallback_provider=fallback,
        max_primary_attempts=1,
        max_fallback_attempts=1,
    )

    result = service.analyze_message(_analysis_request())

    assert fallback.call_count == 1
    assert result.used_fallback is False
    assert result.used_fallback_provider is True
    assert result.provider == LLMProviderName.BEDROCK.value
    assert result.primary_attempt_count == 1
    assert result.fallback_attempt_count == 1
    assert result.attempt_count == 2


def test_safety_violation_on_primary_does_not_call_fallback() -> None:
    payload = build_receptionist_analysis_payload(
        intent="greeting",
        urgency="emergency",
    )
    primary = CountingLLMProvider(content=payload)
    fallback = CountingLLMProvider(content=build_receptionist_analysis_payload())
    service = _build_service(
        primary_provider=primary,
        fallback_provider=fallback,
        max_primary_attempts=2,
        max_fallback_attempts=1,
    )

    result = service.analyze_message(_analysis_request())

    assert primary.call_count == 1
    assert fallback.call_count == 0
    assert result.used_fallback is True
    assert result.failure_reason == LLMFailureReason.SAFETY_VIOLATION


def test_low_confidence_on_primary_does_not_call_fallback() -> None:
    payload = json.dumps(
        {
            "intent": "fallback",
            "confidence": 0.4,
            "urgency": "normal",
        },
    )
    primary = CountingLLMProvider(content=payload)
    fallback = CountingLLMProvider(content=build_receptionist_analysis_payload())
    service = _build_service(
        primary_provider=primary,
        fallback_provider=fallback,
        max_primary_attempts=2,
        max_fallback_attempts=1,
    )

    result = service.analyze_message(_analysis_request())

    assert primary.call_count == 1
    assert fallback.call_count == 0
    assert result.used_fallback is False
    assert result.failure_reason == LLMFailureReason.LOW_CONFIDENCE


def test_fallback_provider_exception_leads_to_deterministic_fallback() -> None:
    primary = AlwaysFailingPrimaryProvider()
    fallback = RaisingLLMProvider()
    service = _build_service(
        primary_provider=primary,
        fallback_provider=fallback,
        max_primary_attempts=1,
        max_fallback_attempts=1,
    )

    result = service.analyze_message(_analysis_request())

    assert result.used_fallback is True
    assert result.used_fallback_provider is False
    assert result.fallback_attempt_count == 1
    assert result.failure_reason == LLMFailureReason.PROVIDER_EXCEPTION


def test_fallback_provider_not_instantiated_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = load_settings(monkeypatch, LLM_FALLBACK_ENABLED="false")

    with patch(
        "app.api.dependencies.create_llm_provider_from_settings",
    ) as create_provider:
        create_provider.return_value = FakeLLMProvider()
        service = get_llm_receptionist_analysis_service(settings=settings)

    assert service is not None
    assert create_provider.call_count == 1
    create_provider.assert_called_once_with(settings, settings.resolved_llm_primary_provider)
    assert service.fallback_provider is None


def test_metadata_records_used_fallback_provider() -> None:
    primary = AlwaysFailingPrimaryProvider()
    fallback = CountingLLMProvider(content=build_receptionist_analysis_payload())
    service = _build_service(
        primary_provider=primary,
        fallback_provider=fallback,
        max_primary_attempts=1,
        max_fallback_attempts=1,
    )

    result = service.analyze_message(_analysis_request())

    assert result.used_fallback_provider is True
    assert result.primary_provider == LLMProviderName.FAKE.value
    assert result.fallback_provider == LLMProviderName.BEDROCK.value
    assert result.provider == LLMProviderName.BEDROCK.value


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
