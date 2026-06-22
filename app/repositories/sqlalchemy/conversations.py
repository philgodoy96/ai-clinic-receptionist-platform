from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.domain.conversations.enums import ConversationChannel
from app.models.conversations import Conversation, ConversationMessage


class SQLAlchemyConversationRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, conversation: Conversation) -> Conversation:
        self.session.add(conversation)
        self.session.flush()

        return conversation

    def get_by_id(self, conversation_id: UUID) -> Conversation | None:
        return self.session.get(Conversation, conversation_id)

    def get_by_external_id(
        self,
        *,
        channel: ConversationChannel,
        external_conversation_id: str,
    ) -> Conversation | None:
        statement = select(Conversation).where(
            Conversation.channel == channel,
            Conversation.external_conversation_id == external_conversation_id,
        )

        return self.session.scalars(statement).first()

    def get_by_call_id(self, call_id: str) -> Conversation | None:
        statement = select(Conversation).where(Conversation.call_id == call_id)

        return self.session.scalars(statement).first()

    def add_message(self, message: ConversationMessage) -> ConversationMessage:
        self.session.add(message)
        self.session.flush()

        return message

    def list_messages(
        self,
        *,
        conversation_id: UUID,
        limit: int,
    ) -> Sequence[ConversationMessage]:
        statement = (
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == conversation_id)
            .order_by(desc(ConversationMessage.created_at), desc(ConversationMessage.id))
            .limit(limit)
        )

        return list(self.session.scalars(statement).all())

    def update(self, conversation: Conversation) -> Conversation:
        self.session.add(conversation)
        self.session.flush()

        return conversation