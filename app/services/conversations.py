from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.core.request_context import get_correlation_id, get_request_id
from app.domain.conversations.enums import (
    ConversationChannel,
    ConversationMessageRole,
    ConversationStatus,
)
from app.domain.voice_conversation import (
    clear_active_hold_voice_context_metadata,
    merge_last_reschedule_summary_metadata,
    merge_voice_context_metadata,
)
from app.models.conversations import Conversation, ConversationMessage
from app.repositories.conversations import ConversationRepository


class ConversationNotFoundError(ValueError):
    pass


class InvalidConversationMessageError(ValueError):
    pass


class InvalidConversationLimitError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ConversationCreate:
    channel: ConversationChannel
    patient_id: UUID | None = None
    appointment_id: UUID | None = None
    external_conversation_id: str | None = None
    call_id: str | None = None
    conversation_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ConversationMessageCreate:
    conversation_id: UUID
    role: ConversationMessageRole
    content: str
    tool_name: str | None = None
    tool_call_id: str | None = None
    message_metadata: dict[str, Any] = field(default_factory=dict)


class ConversationService:
    def __init__(self, *, repository: ConversationRepository) -> None:
        self.repository = repository

    def create_conversation(self, payload: ConversationCreate) -> Conversation:
        if payload.external_conversation_id is not None:
            existing = self.repository.get_by_external_id(
                channel=payload.channel,
                external_conversation_id=payload.external_conversation_id,
            )
            if existing is not None:
                return existing

        conversation = Conversation(
            channel=payload.channel,
            status=ConversationStatus.ACTIVE,
            patient_id=payload.patient_id,
            appointment_id=payload.appointment_id,
            external_conversation_id=payload.external_conversation_id,
            call_id=payload.call_id,
            request_id=get_request_id(),
            correlation_id=get_correlation_id(),
            conversation_metadata=payload.conversation_metadata,
        )

        return self.repository.add(conversation)

    def get_conversation(self, conversation_id: UUID) -> Conversation:
        conversation = self.repository.get_by_id(conversation_id)

        if conversation is None:
            raise ConversationNotFoundError("conversation was not found")

        return conversation

    def append_message(self, payload: ConversationMessageCreate) -> ConversationMessage:
        self.get_conversation(payload.conversation_id)

        if not payload.content.strip():
            raise InvalidConversationMessageError("message content cannot be empty")

        message = ConversationMessage(
            conversation_id=payload.conversation_id,
            role=payload.role,
            content=payload.content.strip(),
            tool_name=payload.tool_name,
            tool_call_id=payload.tool_call_id,
            message_metadata=payload.message_metadata,
        )

        return self.repository.add_message(message)

    def list_messages(
        self,
        *,
        conversation_id: UUID,
        limit: int = 50,
    ) -> list[ConversationMessage]:
        self.get_conversation(conversation_id)

        if limit < 1 or limit > 100:
            raise InvalidConversationLimitError("limit must be between 1 and 100")

        return list(
            self.repository.list_messages(
                conversation_id=conversation_id,
                limit=limit,
            ),
        )

    def update_conversation_status(
        self,
        *,
        conversation_id: UUID,
        status: ConversationStatus,
    ) -> Conversation:
        conversation = self.get_conversation(conversation_id)
        conversation.status = status
        conversation.updated_at = datetime.now(UTC)
        return self.repository.update(conversation)

    def close_conversation(
        self,
        *,
        conversation_id: UUID,
        status: ConversationStatus = ConversationStatus.CLOSED,
        ended_at: datetime | None = None,
    ) -> Conversation:
        conversation = self.get_conversation(conversation_id)
        conversation.status = status
        conversation.ended_at = ended_at or datetime.now(UTC)
        conversation.updated_at = datetime.now(UTC)

        return conversation

    def merge_conversation_metadata(
        self,
        *,
        conversation_id: UUID,
        metadata: dict[str, Any],
    ) -> Conversation:
        conversation = self.get_conversation(conversation_id)
        conversation.conversation_metadata = {
            **conversation.conversation_metadata,
            **metadata,
        }
        conversation.updated_at = datetime.now(UTC)
        return self.repository.update(conversation)

    def merge_chat_context(
        self,
        *,
        conversation_id: UUID,
        chat_context: dict[str, Any],
    ) -> Conversation:
        conversation = self.get_conversation(conversation_id)
        existing_context = conversation.conversation_metadata.get("chat_context", {})
        conversation.conversation_metadata = {
            **conversation.conversation_metadata,
            "chat_context": {
                **existing_context,
                **chat_context,
            },
        }
        conversation.updated_at = datetime.now(UTC)
        return self.repository.update(conversation)

    def merge_voice_context(
        self,
        *,
        conversation_id: UUID,
        voice_context: dict[str, Any],
    ) -> Conversation:
        conversation = self.get_conversation(conversation_id)
        conversation.conversation_metadata = merge_voice_context_metadata(
            conversation.conversation_metadata,
            voice_context,
        )
        conversation.updated_at = datetime.now(UTC)
        return self.repository.update(conversation)

    def merge_last_reschedule_summary(
        self,
        *,
        conversation_id: UUID,
        summary: dict[str, Any],
    ) -> Conversation:
        conversation = self.get_conversation(conversation_id)
        conversation.conversation_metadata = merge_last_reschedule_summary_metadata(
            conversation.conversation_metadata,
            summary,
        )
        conversation.updated_at = datetime.now(UTC)
        return self.repository.update(conversation)

    def clear_voice_active_hold(
        self,
        *,
        conversation_id: UUID,
    ) -> Conversation:
        conversation = self.get_conversation(conversation_id)
        conversation.conversation_metadata = clear_active_hold_voice_context_metadata(
            conversation.conversation_metadata,
        )
        conversation.updated_at = datetime.now(UTC)
        return self.repository.update(conversation)
