from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.appointment_rescheduling import AppointmentReschedulingService
from app.services.chat_appointment_cancellation import (
    APPOINTMENT_MANAGEMENT_AWAITING_PATIENT_IDENTITY,
    APPOINTMENT_MANAGEMENT_MODE_CANCEL,
)
from app.services.chat_appointment_rescheduling import APPOINTMENT_MANAGEMENT_MODE_RESCHEDULE
from app.services.chat_receptionist import (
    _GENERIC_SCHEDULING_FALLBACK_MESSAGE,
    ChatMessageInput,
    ChatReceptionistIntent,
    ChatReceptionistService,
    _resolve_contextual_fallback_reply,
)
from app.services.conversations import ConversationService
from app.services.post_cancellation_turn import (
    PostCancellationTurnDecision,
    classify_post_cancellation_turn,
)
from tests.test_chat_appointment_cancellation import (
    _complete_cancellation,
    _create_chat_service_with_patient,
    _wednesday_appointment,
)
from tests.test_chat_booking_identity_orchestration import (
    _book_appointment_and_get_conversation,
    _create_service,
)
from tests.test_chat_receptionist_service import create_chat_receptionist_service
from tests.test_conversations import FakeConversationRepository
from tests.test_scheduling_services import create_demo_scheduling_service


@pytest.fixture()
def scheduling_chat_service() -> tuple[ChatReceptionistService, FakeConversationRepository]:
    repository = FakeConversationRepository()
    conversations = ConversationService(repository=repository)
    scheduling = create_demo_scheduling_service()
    service = create_chat_receptionist_service(
        conversations=conversations,
        scheduling=scheduling,
    )
    return service, repository


@pytest.mark.parametrize(
    "message",
    [
        "I need to reschedule my appointment",
        "I want to reschedule",
        "Can I move my appointment?",
        "move appointment",
    ],
)
def test_reschedule_request_enters_reschedule_task_frame(
    scheduling_chat_service: tuple[ChatReceptionistService, object],
    message: str,
) -> None:
    service, _repository = scheduling_chat_service

    result = service.handle_message(ChatMessageInput(message=message))

    assert result.intent == ChatReceptionistIntent.RESCHEDULE_REQUEST
    reply = result.reply.lower()
    assert "full name" in reply
    assert "date of birth" in reply
    assert "preferred new time" not in reply
    assert "which appointment you want to move" not in reply
    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert chat_context["appointment_management_mode"] == APPOINTMENT_MANAGEMENT_MODE_RESCHEDULE
    assert (
        chat_context["appointment_management_awaiting"]
        == APPOINTMENT_MANAGEMENT_AWAITING_PATIENT_IDENTITY
    )


def test_unclear_message_during_reschedule_identity_intake_reprompts(
    scheduling_chat_service: tuple[ChatReceptionistService, object],
) -> None:
    service, _repository = scheduling_chat_service

    first = service.handle_message(
        ChatMessageInput(message="I need to reschedule my appointment"),
    )
    result = service.handle_message(
        ChatMessageInput(message="???", conversation_id=first.conversation.id),
    )

    assert result.intent == ChatReceptionistIntent.RESCHEDULE_REQUEST
    assert "full name" in result.reply.lower()
    assert "date of birth" in result.reply.lower()
    assert result.reply != _GENERIC_SCHEDULING_FALLBACK_MESSAGE
    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert chat_context["appointment_management_mode"] == APPOINTMENT_MANAGEMENT_MODE_RESCHEDULE
    assert (
        chat_context["appointment_management_awaiting"]
        == APPOINTMENT_MANAGEMENT_AWAITING_PATIENT_IDENTITY
    )


def test_resolve_contextual_fallback_for_reschedule_patient_identity() -> None:
    resolved = _resolve_contextual_fallback_reply(
        {
            "appointment_management_mode": APPOINTMENT_MANAGEMENT_MODE_RESCHEDULE,
            "appointment_management_awaiting": APPOINTMENT_MANAGEMENT_AWAITING_PATIENT_IDENTITY,
        },
    )

    assert resolved is not None
    intent, content = resolved
    assert intent == ChatReceptionistIntent.RESCHEDULE_REQUEST
    assert "full name" in content.lower()
    assert "date of birth" in content.lower()


def test_reschedule_task_frame_does_not_call_rescheduling_service(
    scheduling_chat_service: tuple[ChatReceptionistService, object],
) -> None:
    service, _repository = scheduling_chat_service

    with patch.object(
        AppointmentReschedulingService,
        "reschedule_appointment",
    ) as reschedule_appointment_mock:
        result = service.handle_message(
            ChatMessageInput(message="I need to reschedule my appointment"),
        )

    assert result.intent == ChatReceptionistIntent.RESCHEDULE_REQUEST
    reschedule_appointment_mock.assert_not_called()


def test_post_booking_reschedule_request_enters_reschedule_task_frame() -> None:
    service, tracking_booking, _scheduling = _create_service(mixed_slots=True)
    conversation = _book_appointment_and_get_conversation(service)

    result = service.handle_message(
        ChatMessageInput(
            message="I need to reschedule",
            conversation_id=conversation.id,
        ),
    )

    reply = result.reply.lower()
    assert result.intent == ChatReceptionistIntent.RESCHEDULE_REQUEST
    assert "full name" in reply
    assert "date of birth" in reply
    assert "already confirmed" not in reply
    assert "preferred new time" not in reply
    assert len(tracking_booking.book_calls) == 1
    context = result.conversation.conversation_metadata["chat_context"]
    assert context["appointment_management_mode"] == APPOINTMENT_MANAGEMENT_MODE_RESCHEDULE
    assert (
        context["appointment_management_awaiting"]
        == APPOINTMENT_MANAGEMENT_AWAITING_PATIENT_IDENTITY
    )


def test_post_cancellation_reschedule_request_enters_reschedule_task_frame() -> None:
    message = "I need to reschedule"
    assert (
        classify_post_cancellation_turn(message=message).decision
        is PostCancellationTurnDecision.RESCHEDULE_REQUEST
    )

    service, _repository, patient, emily, _reed = _create_chat_service_with_patient()
    _cancel_result, conversation_id, _appointment = _complete_cancellation(
        service,
        appointments=[
            _wednesday_appointment(
                patient_id=patient.id,
                doctor_id=emily.id,
                specialty_id=emily.specialty_id,
            ),
        ],
    )

    result = service.handle_message(
        ChatMessageInput(message=message, conversation_id=conversation_id),
    )

    assert result.intent == ChatReceptionistIntent.RESCHEDULE_REQUEST
    assert "full name" in result.reply.lower()
    assert "date of birth" in result.reply.lower()
    context = result.conversation.conversation_metadata["chat_context"]
    assert context["appointment_management_mode"] == APPOINTMENT_MANAGEMENT_MODE_RESCHEDULE
    assert (
        context["appointment_management_awaiting"]
        == APPOINTMENT_MANAGEMENT_AWAITING_PATIENT_IDENTITY
    )


def test_cancellation_routing_unchanged_when_not_rescheduling(
    scheduling_chat_service: tuple[ChatReceptionistService, object],
) -> None:
    service, _repository = scheduling_chat_service

    result = service.handle_message(
        ChatMessageInput(message="I want to cancel my appointment"),
    )

    assert result.intent == ChatReceptionistIntent.CANCEL_REQUEST
    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert chat_context["appointment_management_mode"] == APPOINTMENT_MANAGEMENT_MODE_CANCEL
    assert (
        chat_context["appointment_management_awaiting"]
        == APPOINTMENT_MANAGEMENT_AWAITING_PATIENT_IDENTITY
    )
