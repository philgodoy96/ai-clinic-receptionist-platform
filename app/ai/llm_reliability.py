from __future__ import annotations

from enum import StrEnum


class LLMFailureCategory(StrEnum):
    NONE = "none"
    REPAIRABLE = "repairable"
    RETRYABLE = "retryable"
    NON_RETRYABLE = "non_retryable"
    FALLBACK_EXHAUSTED = "fallback_exhausted"


class LLMFailureReason(StrEnum):
    NONE = "none"
    PROVIDER_EXCEPTION = "provider_exception"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_RATE_LIMITED = "provider_rate_limited"
    EMPTY_RESPONSE = "empty_response"
    JSON_PARSE_FAILED = "json_parse_failed"
    JSON_REPAIR_FAILED = "json_repair_failed"
    SCHEMA_VALIDATION_FAILED = "schema_validation_failed"
    SAFETY_VIOLATION = "safety_violation"
    LOW_CONFIDENCE = "low_confidence"
    HUMAN_ESCALATION_REQUEST = "human_escalation_request"
    MEDICAL_EMERGENCY = "medical_emergency"
    POLICY_VIOLATION = "policy_violation"
    FALLBACK_PROVIDER_UNAVAILABLE = "fallback_provider_unavailable"
    FALLBACK_EXHAUSTED = "fallback_exhausted"


_RETRYABLE_REASONS = frozenset(
    {
        LLMFailureReason.PROVIDER_EXCEPTION,
        LLMFailureReason.PROVIDER_TIMEOUT,
        LLMFailureReason.PROVIDER_RATE_LIMITED,
        LLMFailureReason.EMPTY_RESPONSE,
        LLMFailureReason.JSON_REPAIR_FAILED,
    },
)

_NON_RETRYABLE_REASONS = frozenset(
    {
        LLMFailureReason.JSON_PARSE_FAILED,
        LLMFailureReason.SCHEMA_VALIDATION_FAILED,
        LLMFailureReason.SAFETY_VIOLATION,
        LLMFailureReason.LOW_CONFIDENCE,
        LLMFailureReason.HUMAN_ESCALATION_REQUEST,
        LLMFailureReason.MEDICAL_EMERGENCY,
        LLMFailureReason.POLICY_VIOLATION,
        LLMFailureReason.FALLBACK_PROVIDER_UNAVAILABLE,
        LLMFailureReason.FALLBACK_EXHAUSTED,
    },
)

_FALLBACK_ELIGIBLE_REASONS = frozenset(
    {
        LLMFailureReason.PROVIDER_EXCEPTION,
        LLMFailureReason.PROVIDER_TIMEOUT,
        LLMFailureReason.PROVIDER_RATE_LIMITED,
        LLMFailureReason.EMPTY_RESPONSE,
        LLMFailureReason.JSON_PARSE_FAILED,
        LLMFailureReason.JSON_REPAIR_FAILED,
        LLMFailureReason.SCHEMA_VALIDATION_FAILED,
    },
)


class LLMOutputSafetyViolation(ValueError):
    pass


MIN_ACCEPTED_CONFIDENCE = 0.45


def is_retryable_failure(reason: LLMFailureReason) -> bool:
    return reason in _RETRYABLE_REASONS


def is_non_retryable_failure(reason: LLMFailureReason) -> bool:
    if reason == LLMFailureReason.NONE:
        return False
    return reason in _NON_RETRYABLE_REASONS


def is_fallback_eligible(reason: LLMFailureReason) -> bool:
    return reason in _FALLBACK_ELIGIBLE_REASONS


def failure_category_for_reason(reason: LLMFailureReason) -> LLMFailureCategory:
    if reason == LLMFailureReason.NONE:
        return LLMFailureCategory.NONE
    if reason == LLMFailureReason.LOW_CONFIDENCE:
        return LLMFailureCategory.NON_RETRYABLE
    if reason == LLMFailureReason.JSON_PARSE_FAILED:
        return LLMFailureCategory.REPAIRABLE
    if reason == LLMFailureReason.FALLBACK_EXHAUSTED:
        return LLMFailureCategory.FALLBACK_EXHAUSTED
    if is_retryable_failure(reason):
        return LLMFailureCategory.RETRYABLE
    return LLMFailureCategory.NON_RETRYABLE


def classify_provider_error(message: str) -> LLMFailureReason:
    normalized = message.lower()
    if "timeout" in normalized or "timed out" in normalized:
        return LLMFailureReason.PROVIDER_TIMEOUT
    if "throttl" in normalized or ("rate" in normalized and "limit" in normalized):
        return LLMFailureReason.PROVIDER_RATE_LIMITED
    if "did not include text" in normalized or "missing text content" in normalized:
        return LLMFailureReason.EMPTY_RESPONSE
    return LLMFailureReason.PROVIDER_EXCEPTION


def failure_reason_for_typed_provider_error(error: BaseException) -> LLMFailureReason | None:
    from app.ai.llm_provider import LLMProviderRateLimitError, LLMProviderTimeoutError

    if isinstance(error, LLMProviderTimeoutError):
        return LLMFailureReason.PROVIDER_TIMEOUT
    if isinstance(error, LLMProviderRateLimitError):
        return LLMFailureReason.PROVIDER_RATE_LIMITED
    return None


def is_primary_provider_retryable(reason: LLMFailureReason) -> bool:
    if reason in {
        LLMFailureReason.NONE,
        LLMFailureReason.SAFETY_VIOLATION,
        LLMFailureReason.LOW_CONFIDENCE,
        LLMFailureReason.HUMAN_ESCALATION_REQUEST,
        LLMFailureReason.MEDICAL_EMERGENCY,
        LLMFailureReason.POLICY_VIOLATION,
        LLMFailureReason.SCHEMA_VALIDATION_FAILED,
        LLMFailureReason.FALLBACK_PROVIDER_UNAVAILABLE,
        LLMFailureReason.FALLBACK_EXHAUSTED,
    }:
        return False
    if is_retryable_failure(reason):
        return True
    return failure_category_for_reason(reason) == LLMFailureCategory.REPAIRABLE


def is_fallback_provider_eligible(reason: LLMFailureReason) -> bool:
    return is_fallback_eligible(reason) and is_retryable_failure(reason)


def parse_failure_reason_from_parse_error(
    *,
    repair_attempted: bool,
) -> LLMFailureReason:
    if repair_attempted:
        return LLMFailureReason.JSON_REPAIR_FAILED
    return LLMFailureReason.JSON_PARSE_FAILED
