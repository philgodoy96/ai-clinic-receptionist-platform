from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from app.domain.audit.enums import AuditActorType
from app.domain.scheduling.enums import AppointmentStatus

APPOINTMENT_RESCHEDULING_SOURCE = "appointment_rescheduling"

_RESCHEDULABLE_STATUSES = frozenset(
    {
        AppointmentStatus.SCHEDULED,
        AppointmentStatus.RESCHEDULED,
    },
)


class AppointmentReschedulingFailureCode(StrEnum):
    APPOINTMENT_NOT_FOUND = "appointment_not_found"
    APPOINTMENT_NOT_RESCHEDULABLE = "appointment_not_reschedulable"
    MISSING_CONFIRMATION = "missing_confirmation"
    INVALID_IDEMPOTENCY_KEY = "invalid_idempotency_key"


class AppointmentReschedulingError(Exception):
    """Base exception for appointment rescheduling errors."""

    def __init__(
        self,
        message: str,
        *,
        failure_code: AppointmentReschedulingFailureCode,
    ) -> None:
        self.failure_code = failure_code
        self.message = message
        super().__init__(message)


class AppointmentReschedulingNotFoundError(AppointmentReschedulingError):
    """Raised when the requested appointment does not exist."""

    def __init__(self, message: str = "appointment was not found") -> None:
        super().__init__(
            message,
            failure_code=AppointmentReschedulingFailureCode.APPOINTMENT_NOT_FOUND,
        )


class AppointmentReschedulingNotReschedulableError(AppointmentReschedulingError):
    """Raised when the appointment cannot be rescheduled."""

    def __init__(self, message: str = "appointment cannot be rescheduled") -> None:
        super().__init__(
            message,
            failure_code=AppointmentReschedulingFailureCode.APPOINTMENT_NOT_RESCHEDULABLE,
        )


class AppointmentReschedulingMissingConfirmationError(AppointmentReschedulingError):
    """Raised when explicit confirmation was not provided."""

    def __init__(self, message: str = "explicit_confirmation is required") -> None:
        super().__init__(
            message,
            failure_code=AppointmentReschedulingFailureCode.MISSING_CONFIRMATION,
        )


class AppointmentReschedulingInvalidIdempotencyKeyError(AppointmentReschedulingError):
    """Raised when the idempotency key is missing or blank."""

    def __init__(self, message: str = "idempotency_key is required") -> None:
        super().__init__(
            message,
            failure_code=AppointmentReschedulingFailureCode.INVALID_IDEMPOTENCY_KEY,
        )


@dataclass(frozen=True, slots=True)
class AppointmentReschedulingRequest:
    appointment_id: UUID
    availability_slot_id: UUID
    explicit_confirmation: bool
    idempotency_key: str
    rescheduling_reason: str | None = None
    source: str = APPOINTMENT_RESCHEDULING_SOURCE
    actor_type: AuditActorType = AuditActorType.SYSTEM
    actor_id: str | None = None
    call_id: str | None = None
    conversation_id: str | None = None


@dataclass(frozen=True, slots=True)
class AppointmentReschedulingResult:
    original_appointment_id: UUID
    new_appointment_id: UUID
    patient_id: UUID
    duplicate: bool = False
    already_rescheduled: bool = False


def is_appointment_reschedulable(status: AppointmentStatus) -> bool:
    return status in _RESCHEDULABLE_STATUSES


def validate_appointment_rescheduling_request(
    request: AppointmentReschedulingRequest,
) -> None:
    if not request.idempotency_key.strip():
        raise AppointmentReschedulingInvalidIdempotencyKeyError()

    if not request.explicit_confirmation:
        raise AppointmentReschedulingMissingConfirmationError()


def normalize_rescheduling_reason(reason: str | None) -> str | None:
    if reason is None:
        return None

    normalized = reason.strip()
    if not normalized:
        return None

    return normalized
