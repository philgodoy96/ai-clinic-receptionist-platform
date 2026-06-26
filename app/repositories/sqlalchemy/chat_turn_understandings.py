from __future__ import annotations

from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models.chat_turn_understandings import ChatTurnUnderstanding


class SQLAlchemyChatTurnUnderstandingRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, record: ChatTurnUnderstanding) -> ChatTurnUnderstanding:
        self.session.add(record)
        self.session.flush()

        return record

    def add_best_effort(self, record: ChatTurnUnderstanding) -> ChatTurnUnderstanding | None:
        try:
            with self.session.begin_nested():
                self.session.add(record)
                self.session.flush()
        except Exception:
            return None

        return record

    def get_by_id(self, record_id: UUID) -> ChatTurnUnderstanding | None:
        return self.session.get(ChatTurnUnderstanding, record_id)

    def get_by_user_message_id(self, user_message_id: UUID) -> ChatTurnUnderstanding | None:
        statement = select(ChatTurnUnderstanding).where(
            ChatTurnUnderstanding.user_message_id == user_message_id,
        )

        return self.session.scalars(statement).first()

    def list_by_conversation_id(
        self,
        conversation_id: UUID,
        *,
        limit: int = 50,
    ) -> list[ChatTurnUnderstanding]:
        statement = (
            select(ChatTurnUnderstanding)
            .where(ChatTurnUnderstanding.conversation_id == conversation_id)
            .order_by(
                desc(ChatTurnUnderstanding.created_at),
                desc(ChatTurnUnderstanding.id),
            )
            .limit(limit)
        )

        return list(self.session.scalars(statement).all())
