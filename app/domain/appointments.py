from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.audit.enums import AuditActorType
from app.domain.scheduling.enums import AppointmentStatus

APPOINTMENT_CANCELLATION_SOURCE = "appointment_cancellation"

_CANCELABLE_STATUSES = frozenset(
    {
        AppointmentStatus.SCHEDULED,
    },
)


class AppointmentCancellationError(Exception):
    """Base exception for appointment cancellation errors."""


class AppointmentNotFoundError(AppointmentCancellationError):
    """Raised when the requested appointment does not exist."""


class AppointmentNotCancelableError(AppointmentCancellationError):
    """Raised when the appointment cannot be cancelled."""


class AppointmentCancellationMissingConfirmationError(AppointmentCancellationError):
    """Raised when explicit confirmation was not provided."""


@dataclass(frozen=True, slots=True)
class AppointmentCancellationRequest:
    appointment_id: UUID
    explicit_confirmation: bool
    idempotency_key: str
    cancellation_reason: str | None = None
    source: str = APPOINTMENT_CANCELLATION_SOURCE
    actor_type: AuditActorType = AuditActorType.SYSTEM
    actor_id: str | None = None
    call_id: str | None = None
    conversation_id: str | None = None


@dataclass(frozen=True, slots=True)
class AppointmentCancellationResult:
    appointment_id: UUID
    patient_id: UUID
    duplicate: bool = False
    already_cancelled: bool = False
    cancelled_at: datetime | None = None


def is_appointment_cancelable(status: AppointmentStatus) -> bool:
    return status in _CANCELABLE_STATUSES
