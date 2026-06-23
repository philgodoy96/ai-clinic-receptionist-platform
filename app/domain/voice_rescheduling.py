from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from app.domain.appointment_rescheduling import build_reschedule_success_context_updates
from app.domain.voice_conversation import read_voice_context

if TYPE_CHECKING:
    from app.models.conversations import Conversation
    from app.schemas.retell_tools import RescheduleAppointmentToolArguments
    from app.services.conversations import ConversationService

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


def resolve_reschedule_target_reference(
    arguments: RescheduleAppointmentToolArguments,
) -> tuple[UUID | None, UUID | None, str | None]:
    hold_text = arguments.hold_id.strip() if arguments.hold_id else ""
    slot_text = str(arguments.new_slot_id).strip() if arguments.new_slot_id is not None else ""

    has_hold = bool(hold_text)
    has_slot = bool(slot_text)

    if not has_hold and not has_slot:
        return None, None, "new_slot_required"

    parsed_hold: UUID | None = None
    parsed_slot: UUID | None = None

    if has_hold:
        try:
            parsed_hold = UUID(hold_text)
        except ValueError:
            return None, None, "active_hold_required"

    if has_slot:
        try:
            parsed_slot = UUID(slot_text)
        except ValueError:
            return None, None, "new_slot_required"

    return parsed_hold, parsed_slot, None


def build_last_reschedule_success_summary(
    *,
    original_appointment_id: UUID,
    new_appointment_id: UUID,
    availability_slot_id: UUID,
    start_time: str,
    end_time: str,
    duplicate: bool = False,
) -> dict[str, object]:
    return {
        "status": "succeeded",
        "original_appointment_id": str(original_appointment_id),
        "new_appointment_id": str(new_appointment_id),
        "appointment_status": "scheduled",
        "availability_slot_id": str(availability_slot_id),
        "start_time": start_time,
        "end_time": end_time,
        "duplicate": duplicate,
    }


_RECOVERABLE_RESCHEDULE_FAILURE_CODES = frozenset(
    {
        "active_hold_required",
        "appointment_hold_expired",
        "slot_unavailable",
        "slot_already_booked",
        "new_slot_required",
    },
)


def is_recoverable_reschedule_failure(error_code: str) -> bool:
    return error_code in _RECOVERABLE_RESCHEDULE_FAILURE_CODES


def apply_reschedule_success_to_conversation(
    conversations: ConversationService,
    *,
    conversation_id: UUID,
    original_appointment_id: UUID,
    new_appointment_id: UUID,
    availability_slot_id: UUID,
    start_time: str,
    end_time: str,
    duplicate: bool = False,
) -> Conversation:
    conversations.clear_voice_active_hold(conversation_id=conversation_id)
    conversations.merge_voice_context(
        conversation_id=conversation_id,
        voice_context=build_reschedule_success_context_updates(
            appointment_id=new_appointment_id,
            original_appointment_id=original_appointment_id,
            availability_slot_id=availability_slot_id,
            start_time=start_time,
            end_time=end_time,
        ),
    )
    return conversations.merge_last_reschedule_summary(
        conversation_id=conversation_id,
        summary=build_last_reschedule_success_summary(
            original_appointment_id=original_appointment_id,
            new_appointment_id=new_appointment_id,
            availability_slot_id=availability_slot_id,
            start_time=start_time,
            end_time=end_time,
            duplicate=duplicate,
        ),
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
