from enum import StrEnum


class VoiceCallStatus(StrEnum):
    CREATED = "created"
    IN_PROGRESS = "in_progress"
    ENDED = "ended"
    FAILED = "failed"
    UNKNOWN = "unknown"


class NormalizedVoiceCallEventType(StrEnum):
    CALL_STARTED = "call_started"
    CALL_UPDATED = "call_updated"
    CALL_ENDED = "call_ended"
    CALL_FAILED = "call_failed"
    UNKNOWN = "unknown"
