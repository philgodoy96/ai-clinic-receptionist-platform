from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import UUID

from app.domain.conversations.enums import (
    ConversationChannel,
    ConversationMessageRole,
)
from app.models.conversations import Conversation, ConversationMessage
from app.models.scheduling import Doctor, Specialty
from app.services.conversations import (
    ConversationCreate,
    ConversationMessageCreate,
    ConversationService,
)
from app.services.scheduling import SchedulingService

_EMERGENCY_KEYWORDS = [
    "emergency",
    "urgent",
    "chest pain",
    "can't breathe",
    "cannot breathe",
]
_CANCEL_KEYWORDS = ["cancel", "cancellation"]
_RESCHEDULE_KEYWORDS = ["reschedule", "move appointment"]
_SPECIALTY_LIST_KEYWORDS = [
    "specialties",
    "specialty",
    "services",
    "what do you offer",
]
_DOCTOR_LIST_KEYWORDS = [
    "doctors",
    "physicians",
    "clinicians",
    "providers",
]
_APPOINTMENT_KEYWORDS = [
    "appointment",
    "schedule",
    "book",
]


class ChatReceptionistIntent(StrEnum):
    GREETING = "greeting"
    APPOINTMENT_REQUEST = "appointment_request"
    CANCEL_REQUEST = "cancel_request"
    RESCHEDULE_REQUEST = "reschedule_request"
    EMERGENCY = "emergency"
    LIST_SPECIALTIES = "list_specialties"
    LIST_DOCTORS = "list_doctors"
    SPECIALTY_DOCTORS = "specialty_doctors"
    FALLBACK = "fallback"


@dataclass(frozen=True, slots=True)
class ChatReceptionistReply:
    intent: ChatReceptionistIntent
    content: str
    matched_specialty_id: UUID | None = None
    matched_specialty_name: str | None = None


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

        if self._contains_any(normalized_message, _EMERGENCY_KEYWORDS):
            return ChatReceptionistReply(
                intent=ChatReceptionistIntent.EMERGENCY,
                content=(
                    "If this is a medical emergency, please call emergency services "
                    "or go to the nearest emergency room."
                ),
            )

        if self._contains_any(normalized_message, _CANCEL_KEYWORDS):
            return ChatReceptionistReply(
                intent=ChatReceptionistIntent.CANCEL_REQUEST,
                content=(
                    "I can help with cancellation requests. Please provide your name "
                    "and the appointment date so we can locate the booking."
                ),
            )

        if self._contains_any(normalized_message, _RESCHEDULE_KEYWORDS):
            return ChatReceptionistReply(
                intent=ChatReceptionistIntent.RESCHEDULE_REQUEST,
                content=(
                    "I can help with rescheduling. Please tell me which appointment "
                    "you want to move and your preferred new time."
                ),
            )

        if self._contains_any(normalized_message, _APPOINTMENT_KEYWORDS):
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
        scheduling: SchedulingService,
        responder: DeterministicChatResponder | None = None,
    ) -> None:
        self.conversations = conversations
        self.scheduling = scheduling
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
        reply = self._generate_reply(payload.message)
        assistant_metadata: dict[str, Any] = {
            "source": "chat_api",
            "intent": reply.intent.value,
        }
        if reply.matched_specialty_id is not None:
            assistant_metadata["matched_specialty_id"] = str(reply.matched_specialty_id)
        if reply.matched_specialty_name is not None:
            assistant_metadata["matched_specialty_name"] = reply.matched_specialty_name

        assistant_message = self.conversations.append_message(
            ConversationMessageCreate(
                conversation_id=conversation.id,
                role=ConversationMessageRole.ASSISTANT,
                content=reply.content,
                message_metadata=assistant_metadata,
            ),
        )

        return ChatMessageResult(
            conversation=conversation,
            user_message=user_message,
            assistant_message=assistant_message,
            intent=reply.intent,
            reply=reply.content,
        )

    def _generate_reply(self, message: str) -> ChatReceptionistReply:
        normalized_message = message.lower()

        if self.responder._contains_any(normalized_message, _EMERGENCY_KEYWORDS):
            return self.responder.generate_reply(message=message)

        if self.responder._contains_any(normalized_message, _CANCEL_KEYWORDS):
            return self.responder.generate_reply(message=message)

        if self.responder._contains_any(normalized_message, _RESCHEDULE_KEYWORDS):
            return self.responder.generate_reply(message=message)

        if self.responder._contains_any(normalized_message, _SPECIALTY_LIST_KEYWORDS):
            specialties = self.scheduling.list_specialties()
            return ChatReceptionistReply(
                intent=ChatReceptionistIntent.LIST_SPECIALTIES,
                content=self._format_specialties(specialties),
            )

        if self.responder._contains_any(normalized_message, _DOCTOR_LIST_KEYWORDS):
            doctors = self.scheduling.list_doctors()
            return ChatReceptionistReply(
                intent=ChatReceptionistIntent.LIST_DOCTORS,
                content=self._format_doctors(doctors),
            )

        matched_specialty = self._match_specialty_in_message(normalized_message)
        if matched_specialty is not None:
            doctors = self.scheduling.list_doctors(specialty_id=matched_specialty.id)
            return ChatReceptionistReply(
                intent=ChatReceptionistIntent.SPECIALTY_DOCTORS,
                content=self._format_doctors(
                    doctors,
                    specialty_name=matched_specialty.name,
                ),
                matched_specialty_id=matched_specialty.id,
                matched_specialty_name=matched_specialty.name,
            )

        if self.responder._contains_any(normalized_message, _APPOINTMENT_KEYWORDS):
            return ChatReceptionistReply(
                intent=ChatReceptionistIntent.APPOINTMENT_REQUEST,
                content=(
                    "I can help with appointment scheduling. Please tell me the "
                    "specialty or doctor you would like to see."
                ),
            )

        return self.responder.generate_reply(message=message)

    def _match_specialty_in_message(self, normalized_message: str) -> Specialty | None:
        specialties = sorted(
            self.scheduling.list_specialties(),
            key=lambda specialty: len(specialty.name),
            reverse=True,
        )

        for specialty in specialties:
            if specialty.name.lower() in normalized_message:
                return specialty

        return None

    def _format_specialties(self, specialties: Sequence[Specialty]) -> str:
        if not specialties:
            return (
                "We do not currently have any specialties listed. "
                "Please contact the clinic for assistance."
            )

        names = [specialty.name for specialty in specialties]

        if len(names) == 1:
            return f"We offer the following specialty: {names[0]}."

        return f"We offer the following specialties: {self._join_names(names)}."

    def _format_doctors(
        self,
        doctors: Sequence[Doctor],
        *,
        specialty_name: str | None = None,
    ) -> str:
        if not doctors:
            if specialty_name is not None:
                return (
                    f"We do not currently have any doctors listed for {specialty_name}. "
                    "Please contact the clinic for assistance."
                )

            return (
                "We do not currently have any doctors listed. "
                "Please contact the clinic for assistance."
            )

        names = [doctor.full_name for doctor in doctors]

        if specialty_name is not None:
            prefix = f"The following doctors are available for {specialty_name}: "
        else:
            prefix = "Our available doctors are: "

        if len(names) == 1:
            return f"{prefix}{names[0]}."

        return f"{prefix}{self._join_names(names)}."

    def _join_names(self, names: Sequence[str]) -> str:
        if len(names) == 2:
            return f"{names[0]} and {names[1]}"

        return ", ".join(names[:-1]) + f", and {names[-1]}"

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
