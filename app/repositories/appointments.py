from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from app.domain.appointment_rescheduling_enums import AppointmentRescheduleAttemptStatus
from app.models.appointment_cancellation_attempt import AppointmentCancellationAttempt
from app.models.appointment_reschedule_attempt import AppointmentRescheduleAttempt
from app.repositories.scheduling import AppointmentRepository

__all__ = [
    "AppointmentCancellationAttemptRepository",
    "AppointmentRepository",
    "AppointmentRescheduleAttemptRepository",
    "RescheduleAttemptCreateResult",
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


@dataclass(frozen=True, slots=True)
class RescheduleAttemptCreateResult:
    attempt: AppointmentRescheduleAttempt
    created: bool


class AppointmentRescheduleAttemptRepository(Protocol):
    def get_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> AppointmentRescheduleAttempt | None:
        raise NotImplementedError

    def create_attempt(
        self,
        *,
        idempotency_key: str,
        appointment_id: UUID,
    ) -> RescheduleAttemptCreateResult:
        raise NotImplementedError

    def mark_succeeded(
        self,
        attempt: AppointmentRescheduleAttempt,
        *,
        new_appointment_id: UUID,
    ) -> AppointmentRescheduleAttempt:
        raise NotImplementedError

    def mark_failed_or_rejected(
        self,
        attempt: AppointmentRescheduleAttempt,
        *,
        error_code: str,
        status: AppointmentRescheduleAttemptStatus = (AppointmentRescheduleAttemptStatus.REJECTED),
    ) -> AppointmentRescheduleAttempt:
        raise NotImplementedError
