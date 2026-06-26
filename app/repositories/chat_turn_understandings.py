from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.models.chat_turn_understandings import ChatTurnUnderstanding


class ChatTurnUnderstandingRepository(Protocol):
    def add(self, record: ChatTurnUnderstanding) -> ChatTurnUnderstanding:
        raise NotImplementedError

    def add_best_effort(self, record: ChatTurnUnderstanding) -> ChatTurnUnderstanding | None:
        raise NotImplementedError

    def get_by_id(self, record_id: UUID) -> ChatTurnUnderstanding | None:
        raise NotImplementedError

    def get_by_user_message_id(self, user_message_id: UUID) -> ChatTurnUnderstanding | None:
        raise NotImplementedError

    def list_by_conversation_id(
        self,
        conversation_id: UUID,
        *,
        limit: int = 50,
    ) -> list[ChatTurnUnderstanding]:
        raise NotImplementedError
