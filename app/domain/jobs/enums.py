from enum import StrEnum


class EmailJobType(StrEnum):
    APPOINTMENT_CONFIRMATION = "appointment_confirmation"
    HUMAN_ESCALATION_NOTIFICATION = "human_escalation_notification"


class EmailJobStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    SENT = "sent"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"