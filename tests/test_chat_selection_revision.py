from __future__ import annotations

from uuid import UUID

from app.services.chat_receptionist import (
    ChatMessageInput,
    ChatReceptionistIntent,
    ChatReceptionistService,
)
from tests.test_chat_receptionist_service import (
    _afternoon_dermatology_scheduling,
    _create_availability_guidance_service,
)


def _reach_held_slot_at_15(
    service: ChatReceptionistService,
) -> tuple[UUID, str]:
    availability = service.handle_message(
        ChatMessageInput(message="Dr. Emily Carter on 2026-07-02"),
    )
    conversation_id = availability.conversation.id

    held = service.handle_message(
        ChatMessageInput(
            message="I want the one at 15:00",
            conversation_id=conversation_id,
        ),
    )
    assert held.intent == ChatReceptionistIntent.HOLD_CREATED
    assert "15:00" in held.reply
    chat_context = held.conversation.conversation_metadata["chat_context"]
    assert chat_context["hold_id"]
    return conversation_id, chat_context["hold_id"]


def test_booking_hold_revision_replaces_selected_slot() -> None:
    service, _repository, hold_service = _create_availability_guidance_service(
        _afternoon_dermatology_scheduling(),
    )
    conversation_id, original_hold_id = _reach_held_slot_at_15(service)

    result = service.handle_message(
        ChatMessageInput(
            message="On second thought, I want the one at 14",
            conversation_id=conversation_id,
        ),
    )

    assert result.intent == ChatReceptionistIntent.HOLD_CREATED
    assert "14:00" in result.reply
    assert len(hold_service.create_hold_calls) == 2
    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert chat_context["hold_id"] != original_hold_id
    assert chat_context["selected_start_time"] == "2026-07-02T18:00:00+00:00"
    offered_slots = chat_context["offered_slots"]
    assert len(offered_slots) == 2


def test_booking_hold_plain_yes_still_confirms_current_slot() -> None:
    service, _repository, _hold_service = _create_availability_guidance_service(
        _afternoon_dermatology_scheduling(),
    )
    conversation_id, _original_hold_id = _reach_held_slot_at_15(service)

    result = service.handle_message(
        ChatMessageInput(message="yes", conversation_id=conversation_id),
    )

    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert chat_context["hold_id"]
    assert chat_context["selected_start_time"] == "2026-07-02T19:00:00+00:00"
    assert result.intent != ChatReceptionistIntent.HOLD_CREATED
    assert "14:00" not in result.reply or "15:00" in result.reply


def test_booking_hold_plain_no_does_not_select_different_slot() -> None:
    service, _repository, hold_service = _create_availability_guidance_service(
        _afternoon_dermatology_scheduling(),
    )
    conversation_id, original_hold_id = _reach_held_slot_at_15(service)

    result = service.handle_message(
        ChatMessageInput(message="no", conversation_id=conversation_id),
    )

    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert chat_context["hold_id"] == original_hold_id
    assert chat_context["selected_start_time"] == "2026-07-02T19:00:00+00:00"
    assert len(hold_service.create_hold_calls) == 1
    assert result.intent != ChatReceptionistIntent.HOLD_CREATED


def test_human_escalation_wins_over_slot_revision_during_hold() -> None:
    service, _repository, hold_service = _create_availability_guidance_service(
        _afternoon_dermatology_scheduling(),
    )
    conversation_id, _original_hold_id = _reach_held_slot_at_15(service)

    result = service.handle_message(
        ChatMessageInput(message="Human please", conversation_id=conversation_id),
    )

    assert result.intent == ChatReceptionistIntent.HUMAN_ESCALATION_REQUESTED
    assert len(hold_service.create_hold_calls) == 1


def test_intent_switch_wins_over_slot_revision_during_hold() -> None:
    service, _repository, hold_service = _create_availability_guidance_service(
        _afternoon_dermatology_scheduling(),
    )
    conversation_id, _original_hold_id = _reach_held_slot_at_15(service)

    result = service.handle_message(
        ChatMessageInput(
            message="Actually I want to cancel instead",
            conversation_id=conversation_id,
        ),
    )

    assert result.intent == ChatReceptionistIntent.CANCEL_REQUEST
    assert len(hold_service.create_hold_calls) == 1
    assert "cancel" in result.reply.lower()
