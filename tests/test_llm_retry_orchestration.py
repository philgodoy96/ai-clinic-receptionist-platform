from __future__ import annotations

import json

from app.ai.llm_provider import LLMProviderName
from app.ai.llm_reliability import LLMFailureReason
from app.ai.receptionist_output import ReceptionistLLMIntent
from app.services.llm_receptionist import ReceptionistAnalysisRequest
from tests.llm_provider_test_helpers import build_receptionist_analysis_payload
from tests.llm_reliability_test_helpers import (
    CountingLLMProvider,
    FailOnceThenSucceedProvider,
    SequentialContentProvider,
    analysis_request,
    assert_result_reliability_metadata,
    build_orchestration_service,
)


def test_first_attempt_success_calls_provider_once_without_fallback() -> None:
    fallback = CountingLLMProvider(
        build_receptionist_analysis_payload(),
        name="fallback",
    )
    primary = CountingLLMProvider(build_receptionist_analysis_payload(), name="primary")
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
    assert result.attempt_count == 1
    assert result.used_fallback is False
    assert result.used_fallback_provider is False
    assert_result_reliability_metadata(result)


def test_local_repair_from_markdown_fenced_json_without_retry() -> None:
    payload = build_receptionist_analysis_payload()
    fenced_content = f"```json\n{payload}\n```"
    primary = CountingLLMProvider(fenced_content)
    service = build_orchestration_service(primary_provider=primary)

    result = service.analyze_message(analysis_request())

    assert primary.call_count == 1
    assert result.used_repair is True
    assert result.used_fallback is False
    assert_result_reliability_metadata(result)


def test_local_repair_from_extra_wrapped_text_without_retry() -> None:
    payload = build_receptionist_analysis_payload()
    wrapped_content = f"Here is the structured result:\n{payload}\nEnd of output."
    primary = CountingLLMProvider(wrapped_content)
    service = build_orchestration_service(primary_provider=primary)

    result = service.analyze_message(analysis_request())

    assert primary.call_count == 1
    assert result.used_repair is True
    assert result.primary_attempt_count == 1
    assert_result_reliability_metadata(result)


def test_provider_exception_retries_and_succeeds_on_second_attempt() -> None:
    primary = FailOnceThenSucceedProvider(
        success_content=build_receptionist_analysis_payload(),
    )
    service = build_orchestration_service(primary_provider=primary)

    result = service.analyze_message(analysis_request())

    assert primary.call_count == 2
    assert result.used_fallback is False
    assert result.primary_attempt_count == 2
    assert result.attempt_count == 2
    assert_result_reliability_metadata(result)


def test_invalid_json_retries_once_when_retryable_then_succeeds() -> None:
    primary = SequentialContentProvider(
        [
            "this is not valid json",
            build_receptionist_analysis_payload(),
        ],
    )
    service = build_orchestration_service(primary_provider=primary)

    result = service.analyze_message(analysis_request())

    assert primary.call_count == 2
    assert result.used_fallback is False
    assert result.primary_attempt_count == 2
    assert_result_reliability_metadata(result)


def test_json_repair_failure_retries_once_when_retryable_then_succeeds() -> None:
    primary = SequentialContentProvider(
        [
            "{not valid json}",
            build_receptionist_analysis_payload(),
        ],
    )
    service = build_orchestration_service(primary_provider=primary)

    result = service.analyze_message(analysis_request())

    assert primary.call_count == 2
    assert result.used_fallback is False
    assert result.primary_attempt_count == 2
    assert_result_reliability_metadata(result)


def test_invalid_json_after_max_attempts_uses_deterministic_fallback() -> None:
    primary = SequentialContentProvider(
        [
            "this is not valid json",
            "still not valid json",
        ],
    )
    fallback = CountingLLMProvider(build_receptionist_analysis_payload(), name="fallback")
    service = build_orchestration_service(
        primary_provider=primary,
        fallback_provider=fallback,
        max_fallback_attempts=1,
        fallback_provider_name=LLMProviderName.BEDROCK,
    )

    result = service.analyze_message(analysis_request())

    assert primary.call_count == 2
    assert fallback.call_count == 0
    assert result.used_fallback is True
    assert result.failure_reason == LLMFailureReason.JSON_PARSE_FAILED
    assert result.primary_attempt_count == 2
    assert result.fallback_attempt_count == 0
    assert_result_reliability_metadata(result)


def test_safety_violation_does_not_retry_primary_or_call_fallback() -> None:
    payload = build_receptionist_analysis_payload(
        intent="greeting",
        urgency="emergency",
    )
    primary = CountingLLMProvider(payload)
    fallback = CountingLLMProvider(build_receptionist_analysis_payload(), name="fallback")
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


def test_medical_emergency_succeeds_without_retry_or_fallback() -> None:
    payload = build_receptionist_analysis_payload(
        intent="emergency",
        urgency="emergency",
        confidence=0.99,
        safety_flags=["medical_emergency"],
    )
    primary = CountingLLMProvider(payload)
    fallback = CountingLLMProvider(build_receptionist_analysis_payload(), name="fallback")
    service = build_orchestration_service(
        primary_provider=primary,
        fallback_provider=fallback,
        max_primary_attempts=2,
        max_fallback_attempts=1,
        fallback_provider_name=LLMProviderName.BEDROCK,
    )

    result = service.analyze_message(
        ReceptionistAnalysisRequest(
            user_message="I have chest pain",
            conversation_context={},
        ),
    )

    assert primary.call_count == 1
    assert fallback.call_count == 0
    assert result.used_fallback is False
    assert result.used_fallback_provider is False
    assert result.analysis.intent == ReceptionistLLMIntent.EMERGENCY
    assert_result_reliability_metadata(result)


def test_explicit_human_request_succeeds_without_retry_or_fallback() -> None:
    payload = build_receptionist_analysis_payload(
        intent="human_escalation_request",
        requires_human=True,
        confidence=0.9,
    )
    primary = CountingLLMProvider(payload)
    fallback = CountingLLMProvider(build_receptionist_analysis_payload(), name="fallback")
    service = build_orchestration_service(
        primary_provider=primary,
        fallback_provider=fallback,
        max_primary_attempts=2,
        max_fallback_attempts=1,
        fallback_provider_name=LLMProviderName.BEDROCK,
    )

    result = service.analyze_message(
        ReceptionistAnalysisRequest(
            user_message="Can I speak to a real person?",
            conversation_context={},
        ),
    )

    assert primary.call_count == 1
    assert fallback.call_count == 0
    assert result.used_fallback is False
    assert result.analysis.intent == ReceptionistLLMIntent.HUMAN_ESCALATION_REQUEST
    assert_result_reliability_metadata(result)


def test_low_confidence_does_not_retry_or_call_fallback() -> None:
    payload = json.dumps(
        {
            "intent": "fallback",
            "confidence": 0.4,
            "urgency": "normal",
        },
    )
    primary = CountingLLMProvider(payload)
    fallback = CountingLLMProvider(build_receptionist_analysis_payload(), name="fallback")
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


def test_success_metadata_includes_all_reliability_fields() -> None:
    service = build_orchestration_service(
        primary_provider=CountingLLMProvider(build_receptionist_analysis_payload()),
    )

    result = service.analyze_message(analysis_request())

    assert result.attempt_count == 1
    assert result.primary_attempt_count == 1
    assert result.fallback_attempt_count == 0
    assert result.failure_category.value == "none"
    assert result.failure_reason == LLMFailureReason.NONE
    assert_result_reliability_metadata(result)
