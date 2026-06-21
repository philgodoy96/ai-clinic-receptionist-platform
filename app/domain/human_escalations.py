from enum import StrEnum


class HumanEscalationStatus(StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"
    CANCELLED = "cancelled"


class HumanEscalationReason(StrEnum):
    USER_REQUESTED_HUMAN = "user_requested_human"
    MEDICAL_EMERGENCY = "medical_emergency"
    REPEATED_FALLBACK = "repeated_fallback"
    REPEATED_SLOT_FILLING_REJECTION = "repeated_slot_filling_rejection"
    REPEATED_LOW_CONFIDENCE = "repeated_low_confidence"
    REPEATED_BOOKING_CONFLICT = "repeated_booking_conflict"
    NO_PROGRESS = "no_progress"
    UNKNOWN = "unknown"


class HumanEscalationPriority(StrEnum):
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class HumanEscalationSource(StrEnum):
    CHAT = "chat"
    RETELL_VOICE = "retell_voice"
    SYSTEM = "system"
