from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

import pytest

from app.domain.conversations.enums import ConversationChannel, ConversationMessageRole
from app.models.scheduling import Doctor
from app.services.chat_receptionist import (
    ChatMessageInput,
    ChatReceptionistIntent,
    ChatReceptionistReply,
    ChatReceptionistService,
    DeterministicChatResponder,
)
from app.services.conversations import ConversationCreate, ConversationService
from app.services.scheduling import SchedulingService
from tests.test_conversations import FakeConversationRepository
from tests.test_scheduling_services import create_doctor, create_service, create_specialty


@pytest.fixture()
def chat_service() -> tuple[ChatReceptionistService, FakeConversationRepository]:
    repository = FakeConversationRepository()
    conversations = ConversationService(repository=repository)
    scheduling = create_service()
    service = ChatReceptionistService(
        conversations=conversations,
        scheduling=scheduling,
    )

    return service, repository


@pytest.fixture()
def scheduling_chat_service() -> ChatReceptionistService:
    dermatology = create_specialty(name="Dermatology")
    cardiology = create_specialty(name="Cardiology")
    dermatology_doctor = Doctor(
        id=uuid4(),
        specialty_id=dermatology.id,
        full_name="Dr. Jane Adams",
        email="jane.adams@example-clinic.test",
        phone_number="+1-555-0101",
        is_active=True,
    )
    cardiology_doctor = create_doctor(
        specialty_id=cardiology.id,
        doctor_id=uuid4(),
    )
    scheduling = create_service(
        specialties=[dermatology, cardiology],
        doctors=[dermatology_doctor, cardiology_doctor],
    )
    conversations = ConversationService(repository=FakeConversationRepository())

    return ChatReceptionistService(
        conversations=conversations,
        scheduling=scheduling,
    )


def test_handle_message_creates_conversation_when_conversation_id_missing(
    chat_service: tuple[ChatReceptionistService, FakeConversationRepository],
) -> None:
    service, repository = chat_service

    result = service.handle_message(ChatMessageInput(message="Hello there"))

    assert len(repository.conversations) == 1
    assert result.conversation.id == repository.conversations[0].id
    assert result.conversation.channel == ConversationChannel.CHAT


def test_handle_message_reuses_existing_conversation_when_conversation_id_provided(
    chat_service: tuple[ChatReceptionistService, FakeConversationRepository],
) -> None:
    service, repository = chat_service
    existing = service.conversations.create_conversation(
        ConversationCreate(channel=ConversationChannel.CHAT),
    )

    result = service.handle_message(
        ChatMessageInput(
            message="Follow up question",
            conversation_id=existing.id,
        ),
    )

    assert len(repository.conversations) == 1
    assert result.conversation.id == existing.id


def test_user_message_is_persisted_with_role_user(
    chat_service: tuple[ChatReceptionistService, FakeConversationRepository],
) -> None:
    service, repository = chat_service

    result = service.handle_message(ChatMessageInput(message="I need help"))

    assert result.user_message.role == ConversationMessageRole.USER
    assert result.user_message.content == "I need help"
    assert result.user_message in repository.messages


def test_assistant_message_is_persisted_with_role_assistant(
    chat_service: tuple[ChatReceptionistService, FakeConversationRepository],
) -> None:
    service, repository = chat_service

    result = service.handle_message(ChatMessageInput(message="Hello"))

    assert result.assistant_message.role == ConversationMessageRole.ASSISTANT
    assert result.assistant_message.content == result.reply
    assert result.assistant_message in repository.messages


def test_appointment_request_message_returns_intent_appointment_request(
    chat_service: tuple[ChatReceptionistService, FakeConversationRepository],
) -> None:
    service, _repository = chat_service

    result = service.handle_message(
        ChatMessageInput(message="I would like to book an appointment."),
    )

    assert result.intent == ChatReceptionistIntent.APPOINTMENT_REQUEST
    assert "appointment scheduling" in result.reply.lower()


def test_emergency_message_returns_intent_emergency_and_safe_guidance(
    chat_service: tuple[ChatReceptionistService, FakeConversationRepository],
) -> None:
    service, _repository = chat_service

    result = service.handle_message(
        ChatMessageInput(message="I have chest pain and this is an emergency."),
    )

    assert result.intent == ChatReceptionistIntent.EMERGENCY
    assert "medical emergency" in result.reply.lower()
    assert "emergency services" in result.reply.lower()


def test_emergency_message_does_not_call_scheduling(
    scheduling_chat_service: ChatReceptionistService,
) -> None:
    service = scheduling_chat_service
    scheduling = service.scheduling

    with patch.object(
        SchedulingService,
        "list_specialties",
        wraps=scheduling.list_specialties,
    ) as list_specialties_mock:
        result = service.handle_message(
            ChatMessageInput(message="This is an emergency and I have chest pain."),
        )

    assert result.intent == ChatReceptionistIntent.EMERGENCY
    list_specialties_mock.assert_not_called()


def test_cancel_request_takes_priority_over_doctor_listing(
    scheduling_chat_service: ChatReceptionistService,
) -> None:
    result = scheduling_chat_service.handle_message(
        ChatMessageInput(message="Please cancel my appointment with the doctors office."),
    )

    assert result.intent == ChatReceptionistIntent.CANCEL_REQUEST


def test_list_specialties_returns_real_specialties(
    scheduling_chat_service: ChatReceptionistService,
) -> None:
    result = scheduling_chat_service.handle_message(
        ChatMessageInput(message="What specialties do you offer?"),
    )

    assert result.intent == ChatReceptionistIntent.LIST_SPECIALTIES
    assert "Dermatology" in result.reply
    assert "Cardiology" in result.reply


def test_list_doctors_returns_real_doctors(
    scheduling_chat_service: ChatReceptionistService,
) -> None:
    result = scheduling_chat_service.handle_message(
        ChatMessageInput(message="Which doctors are available?"),
    )

    assert result.intent == ChatReceptionistIntent.LIST_DOCTORS
    assert "Dr. Jane Adams" in result.reply
    assert "Dr. Sarah Mitchell" in result.reply


def test_specialty_name_in_message_lists_doctors_for_specialty(
    scheduling_chat_service: ChatReceptionistService,
) -> None:
    result = scheduling_chat_service.handle_message(
        ChatMessageInput(message="I need to see someone in dermatology."),
    )

    assert result.intent == ChatReceptionistIntent.SPECIALTY_DOCTORS
    assert "Dr. Jane Adams" in result.reply
    assert "Dr. Sarah Mitchell" not in result.reply
    assert result.assistant_message.message_metadata["matched_specialty_name"] == "Dermatology"
    assert result.assistant_message.message_metadata["intent"] == "specialty_doctors"


def test_specialty_with_no_doctors_returns_safe_message(
    chat_service: tuple[ChatReceptionistService, FakeConversationRepository],
) -> None:
    service, _repository = chat_service
    empty_specialty = create_specialty(name="Pediatrics")
    service_with_empty_specialty = ChatReceptionistService(
        conversations=service.conversations,
        scheduling=create_service(specialties=[empty_specialty]),
    )

    result = service_with_empty_specialty.handle_message(
        ChatMessageInput(message="I am looking for pediatrics."),
    )

    assert result.intent == ChatReceptionistIntent.SPECIALTY_DOCTORS
    assert "Pediatrics" in result.reply
    assert "do not currently have any doctors" in result.reply.lower()


def test_handle_message_does_not_call_llm_provider(
    chat_service: tuple[ChatReceptionistService, FakeConversationRepository],
) -> None:
    service, repository = chat_service
    responder = SpyDeterministicChatResponder()
    service_with_spy = ChatReceptionistService(
        conversations=service.conversations,
        scheduling=service.scheduling,
        responder=responder,
    )

    with patch("app.services.chat_receptionist.openai", create=True) as openai_mock:
        result = service_with_spy.handle_message(
            ChatMessageInput(message="Hello"),
        )

    assert responder.call_count == 1
    assert openai_mock.call_count == 0
    assert result.conversation.appointment_id is None
    assert len(repository.conversations) == 1


class SpyDeterministicChatResponder(DeterministicChatResponder):
    def __init__(self) -> None:
        super().__init__()
        self.call_count = 0

    def generate_reply(self, *, message: str) -> ChatReceptionistReply:
        self.call_count += 1

        return super().generate_reply(message=message)
