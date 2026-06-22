from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from app.domain.voice_conversation import read_voice_context

if TYPE_CHECKING:
    from app.models.conversations import Conversation
    from app.schemas.retell_tools import RescheduleAppointmentToolArguments

_BLOCKED_RESCHEDULE_ARGUMENT_KEYS = frozenset(
    {
        "raw_transcript",
        "transcript",
        "transcript_text",
        "voice_transcript",
    },
)

VOICE_RESCHEDULING_SOURCE = "voice_rescheduling"


def reschedule_patient_phone_required() -> bool:
    return False


def contains_blocked_reschedule_argument_keys(arguments: dict[str, object]) -> bool:
    return any(key in _BLOCKED_RESCHEDULE_ARGUMENT_KEYS for key in arguments)


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


def resolve_reschedule_original_appointment_id(
    arguments: RescheduleAppointmentToolArguments,
    voice_context: dict[str, Any],
    *,
    conversation_appointment_id: UUID | None = None,
) -> UUID | None:
    argument_appointment_id = _parse_appointment_uuid(arguments.original_appointment_id)
    if argument_appointment_id is not None:
        return argument_appointment_id

    candidates = collect_conversation_appointment_reference_candidates(
        voice_context=voice_context,
        conversation_appointment_id=conversation_appointment_id,
    )
    if len(candidates) == 1:
        return next(iter(candidates))

    return None


def is_reschedule_appointment_reference_ambiguous(
    arguments: RescheduleAppointmentToolArguments,
    voice_context: dict[str, Any],
    *,
    conversation_appointment_id: UUID | None = None,
) -> bool:
    if _parse_appointment_uuid(arguments.original_appointment_id) is not None:
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


def validate_reschedule_appointment_conversation_context(
    appointment_id: UUID,
    *,
    arguments: RescheduleAppointmentToolArguments,
    voice_context: dict[str, Any],
    conversation_appointment_id: UUID | None = None,
) -> bool:
    argument_appointment_id = _parse_appointment_uuid(arguments.original_appointment_id)
    context_candidates = collect_conversation_appointment_reference_candidates(
        voice_context=voice_context,
        conversation_appointment_id=conversation_appointment_id,
    )

    if argument_appointment_id is not None and context_candidates:
        return argument_appointment_id in context_candidates

    if len(context_candidates) > 1:
        return False

    return True


def is_reschedule_appointment_executable(
    arguments: RescheduleAppointmentToolArguments,
    *,
    voice_context: dict[str, Any] | None = None,
    conversation_appointment_id: UUID | None = None,
) -> bool:
    if not arguments.explicit_confirmation:
        return False

    context = voice_context if voice_context is not None else {}
    if is_reschedule_appointment_reference_ambiguous(
        arguments,
        context,
        conversation_appointment_id=conversation_appointment_id,
    ):
        return False

    appointment_id = resolve_reschedule_original_appointment_id(
        arguments,
        context,
        conversation_appointment_id=conversation_appointment_id,
    )
    if appointment_id is None:
        return False

    return validate_reschedule_appointment_conversation_context(
        appointment_id,
        arguments=arguments,
        voice_context=context,
        conversation_appointment_id=conversation_appointment_id,
    )


def read_reschedule_appointment_voice_context(
    conversation_metadata: dict[str, Any],
) -> dict[str, Any]:
    return read_voice_context(conversation_metadata)


def resolve_reschedule_original_appointment_id_for_conversation(
    arguments: RescheduleAppointmentToolArguments,
    conversation: Conversation,
) -> UUID | None:
    voice_context = read_reschedule_appointment_voice_context(conversation.conversation_metadata)
    return resolve_reschedule_original_appointment_id(
        arguments,
        voice_context,
        conversation_appointment_id=conversation.appointment_id,
    )
