from __future__ import annotations

from unittest.mock import patch

import pytest

from app.domain.conversations.enums import ConversationChannel, ConversationMessageRole
from app.services.chat_receptionist import (
    ChatMessageInput,
    ChatReceptionistIntent,
    ChatReceptionistReply,
    ChatReceptionistService,
    DeterministicChatResponder,
)
from app.services.conversations import ConversationCreate, ConversationService
from tests.test_conversations import FakeConversationRepository


@pytest.fixture()
def chat_service() -> tuple[ChatReceptionistService, FakeConversationRepository]:
    repository = FakeConversationRepository()
    conversations = ConversationService(repository=repository)
    service = ChatReceptionistService(conversations=conversations)

    return service, repository


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


def test_handle_message_does_not_call_llm_provider(
    chat_service: tuple[ChatReceptionistService, FakeConversationRepository],
) -> None:
    service, repository = chat_service
    responder = SpyDeterministicChatResponder()
    service_with_spy = ChatReceptionistService(
        conversations=service.conversations,
        responder=responder,
    )

    with patch("app.services.chat_receptionist.openai", create=True) as openai_mock:
        result = service_with_spy.handle_message(
            ChatMessageInput(message="Can I schedule a visit?"),
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
