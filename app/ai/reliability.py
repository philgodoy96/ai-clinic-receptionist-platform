from enum import StrEnum


class LLMFailureReason(StrEnum):
    NONE = "none"
    PROVIDER_ERROR = "provider_error"
    INVALID_JSON = "invalid_json"
    SCHEMA_VALIDATION_ERROR = "schema_validation_error"
    SAFETY_VIOLATION = "safety_violation"
    LOW_CONFIDENCE = "low_confidence"
    UNKNOWN_ERROR = "unknown_error"


class LLMOutputSafetyViolation(ValueError):
    pass


MIN_ACCEPTED_CONFIDENCE = 0.45