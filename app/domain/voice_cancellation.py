from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from app.domain.scheduling.enums import AppointmentStatus
from app.domain.voice_conversation import read_voice_context

if TYPE_CHECKING:
    from app.models.conversations import Conversation
    from app.schemas.retell_tools import CancelAppointmentToolArguments

_BLOCKED_CANCELLATION_ARGUMENT_KEYS = frozenset(
    {
        "raw_transcript",
        "transcript",
        "transcript_text",
        "voice_transcript",
    },
)

VOICE_CANCELLATION_SOURCE = "voice_cancellation"

_APPOINTMENT_NOT_OWNED_MESSAGE = (
    "I could not verify that appointment for your profile."
)
_ALREADY_CANCELLED_MESSAGE = "That appointment is already cancelled."


class VoiceCancellationError(Exception):
    """Base exception for voice appointment cancellation errors."""


class VoiceCancellationMissingConfirmationError(VoiceCancellationError):
    """Raised when explicit confirmation or confirmation text is missing."""


class PatientResolutionRequiredForCancellationError(VoiceCancellationError):
    """Raised when cancellation lacks a valid scoped patient resolution token."""


class AppointmentNotOwnedByPatientError(VoiceCancellationError):
    """Raised when the appointment does not belong to the resolved patient."""


@dataclass(frozen=True, slots=True)
class VoiceAppointmentCancellationRequest:
    patient_resolution_id: str
    appointment_id: UUID
    explicit_confirmation: bool
    confirmation_text: str | None
    provider_call_id: str
    conversation_id: UUID | None = None
    idempotency_key: str = ""
    cancellation_reason: str | None = None
    call_id: str | None = None


@dataclass(frozen=True, slots=True)
class VoiceAppointmentCancellationResult:
    appointment_id: UUID
    status: str
    cancelled_at: datetime | None
    human_readable_summary: str | None
    suggested_response_text: str
    already_cancelled: bool = False
    duplicate: bool = False

    def to_tool_result(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "appointment_id": str(self.appointment_id),
            "status": self.status,
            "already_cancelled": self.already_cancelled,
            "suggested_response_text": self.suggested_response_text,
        }
        if self.cancelled_at is not None:
            payload["cancelled_at"] = self.cancelled_at.isoformat()
        if self.human_readable_summary is not None:
            payload["human_readable_summary"] = self.human_readable_summary
        return payload


def cancellation_patient_phone_required() -> bool:
    return False


def contains_blocked_cancellation_argument_keys(arguments: dict[str, object]) -> bool:
    return any(key in _BLOCKED_CANCELLATION_ARGUMENT_KEYS for key in arguments)


def _parse_appointment_uuid(value: object | None) -> UUID | None:
    if value is None:
        return None

    normalized = str(value).strip()
    if not normalized:
        return None

    try:
        return UUID(normalized)
    except ValueError:
        return None


def collect_conversation_appointment_reference_candidates(
    *,
    voice_context: dict[str, Any],
    conversation_appointment_id: UUID | None = None,
) -> set[UUID]:
    candidates: set[UUID] = set()

    for value in (voice_context.get("appointment_id"), conversation_appointment_id):
        parsed = _parse_appointment_uuid(value)
        if parsed is not None:
            candidates.add(parsed)

    return candidates


def resolve_cancel_appointment_id(
    arguments: CancelAppointmentToolArguments,
    voice_context: dict[str, Any],
    *,
    conversation_appointment_id: UUID | None = None,
) -> UUID | None:
    argument_appointment_id = _parse_appointment_uuid(arguments.appointment_id)
    if argument_appointment_id is not None:
        return argument_appointment_id

    candidates = collect_conversation_appointment_reference_candidates(
        voice_context=voice_context,
        conversation_appointment_id=conversation_appointment_id,
    )
    if len(candidates) == 1:
        return next(iter(candidates))

    return None


def is_cancel_appointment_reference_ambiguous(
    arguments: CancelAppointmentToolArguments,
    voice_context: dict[str, Any],
    *,
    conversation_appointment_id: UUID | None = None,
) -> bool:
    if _parse_appointment_uuid(arguments.appointment_id) is not None:
        return False

    return (
        len(
            collect_conversation_appointment_reference_candidates(
                voice_context=voice_context,
                conversation_appointment_id=conversation_appointment_id,
            ),
        )
        > 1
    )


def validate_cancel_appointment_conversation_context(
    appointment_id: UUID,
    *,
    arguments: CancelAppointmentToolArguments,
    voice_context: dict[str, Any],
    conversation_appointment_id: UUID | None = None,
) -> bool:
    argument_appointment_id = _parse_appointment_uuid(arguments.appointment_id)
    context_candidates = collect_conversation_appointment_reference_candidates(
        voice_context=voice_context,
        conversation_appointment_id=conversation_appointment_id,
    )

    if argument_appointment_id is not None and context_candidates:
        return argument_appointment_id in context_candidates

    if len(context_candidates) > 1:
        return False

    return True


def _non_empty_text(value: str | None) -> bool:
    return value is not None and value.strip() != ""


def _non_empty_patient_resolution_id(value: str | None) -> bool:
    return value is not None and value.strip() != ""


def is_cancel_appointment_executable(
    arguments: CancelAppointmentToolArguments,
    *,
    voice_context: dict[str, Any] | None = None,
    conversation_appointment_id: UUID | None = None,
) -> bool:
    del voice_context, conversation_appointment_id

    if not arguments.explicit_confirmation:
        return False

    if not _non_empty_text(arguments.confirmation_text):
        return False

    if not _non_empty_patient_resolution_id(arguments.patient_resolution_id):
        return False

    return _parse_appointment_uuid(arguments.appointment_id) is not None


def read_cancel_appointment_voice_context(conversation_metadata: dict[str, Any]) -> dict[str, Any]:
    return read_voice_context(conversation_metadata)


def build_cancel_appointment_success_context_updates(*, appointment_id: UUID) -> dict[str, Any]:
    return {
        "appointment_id": str(appointment_id),
        "appointment_status": AppointmentStatus.CANCELLED.value,
        "hold_id": None,
        "availability_slot_id": None,
        "start_time": None,
        "end_time": None,
    }


def build_patient_resolution_retry_response_for_cancellation() -> dict[str, str]:
    return {
        "suggested_response_text": (
            "I need to verify your profile again before I can cancel an appointment."
        ),
    }


def resolve_cancel_appointment_id_for_conversation(
    arguments: CancelAppointmentToolArguments,
    conversation: Conversation,
) -> UUID | None:
    voice_context = read_cancel_appointment_voice_context(conversation.conversation_metadata)
    return resolve_cancel_appointment_id(
        arguments,
        voice_context,
        conversation_appointment_id=conversation.appointment_id,
    )
