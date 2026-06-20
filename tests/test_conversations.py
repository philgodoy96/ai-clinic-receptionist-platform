from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID, uuid4

import pytest

from app.domain.conversations.enums import (
    ConversationChannel,
    ConversationMessageRole,
    ConversationStatus,
)
from app.models.conversations import Conversation, ConversationMessage
from app.services.conversations import (
    ConversationCreate,
    ConversationMessageCreate,
    ConversationNotFoundError,
    ConversationService,
    InvalidConversationLimitError,
    InvalidConversationMessageError,
)


def test_create_conversation_creates_active_conversation() -> None:
    repository = FakeConversationRepository()
    service = ConversationService(repository=repository)

    conversation = service.create_conversation(
        ConversationCreate(
            channel=ConversationChannel.CHAT,
            patient_id=uuid4(),
            conversation_metadata={"source": "test"},
        ),
    )

    assert conversation in repository.conversations
    assert conversation.status == ConversationStatus.ACTIVE
    assert conversation.channel == ConversationChannel.CHAT
    assert conversation.conversation_metadata == {"source": "test"}


def test_create_conversation_reuses_existing_external_conversation() -> None:
    repository = FakeConversationRepository()
    service = ConversationService(repository=repository)
    first = service.create_conversation(
        ConversationCreate(
            channel=ConversationChannel.RETELL_VOICE,
            external_conversation_id="external-123",
        ),
    )

    second = service.create_conversation(
        ConversationCreate(
            channel=ConversationChannel.RETELL_VOICE,
            external_conversation_id="external-123",
        ),
    )

    assert second.id == first.id
    assert len(repository.conversations) == 1


def test_append_message_adds_message_to_conversation() -> None:
    repository = FakeConversationRepository()
    service = ConversationService(repository=repository)
    conversation = service.create_conversation(
        ConversationCreate(channel=ConversationChannel.CHAT),
    )

    message = service.append_message(
        ConversationMessageCreate(
            conversation_id=conversation.id,
            role=ConversationMessageRole.USER,
            content="I need an appointment.",
        ),
    )

    assert message in repository.messages
    assert message.conversation_id == conversation.id
    assert message.role == ConversationMessageRole.USER
    assert message.content == "I need an appointment."


def test_append_message_rejects_empty_content() -> None:
    repository = FakeConversationRepository()
    service = ConversationService(repository=repository)
    conversation = service.create_conversation(
        ConversationCreate(channel=ConversationChannel.CHAT),
    )

    with pytest.raises(InvalidConversationMessageError):
        service.append_message(
            ConversationMessageCreate(
                conversation_id=conversation.id,
                role=ConversationMessageRole.USER,
                content="   ",
            ),
        )


def test_append_message_requires_existing_conversation() -> None:
    repository = FakeConversationRepository()
    service = ConversationService(repository=repository)

    with pytest.raises(ConversationNotFoundError):
        service.append_message(
            ConversationMessageCreate(
                conversation_id=uuid4(),
                role=ConversationMessageRole.USER,
                content="Hello.",
            ),
        )


def test_list_messages_enforces_limit() -> None:
    repository = FakeConversationRepository()
    service = ConversationService(repository=repository)
    conversation = service.create_conversation(
        ConversationCreate(channel=ConversationChannel.CHAT),
    )

    with pytest.raises(InvalidConversationLimitError):
        service.list_messages(conversation_id=conversation.id, limit=101)


def test_close_conversation_sets_status_and_ended_at() -> None:
    repository = FakeConversationRepository()
    service = ConversationService(repository=repository)
    conversation = service.create_conversation(
        ConversationCreate(channel=ConversationChannel.CHAT),
    )

    closed = service.close_conversation(conversation_id=conversation.id)

    assert closed.status == ConversationStatus.CLOSED
    assert closed.ended_at is not None


class FakeConversationRepository:
    def __init__(self) -> None:
        self.conversations: list[Conversation] = []
        self.messages: list[ConversationMessage] = []

    def add(self, conversation: Conversation) -> Conversation:
        self.conversations.append(conversation)

        return conversation

    def get_by_id(self, conversation_id: UUID) -> Conversation | None:
        for conversation in self.conversations:
            if conversation.id == conversation_id:
                return conversation

        return None

    def get_by_external_id(
        self,
        *,
        channel: ConversationChannel,
        external_conversation_id: str,
    ) -> Conversation | None:
        for conversation in self.conversations:
            if (
                conversation.channel == channel
                and conversation.external_conversation_id == external_conversation_id
            ):
                return conversation

        return None

    def add_message(self, message: ConversationMessage) -> ConversationMessage:
        self.messages.append(message)

        return message

    def list_messages(
        self,
        *,
        conversation_id: UUID,
        limit: int,
    ) -> Sequence[ConversationMessage]:
        messages = [
            message
            for message in self.messages
            if message.conversation_id == conversation_id
        ]

        return messages[:limit]