from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any
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
    MISSING_TARGET = "missing_target"
    MISSING_HOLD_OWNER = "missing_hold_owner"
    HOLD_EXPIRED = "hold_expired"
    SLOT_NOT_FOUND = "slot_not_found"
    SLOT_UNAVAILABLE = "slot_unavailable"
    SLOT_ALREADY_BOOKED = "slot_already_booked"


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


class AppointmentReschedulingMissingTargetError(AppointmentReschedulingError):
    """Raised when neither hold_id nor new_slot_id was provided."""

    def __init__(self, message: str = "hold_id or new_slot_id is required") -> None:
        super().__init__(
            message,
            failure_code=AppointmentReschedulingFailureCode.MISSING_TARGET,
        )


class AppointmentReschedulingMissingHoldOwnerError(AppointmentReschedulingError):
    """Raised when hold_id is provided without owner_id."""

    def __init__(self, message: str = "owner_id is required when hold_id is provided") -> None:
        super().__init__(
            message,
            failure_code=AppointmentReschedulingFailureCode.MISSING_HOLD_OWNER,
        )


class AppointmentReschedulingHoldExpiredError(AppointmentReschedulingError):
    """Raised when the active hold is missing or expired."""

    def __init__(self, message: str = "appointment hold was not found or expired") -> None:
        super().__init__(
            message,
            failure_code=AppointmentReschedulingFailureCode.HOLD_EXPIRED,
        )


class AppointmentReschedulingSlotNotFoundError(AppointmentReschedulingError):
    """Raised when the target availability slot does not exist."""

    def __init__(self, message: str = "availability slot was not found") -> None:
        super().__init__(
            message,
            failure_code=AppointmentReschedulingFailureCode.SLOT_NOT_FOUND,
        )


class AppointmentReschedulingSlotUnavailableError(AppointmentReschedulingError):
    """Raised when the target availability slot is not available."""

    def __init__(self, message: str = "availability slot is not available") -> None:
        super().__init__(
            message,
            failure_code=AppointmentReschedulingFailureCode.SLOT_UNAVAILABLE,
        )


class AppointmentReschedulingSlotAlreadyBookedError(AppointmentReschedulingError):
    """Raised when the doctor/start_time already has a scheduled appointment."""

    def __init__(
        self,
        message: str = "doctor already has a scheduled appointment at this time",
    ) -> None:
        super().__init__(
            message,
            failure_code=AppointmentReschedulingFailureCode.SLOT_ALREADY_BOOKED,
        )


@dataclass(frozen=True, slots=True)
class AppointmentReschedulingRequest:
    appointment_id: UUID
    explicit_confirmation: bool
    idempotency_key: str
    hold_id: UUID | None = None
    new_slot_id: UUID | None = None
    owner_id: str | None = None
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
    confirmation_email_created: bool = False


def is_appointment_reschedulable(status: AppointmentStatus) -> bool:
    return status in _RESCHEDULABLE_STATUSES


def validate_appointment_rescheduling_request(
    request: AppointmentReschedulingRequest,
) -> None:
    if not request.idempotency_key.strip():
        raise AppointmentReschedulingInvalidIdempotencyKeyError()

    if not request.explicit_confirmation:
        raise AppointmentReschedulingMissingConfirmationError()

    if request.hold_id is None and request.new_slot_id is None:
        raise AppointmentReschedulingMissingTargetError()

    if request.hold_id is not None and not (request.owner_id or "").strip():
        raise AppointmentReschedulingMissingHoldOwnerError()


def normalize_rescheduling_reason(reason: str | None) -> str | None:
    if reason is None:
        return None

    normalized = reason.strip()
    if not normalized:
        return None

    return normalized


def build_reschedule_success_context_updates(
    *,
    appointment_id: UUID,
    original_appointment_id: UUID,
    availability_slot_id: UUID,
    start_time: str,
    end_time: str,
) -> dict[str, Any]:
    return {
        "appointment_id": str(appointment_id),
        "appointment_status": AppointmentStatus.SCHEDULED.value,
        "rescheduled_from_appointment_id": str(original_appointment_id),
        "availability_slot_id": str(availability_slot_id),
        "start_time": start_time,
        "end_time": end_time,
        "hold_id": None,
    }
