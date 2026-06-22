from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any
from uuid import UUID

from app.domain.voice_conversation import read_voice_context

if TYPE_CHECKING:
    from app.schemas.retell_tools import BookAppointmentToolArguments


class VoiceBookingConfirmationError(Exception):
    """Base exception for voice booking confirmation errors."""


class VoiceBookingMissingContextError(VoiceBookingConfirmationError):
    """Raised when required voice booking context is missing."""


class VoiceBookingMissingConfirmationError(VoiceBookingConfirmationError):
    """Raised when explicit confirmation was not provided."""


class VoiceBookingMissingIdentityError(VoiceBookingConfirmationError):
    """Raised when patient identity is incomplete."""


class VoiceBookingMissingHoldError(VoiceBookingConfirmationError):
    """Raised when no active hold is available for booking."""


class VoiceBookingExpiredHoldError(VoiceBookingConfirmationError):
    """Raised when the active hold is missing or expired."""


class VoiceBookingHoldOwnershipError(VoiceBookingConfirmationError):
    """Raised when the hold does not belong to the voice conversation."""


class VoiceBookingPatientNotFoundError(VoiceBookingConfirmationError):
    """Raised when patient identity does not match a patient record."""


class VoiceBookingQuotaExceededError(VoiceBookingConfirmationError):
    """Raised when public demo booking quotas are exceeded."""


class VoiceBookingTemporaryFailureError(VoiceBookingConfirmationError):
    """Raised when booking fails due to a recoverable backend error."""

    def __init__(self, *, error_code: str, message: str) -> None:
        self.error_code = error_code
        self.message = message
        super().__init__(message)


_BLOCKED_BOOKING_ARGUMENT_KEYS = frozenset(
    {
        "raw_transcript",
        "transcript",
        "transcript_text",
        "voice_transcript",
    },
)

VOICE_BOOKING_SOURCE = "voice_booking_confirmation"


@dataclass(frozen=True, slots=True)
class VoiceBookingPatientIdentity:
    patient_name: str
    patient_date_of_birth: date
    patient_email: str
    patient_phone: str | None = None


@dataclass(frozen=True, slots=True)
class VoiceBookingConfirmationRequest:
    provider: str
    provider_call_id: str
    tool_call_id: str | None
    voice_call_id: UUID
    conversation_id: UUID
    hold_id: str | None
    slot_id: UUID | str | None
    patient_name: str
    patient_date_of_birth: date
    patient_email: str
    patient_phone: str | None
    explicit_confirmation: bool
    confirmation_text: str | None
    idempotency_key: str
    client_ip: str | None = None
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class VoiceBookingConfirmationResult:
    appointment_id: UUID
    patient_id: UUID
    availability_slot_id: UUID
    hold_id: str
    duplicate: bool = False
    confirmation_email_created: bool = False


def booking_patient_phone_required() -> bool:
    return False


def is_book_appointment_executable(arguments: BookAppointmentToolArguments) -> bool:
    if not arguments.explicit_confirmation:
        return False

    return is_voice_booking_patient_identity_complete(
        VoiceBookingPatientIdentity(
            patient_name=arguments.patient_name,
            patient_date_of_birth=arguments.patient_date_of_birth,
            patient_email=arguments.patient_email,
            patient_phone=arguments.patient_phone,
        ),
    )


def is_voice_booking_patient_identity_complete(identity: VoiceBookingPatientIdentity) -> bool:
    if not identity.patient_name.strip():
        return False

    if not identity.patient_email.strip():
        return False

    if booking_patient_phone_required() and not _non_empty(identity.patient_phone):
        return False

    return True


def contains_blocked_booking_argument_keys(arguments: dict[str, object]) -> bool:
    return any(key in _BLOCKED_BOOKING_ARGUMENT_KEYS for key in arguments)


def resolve_voice_booking_owner_id(
    *,
    provider_call_id: str,
    conversation_id: UUID,
) -> str:
    normalized_call_id = provider_call_id.strip()
    if normalized_call_id:
        return normalized_call_id

    return str(conversation_id)


def validate_voice_context_hold_reference(
    *,
    conversation_metadata: dict[str, Any],
    hold_id: str,
) -> None:
    voice_context = read_voice_context(conversation_metadata)
    context_hold_id = voice_context.get("hold_id")

    if context_hold_id is None:
        return

    if str(context_hold_id) != hold_id:
        msg = "hold_id does not match active voice conversation hold"
        raise VoiceBookingHoldOwnershipError(msg)


def build_voice_booking_success_context_updates(*, appointment_id: UUID) -> dict[str, Any]:
    return {
        "appointment_id": str(appointment_id),
        "hold_id": None,
        "availability_slot_id": None,
        "start_time": None,
        "end_time": None,
    }


def _non_empty(value: str | None) -> bool:
    return value is not None and value.strip() != ""
