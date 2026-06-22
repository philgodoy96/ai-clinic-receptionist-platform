from __future__ import annotations

from app.ai.llm_provider import LLMProviderError, provider_failure_reason
from app.ai.llm_reliability import (
    LLMFailureCategory,
    LLMFailureReason,
    failure_category_for_reason,
    is_fallback_eligible,
    is_fallback_provider_eligible,
    is_non_retryable_failure,
    is_retryable_failure,
)
from app.ai.structured_output import StructuredOutputParseError
from app.services.llm_receptionist import (
    LLMReceptionistAnalysisService,
    ReceptionistAnalysisRequest,
)
from tests.llm_provider_test_helpers import StaticContentLLMProvider
from tests.llm_reliability_test_helpers import assert_result_reliability_metadata


def test_provider_exception_is_retryable_and_fallback_eligible() -> None:
    reason = provider_failure_reason(LLMProviderError("simulated provider failure"))

    assert reason == LLMFailureReason.PROVIDER_EXCEPTION
    assert is_retryable_failure(reason) is True
    assert is_non_retryable_failure(reason) is False
    assert is_fallback_eligible(reason) is True
    assert failure_category_for_reason(reason) == LLMFailureCategory.RETRYABLE


def test_provider_timeout_is_retryable() -> None:
    reason = provider_failure_reason(LLMProviderError("Read timeout on endpoint URL"))

    assert reason == LLMFailureReason.PROVIDER_TIMEOUT
    assert is_retryable_failure(reason) is True
    assert is_fallback_eligible(reason) is True


def test_provider_rate_limit_is_retryable() -> None:
    reason = provider_failure_reason(
        LLMProviderError("Rate limit exceeded for model invocation"),
    )

    assert reason == LLMFailureReason.PROVIDER_RATE_LIMITED
    assert is_retryable_failure(reason) is True
    assert is_fallback_eligible(reason) is True


def test_empty_response_is_retryable() -> None:
    reason = provider_failure_reason(
        LLMProviderError("Bedrock response did not include text content"),
    )

    assert reason == LLMFailureReason.EMPTY_RESPONSE
    assert is_retryable_failure(reason) is True
    assert is_fallback_eligible(reason) is True


def test_safety_violation_is_non_retryable_and_not_fallback_eligible() -> None:
    reason = LLMFailureReason.SAFETY_VIOLATION

    assert is_retryable_failure(reason) is False
    assert is_non_retryable_failure(reason) is True
    assert is_fallback_eligible(reason) is False
    assert failure_category_for_reason(reason) == LLMFailureCategory.NON_RETRYABLE


def test_medical_emergency_is_non_retryable() -> None:
    reason = LLMFailureReason.MEDICAL_EMERGENCY

    assert is_retryable_failure(reason) is False
    assert is_non_retryable_failure(reason) is True
    assert is_fallback_eligible(reason) is False


def test_human_escalation_request_is_non_retryable() -> None:
    reason = LLMFailureReason.HUMAN_ESCALATION_REQUEST

    assert is_retryable_failure(reason) is False
    assert is_non_retryable_failure(reason) is True
    assert is_fallback_eligible(reason) is False


def test_low_confidence_is_non_retryable() -> None:
    reason = LLMFailureReason.LOW_CONFIDENCE

    assert is_retryable_failure(reason) is False
    assert is_non_retryable_failure(reason) is True
    assert is_fallback_eligible(reason) is False
    assert failure_category_for_reason(reason) == LLMFailureCategory.NON_RETRYABLE


def test_json_parse_failure_is_repairable_and_fallback_eligible() -> None:
    reason = LLMFailureReason.JSON_PARSE_FAILED

    assert is_retryable_failure(reason) is False
    assert is_non_retryable_failure(reason) is True
    assert is_fallback_eligible(reason) is True
    assert failure_category_for_reason(reason) == LLMFailureCategory.REPAIRABLE


def test_json_repair_failure_is_retryable_and_fallback_eligible() -> None:
    reason = LLMFailureReason.JSON_REPAIR_FAILED

    assert is_retryable_failure(reason) is True
    assert is_fallback_eligible(reason) is True
    assert failure_category_for_reason(reason) == LLMFailureCategory.RETRYABLE


def test_schema_validation_failure_is_non_retryable_and_fallback_eligible() -> None:
    reason = LLMFailureReason.SCHEMA_VALIDATION_FAILED

    assert is_retryable_failure(reason) is False
    assert is_non_retryable_failure(reason) is True
    assert is_fallback_eligible(reason) is True
    assert is_fallback_provider_eligible(reason) is False
    assert failure_category_for_reason(reason) == LLMFailureCategory.NON_RETRYABLE


def test_fallback_provider_eligible_for_retryable_provider_exception() -> None:
    reason = LLMFailureReason.PROVIDER_EXCEPTION

    assert is_fallback_provider_eligible(reason) is True


def test_fallback_provider_eligible_for_json_repair_failure() -> None:
    reason = LLMFailureReason.JSON_REPAIR_FAILED

    assert is_fallback_provider_eligible(reason) is True


def test_fallback_provider_not_eligible_for_json_parse_failure() -> None:
    reason = LLMFailureReason.JSON_PARSE_FAILED

    assert is_fallback_eligible(reason) is True
    assert is_fallback_provider_eligible(reason) is False


def test_fallback_provider_not_eligible_for_safety_violation() -> None:
    reason = LLMFailureReason.SAFETY_VIOLATION

    assert is_fallback_provider_eligible(reason) is False


def test_analysis_service_records_json_parse_failure_category() -> None:
    service = LLMReceptionistAnalysisService(
        provider=StaticContentLLMProvider("this is not valid json"),
    )

    result = service.analyze_message(
        ReceptionistAnalysisRequest(
            user_message="Hello",
            conversation_context={},
        ),
    )

    assert result.failure_reason == LLMFailureReason.JSON_PARSE_FAILED
    assert result.failure_category == LLMFailureCategory.REPAIRABLE
    assert_result_reliability_metadata(result)


def test_analysis_service_records_json_repair_failure_reason() -> None:
    service = LLMReceptionistAnalysisService(
        provider=StaticContentLLMProvider("{not valid json}"),
    )

    result = service.analyze_message(
        ReceptionistAnalysisRequest(
            user_message="Hello",
            conversation_context={},
        ),
    )

    assert result.failure_reason == LLMFailureReason.JSON_REPAIR_FAILED
    assert result.failure_category == LLMFailureCategory.RETRYABLE
    assert_result_reliability_metadata(result)


def test_parse_error_repair_attempted_flag() -> None:
    error = StructuredOutputParseError("LLM output repair failed", repair_attempted=True)

    assert error.repair_attempted is True
