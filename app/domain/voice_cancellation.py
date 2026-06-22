from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from app.domain.voice_conversation import read_voice_context

if TYPE_CHECKING:
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


def cancellation_patient_phone_required() -> bool:
    return False


def contains_blocked_cancellation_argument_keys(arguments: dict[str, object]) -> bool:
    return any(key in _BLOCKED_CANCELLATION_ARGUMENT_KEYS for key in arguments)


def resolve_cancel_appointment_id(
    arguments: CancelAppointmentToolArguments,
    voice_context: dict[str, Any],
) -> UUID | None:
    if arguments.appointment_id is not None and str(arguments.appointment_id).strip():
        try:
            return UUID(str(arguments.appointment_id))
        except ValueError:
            return None

    context_appointment_id = voice_context.get("appointment_id")
    if context_appointment_id is None:
        return None

    try:
        return UUID(str(context_appointment_id))
    except ValueError:
        return None


def is_cancel_appointment_executable(
    arguments: CancelAppointmentToolArguments,
    *,
    voice_context: dict[str, Any] | None = None,
) -> bool:
    if not arguments.explicit_confirmation:
        return False

    context = voice_context if voice_context is not None else {}
    return resolve_cancel_appointment_id(arguments, context) is not None


def read_cancel_appointment_voice_context(conversation_metadata: dict[str, Any]) -> dict[str, Any]:
    return read_voice_context(conversation_metadata)
