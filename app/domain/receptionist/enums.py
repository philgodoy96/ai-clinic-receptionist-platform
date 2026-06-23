from enum import StrEnum


class ReceptionistTemplateType(StrEnum):
    GREETING = "greeting"
    ASK_FOR_SPECIALTY = "ask_for_specialty"
    ASK_FOR_DATE = "ask_for_date"
    ASK_FOR_TIME_PREFERENCE = "ask_for_time_preference"
    AVAILABILITY_OPTIONS = "availability_options"
    SLOT_HOLD_CREATED = "slot_hold_created"
    ASK_FOR_PATIENT_IDENTITY = "ask_for_patient_identity"
    ASK_FOR_CONFIRMATION = "ask_for_confirmation"
    BOOKING_SUCCEEDED = "booking_succeeded"
    BOOKING_FAILED = "booking_failed"
    CANCELLATION_SUCCEEDED = "cancellation_succeeded"
    CANCELLATION_FAILED = "cancellation_failed"
    RESCHEDULE_SUCCEEDED = "reschedule_succeeded"
    RESCHEDULE_FAILED = "reschedule_failed"
    EMERGENCY_GUIDANCE = "emergency_guidance"
    HUMAN_ESCALATION = "human_escalation"
    UNSUPPORTED_REQUEST = "unsupported_request"
    GENERIC_ERROR = "generic_error"


class ReceptionistResponseMode(StrEnum):
    DETERMINISTIC = "deterministic"


class ReceptionistResponseType(StrEnum):
    INFORMATIONAL = "informational"
    SCHEDULING = "scheduling"
    CONFIRMATION = "confirmation"
    ESCALATION = "escalation"
    FALLBACK = "fallback"
    CRITICAL = "critical"


class ReceptionistResponseSafetyLevel(StrEnum):
    STANDARD = "standard"
    ELEVATED = "elevated"
    CRITICAL = "critical"
