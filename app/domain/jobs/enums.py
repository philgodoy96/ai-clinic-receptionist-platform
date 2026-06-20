from enum import StrEnum


class EmailJobType(StrEnum):
    APPOINTMENT_CONFIRMATION = "appointment_confirmation"


class EmailJobStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    SENT = "sent"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"