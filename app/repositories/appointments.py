from __future__ import annotations

from typing import Protocol

from app.models.appointment_cancellation_attempt import AppointmentCancellationAttempt
from app.repositories.scheduling import AppointmentRepository

__all__ = [
    "AppointmentCancellationAttemptRepository",
    "AppointmentRepository",
]


class AppointmentCancellationAttemptRepository(Protocol):
    def add(
        self,
        attempt: AppointmentCancellationAttempt,
    ) -> AppointmentCancellationAttempt:
        raise NotImplementedError

    def get_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> AppointmentCancellationAttempt | None:
        raise NotImplementedError
