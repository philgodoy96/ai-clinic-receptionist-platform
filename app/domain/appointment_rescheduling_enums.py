from __future__ import annotations

from enum import StrEnum


class AppointmentRescheduleAttemptStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    REJECTED = "rejected"
    FAILED = "failed"
