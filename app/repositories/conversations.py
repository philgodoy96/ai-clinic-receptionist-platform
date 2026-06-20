from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from app.domain.conversations.enums import ConversationChannel
from app.models.conversations import Conversation, ConversationMessage


class ConversationRepository(Protocol):
    def add(self, conversation: Conversation) -> Conversation:
        raise NotImplementedError

    def get_by_id(self, conversation_id: UUID) -> Conversation | None:
        raise NotImplementedError

    def get_by_external_id(
        self,
        *,
        channel: ConversationChannel,
        external_conversation_id: str,
    ) -> Conversation | None:
        raise NotImplementedError

    def add_message(self, message: ConversationMessage) -> ConversationMessage:
        raise NotImplementedError

    def list_messages(
        self,
        *,
        conversation_id: UUID,
        limit: int,
    ) -> Sequence[ConversationMessage]:
        raise NotImplementedError