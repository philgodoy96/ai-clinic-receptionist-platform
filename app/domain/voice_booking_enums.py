from __future__ import annotations

from enum import StrEnum


class VoiceBookingAttemptStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
