from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest

from app.services.chat_receptionist import (
    ChatMessageInput,
    ChatMessageResult,
    ChatReceptionistIntent,
    ChatReceptionistService,
)
from app.services.conversations import ConversationService
from app.services.scheduling import SchedulingService
from tests.test_chat_receptionist_service import (
    FakeAppointmentHoldService,
    _create_hold_service,
    create_chat_receptionist_service,
)
from tests.test_conversations import FakeConversationRepository
from tests.test_scheduling_services import (
    FakeAppointmentRepository,
    create_demo_scheduling_service_with_emily_afternoon_july_availability,
    create_demo_scheduling_service_with_emily_july_availability,
)

RevisionChatService = tuple[
    ChatReceptionistService,
    FakeAppointmentHoldService,
    SchedulingService,
]


def _build_service(scheduling: SchedulingService) -> RevisionChatService:
    repository = FakeConversationRepository()
    conversations = ConversationService(repository=repository)
    hold_service = _create_hold_service()
    service = create_chat_receptionist_service(
        conversations=conversations,
        scheduling=scheduling,
        hold_service=hold_service,
    )
    return service, hold_service, scheduling


@pytest.fixture()
def revision_chat_service() -> RevisionChatService:
    return _build_service(
        create_demo_scheduling_service_with_emily_july_availability(patients=[]),
    )


@pytest.fixture()
def single_slot_chat_service() -> RevisionChatService:
    return _build_service(
        create_demo_scheduling_service_with_emily_afternoon_july_availability(patients=[]),
    )


def _offer_slots(service: ChatReceptionistService) -> UUID:
    availability = service.handle_message(
        ChatMessageInput(message="Dr. Emily Carter on 2026-07-02"),
    )
    chat_context = availability.conversation.conversation_metadata["chat_context"]
    assert chat_context.get("offered_slots")
    assert not chat_context.get("hold_id")
    return availability.conversation.id


def _chat_context(result: ChatMessageResult) -> dict[str, Any]:
    chat_context = result.conversation.conversation_metadata.get("chat_context", {})
    assert isinstance(chat_context, dict)
    return chat_context


def _assert_no_identity_intake(result: ChatMessageResult) -> None:
    reply = result.reply.lower()
    assert result.intent not in {
        ChatReceptionistIntent.PATIENT_IDENTITY_PARTIAL,
        ChatReceptionistIntent.PATIENT_IDENTITY_COMPLETE,
        ChatReceptionistIntent.BOOKING_HOLD_MISSING,
    }
    assert "noted your details" not in reply
    assert "date of birth" not in reply
    chat_context = _chat_context(result)
    patient_identity = chat_context.get("patient_identity") or {}
    assert not patient_identity.get("full_name")


def _assert_nothing_booked(
    result: ChatMessageResult,
    scheduling: SchedulingService,
) -> None:
    appointments = scheduling.appointments
    assert isinstance(appointments, FakeAppointmentRepository)
    assert appointments.appointments == []
    assert result.booking_confirmed is False
    assert result.assistant_message.message_metadata.get("appointment_id") is None


def test_offered_slot_date_revision_asks_for_time_not_identity(
    revision_chat_service: RevisionChatService,
) -> None:
    service, _hold_service, scheduling = revision_chat_service
    conversation_id = _offer_slots(service)

    result = service.handle_message(
        ChatMessageInput(
            message="On second thought, I'd like for Wednesday",
            conversation_id=conversation_id,
        ),
    )

    _assert_no_identity_intake(result)
    _assert_nothing_booked(result, scheduling)
    reply = result.reply.lower()
    assert "wednesday" in reply
    assert "time" in reply
    assert "hold it first" not in reply
    # The now-stale Tuesday options are cleared so the next turn is not trapped.
    chat_context = _chat_context(result)
    assert not chat_context.get("offered_slots")
    assert chat_context.get("requested_exact_time") is None


def test_offered_slot_time_revision_is_not_identity(
    revision_chat_service: RevisionChatService,
) -> None:
    service, _hold_service, scheduling = revision_chat_service
    conversation_id = _offer_slots(service)

    result = service.handle_message(
        ChatMessageInput(message="Actually, 14", conversation_id=conversation_id),
    )

    # "14" is not one of the offered times, so this is handled as a scheduling
    # revision/clarification rather than patient identity, and nothing is booked.
    _assert_no_identity_intake(result)
    _assert_nothing_booked(result, scheduling)
    assert result.intent == ChatReceptionistIntent.APPOINTMENT_REQUEST


def test_offered_slot_time_revision_selects_matching_offered_slot(
    revision_chat_service: RevisionChatService,
) -> None:
    service, hold_service, _scheduling = revision_chat_service
    conversation_id = _offer_slots(service)

    result = service.handle_message(
        ChatMessageInput(message="Actually, 10:30", conversation_id=conversation_id),
    )

    # A revised time that matches an offered slot selects/holds it via the
    # existing slot-selection behavior.
    assert result.intent == ChatReceptionistIntent.HOLD_CREATED
    chat_context = _chat_context(result)
    hold_id = chat_context.get("hold_id")
    assert isinstance(hold_id, str) and hold_id


def test_revision_phrase_is_not_parsed_as_patient_name(
    revision_chat_service: RevisionChatService,
) -> None:
    service, _hold_service, _scheduling = revision_chat_service

    identity = service.parse_patient_identity("On second thought, I'd like for Wednesday")

    assert identity.full_name is None


@pytest.mark.parametrize(
    "message",
    [
        "Actually, I want Wednesday",
        "Wait, I meant 14",
        "I changed my mind",
        "Never mind",
        "Don't book it",
    ],
)
def test_more_revision_and_denial_phrases_are_not_names(
    revision_chat_service: RevisionChatService,
    message: str,
) -> None:
    service, _hold_service, _scheduling = revision_chat_service

    assert service.parse_patient_identity(message).full_name is None


def test_valid_identity_still_parses(
    revision_chat_service: RevisionChatService,
) -> None:
    service, _hold_service, _scheduling = revision_chat_service

    identity = service.parse_patient_identity("John Miller, 1985-04-12")

    assert identity.full_name == "John Miller"
    assert identity.date_of_birth == "1985-04-12"


def test_identity_after_revision_is_not_blocked_by_polluted_state(
    revision_chat_service: RevisionChatService,
) -> None:
    service, _hold_service, _scheduling = revision_chat_service
    conversation_id = _offer_slots(service)

    revision = service.handle_message(
        ChatMessageInput(
            message="On second thought, I'd like for Wednesday",
            conversation_id=conversation_id,
        ),
    )

    # No bogus identity was persisted, so a later correct identity cannot be
    # shadowed by existing-wins merge semantics.
    chat_context = _chat_context(revision)
    assert not (chat_context.get("patient_identity") or {}).get("full_name")

    identity = service.parse_patient_identity("John Miller, 1985-04-12")
    assert identity.full_name == "John Miller"
    assert identity.date_of_birth == "1985-04-12"


def test_yes_after_unresolved_revision_does_not_loop_on_hold_first(
    revision_chat_service: RevisionChatService,
) -> None:
    service, _hold_service, scheduling = revision_chat_service
    conversation_id = _offer_slots(service)

    service.handle_message(
        ChatMessageInput(message="Actually, 14", conversation_id=conversation_id),
    )

    result = service.handle_message(
        ChatMessageInput(message="yes", conversation_id=conversation_id),
    )

    assert result.intent != ChatReceptionistIntent.BOOKING_HOLD_MISSING
    assert "hold it first before confirming a booking" not in result.reply.lower()
    _assert_nothing_booked(result, scheduling)


def test_offered_single_slot_yes_still_creates_hold(
    single_slot_chat_service: RevisionChatService,
) -> None:
    service, _hold_service, _scheduling = single_slot_chat_service
    conversation_id = _offer_slots(service)

    result = service.handle_message(
        ChatMessageInput(message="yes", conversation_id=conversation_id),
    )

    assert result.intent == ChatReceptionistIntent.HOLD_CREATED
    chat_context = _chat_context(result)
    assert isinstance(chat_context.get("hold_id"), str)
