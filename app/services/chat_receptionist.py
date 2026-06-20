from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import UUID

from app.domain.conversations.enums import (
    ConversationChannel,
    ConversationMessageRole,
)
from app.models.conversations import Conversation, ConversationMessage
from app.services.conversations import (
    ConversationCreate,
    ConversationMessageCreate,
    ConversationService,
)


class ChatReceptionistIntent(StrEnum):
    GREETING = "greeting"
    APPOINTMENT_REQUEST = "appointment_request"
    CANCEL_REQUEST = "cancel_request"
    RESCHEDULE_REQUEST = "reschedule_request"
    EMERGENCY = "emergency"
    FALLBACK = "fallback"


@dataclass(frozen=True, slots=True)
class ChatReceptionistReply:
    intent: ChatReceptionistIntent
    content: str


@dataclass(frozen=True, slots=True)
class ChatMessageInput:
    message: str
    conversation_id: UUID | None = None
    patient_id: UUID | None = None
    conversation_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ChatMessageResult:
    conversation: Conversation
    user_message: ConversationMessage
    assistant_message: ConversationMessage
    intent: ChatReceptionistIntent
    reply: str


class DeterministicChatResponder:
    def generate_reply(self, *, message: str) -> ChatReceptionistReply:
        normalized_message = message.lower()

        if self._contains_any(
            normalized_message,
            ["emergency", "urgent", "chest pain", "can't breathe", "cannot breathe"],
        ):
            return ChatReceptionistReply(
                intent=ChatReceptionistIntent.EMERGENCY,
                content=(
                    "If this is a medical emergency, please call emergency services "
                    "or go to the nearest emergency room."
                ),
            )

        if self._contains_any(normalized_message, ["cancel", "cancellation"]):
            return ChatReceptionistReply(
                intent=ChatReceptionistIntent.CANCEL_REQUEST,
                content=(
                    "I can help with cancellation requests. Please provide your name "
                    "and the appointment date so we can locate the booking."
                ),
            )

        if self._contains_any(normalized_message, ["reschedule", "move appointment"]):
            return ChatReceptionistReply(
                intent=ChatReceptionistIntent.RESCHEDULE_REQUEST,
                content=(
                    "I can help with rescheduling. Please tell me which appointment "
                    "you want to move and your preferred new time."
                ),
            )

        if self._contains_any(
            normalized_message,
            [
                "appointment",
                "schedule",
                "book",
                "doctor",
                "dermatology",
                "cardiology",
                "primary care",
            ],
        ):
            return ChatReceptionistReply(
                intent=ChatReceptionistIntent.APPOINTMENT_REQUEST,
                content=(
                    "I can help with appointment scheduling. Please tell me the "
                    "specialty or doctor you would like to see."
                ),
            )

        if self._contains_any(
            normalized_message,
            ["hello", "hi", "hey", "good morning", "good afternoon"],
        ):
            return ChatReceptionistReply(
                intent=ChatReceptionistIntent.GREETING,
                content=(
                    "Hello, I am the clinic receptionist assistant. I can help with "
                    "appointments, cancellations, and rescheduling."
                ),
            )

        return ChatReceptionistReply(
            intent=ChatReceptionistIntent.FALLBACK,
            content=(
                "I can help with clinic scheduling questions. Please tell me whether "
                "you want to book, cancel, or reschedule an appointment."
            ),
        )

    def _contains_any(self, value: str, options: list[str]) -> bool:
        return any(option in value for option in options)


class ChatReceptionistService:
    def __init__(
        self,
        *,
        conversations: ConversationService,
        responder: DeterministicChatResponder | None = None,
    ) -> None:
        self.conversations = conversations
        self.responder = responder or DeterministicChatResponder()

    def handle_message(self, payload: ChatMessageInput) -> ChatMessageResult:
        conversation = self._get_or_create_conversation(payload)
        user_message = self.conversations.append_message(
            ConversationMessageCreate(
                conversation_id=conversation.id,
                role=ConversationMessageRole.USER,
                content=payload.message,
                message_metadata={
                    "source": "chat_api",
                },
            ),
        )
        reply = self.responder.generate_reply(message=payload.message)
        assistant_message = self.conversations.append_message(
            ConversationMessageCreate(
                conversation_id=conversation.id,
                role=ConversationMessageRole.ASSISTANT,
                content=reply.content,
                message_metadata={
                    "source": "chat_api",
                    "intent": reply.intent.value,
                },
            ),
        )

        return ChatMessageResult(
            conversation=conversation,
            user_message=user_message,
            assistant_message=assistant_message,
            intent=reply.intent,
            reply=reply.content,
        )

    def _get_or_create_conversation(
        self,
        payload: ChatMessageInput,
    ) -> Conversation:
        if payload.conversation_id is not None:
            return self.conversations.get_conversation(payload.conversation_id)

        return self.conversations.create_conversation(
            ConversationCreate(
                channel=ConversationChannel.CHAT,
                patient_id=payload.patient_id,
                conversation_metadata={
                    "source": "chat_api",
                    **payload.conversation_metadata,
                },
            ),
        )