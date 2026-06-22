from __future__ import annotations

import json

from app.ai.llm_provider import LLMProviderError, LLMRequest, LLMResponse
from app.ai.llm_reliability import LLMFailureReason
from app.services.llm_receptionist import (
    LLMReceptionistAnalysisService,
    ReceptionistAnalysisRequest,
)
from tests.llm_provider_test_helpers import build_receptionist_analysis_payload


class CountingLLMProvider:
    def __init__(self, content: str) -> None:
        self.content = content
        self.call_count = 0

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.call_count += 1
        return LLMResponse(
            content=self.content,
            model="test-model",
            input_tokens=12,
            output_tokens=8,
            estimated_cost_micros=42,
        )


class FailOnceThenSucceedProvider:
    def __init__(self, *, success_content: str) -> None:
        self.success_content = success_content
        self.call_count = 0

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.call_count += 1
        if self.call_count == 1:
            raise LLMProviderError("simulated provider failure")
        return LLMResponse(
            content=self.success_content,
            model="test-model",
            input_tokens=12,
            output_tokens=8,
            estimated_cost_micros=42,
        )


class SequentialContentProvider:
    def __init__(self, contents: list[str]) -> None:
        self.contents = contents
        self.call_count = 0

    def complete(self, request: LLMRequest) -> LLMResponse:
        index = min(self.call_count, len(self.contents) - 1)
        content = self.contents[index]
        self.call_count += 1
        return LLMResponse(
            content=content,
            model="test-model",
            input_tokens=12,
            output_tokens=8,
            estimated_cost_micros=42,
        )


def _analysis_request() -> ReceptionistAnalysisRequest:
    return ReceptionistAnalysisRequest(
        user_message="Hello",
        conversation_context={},
    )


def test_success_first_attempt_calls_provider_once() -> None:
    provider = CountingLLMProvider(build_receptionist_analysis_payload())
    service = LLMReceptionistAnalysisService(provider=provider)

    result = service.analyze_message(_analysis_request())

    assert provider.call_count == 1
    assert result.used_fallback is False
    assert result.primary_attempt_count == 1
    assert result.attempt_count == 1
    assert result.fallback_attempt_count == 0
    assert result.used_repair is False


def test_repairable_markdown_fenced_json_uses_repair_without_retry() -> None:
    payload = build_receptionist_analysis_payload()
    fenced_content = f"```json\n{payload}\n```"
    provider = CountingLLMProvider(fenced_content)
    service = LLMReceptionistAnalysisService(provider=provider)

    result = service.analyze_message(_analysis_request())

    assert provider.call_count == 1
    assert result.used_fallback is False
    assert result.used_repair is True
    assert result.primary_attempt_count == 1


def test_provider_exception_then_success_retries_once() -> None:
    provider = FailOnceThenSucceedProvider(
        success_content=build_receptionist_analysis_payload(),
    )
    service = LLMReceptionistAnalysisService(provider=provider)

    result = service.analyze_message(_analysis_request())

    assert provider.call_count == 2
    assert result.used_fallback is False
    assert result.primary_attempt_count == 2
    assert result.attempt_count == 2


def test_invalid_json_then_valid_json_retries_once() -> None:
    provider = SequentialContentProvider(
        [
            "this is not valid json",
            build_receptionist_analysis_payload(),
        ],
    )
    service = LLMReceptionistAnalysisService(provider=provider)

    result = service.analyze_message(_analysis_request())

    assert provider.call_count == 2
    assert result.used_fallback is False
    assert result.primary_attempt_count == 2


def test_invalid_json_twice_falls_back() -> None:
    provider = SequentialContentProvider(
        [
            "this is not valid json",
            "still not valid json",
        ],
    )
    service = LLMReceptionistAnalysisService(provider=provider)

    result = service.analyze_message(_analysis_request())

    assert provider.call_count == 2
    assert result.used_fallback is True
    assert result.failure_reason == LLMFailureReason.JSON_PARSE_FAILED
    assert result.primary_attempt_count == 2
    assert result.fallback_attempt_count == 0


def test_safety_violation_does_not_retry() -> None:
    payload = build_receptionist_analysis_payload(
        intent="greeting",
        urgency="emergency",
    )
    provider = CountingLLMProvider(payload)
    service = LLMReceptionistAnalysisService(provider=provider)

    result = service.analyze_message(_analysis_request())

    assert provider.call_count == 1
    assert result.used_fallback is True
    assert result.failure_reason == LLMFailureReason.SAFETY_VIOLATION
    assert result.primary_attempt_count == 1


def test_low_confidence_does_not_retry() -> None:
    payload = json.dumps(
        {
            "intent": "fallback",
            "confidence": 0.4,
            "urgency": "normal",
        },
    )
    provider = CountingLLMProvider(payload)
    service = LLMReceptionistAnalysisService(provider=provider)

    result = service.analyze_message(_analysis_request())

    assert provider.call_count == 1
    assert result.used_fallback is False
    assert result.failure_reason == LLMFailureReason.LOW_CONFIDENCE
    assert result.primary_attempt_count == 1


def test_metadata_includes_attempt_counts() -> None:
    provider = FailOnceThenSucceedProvider(
        success_content=build_receptionist_analysis_payload(),
    )
    service = LLMReceptionistAnalysisService(provider=provider)

    result = service.analyze_message(_analysis_request())

    assert result.attempt_count == result.primary_attempt_count
    assert result.primary_attempt_count == 2
    assert result.fallback_attempt_count == 0
    assert result.failure_category.value == "none"
    assert result.failure_reason == LLMFailureReason.NONE
