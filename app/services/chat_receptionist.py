from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID

from app.domain.conversations.enums import (
    ConversationChannel,
    ConversationMessageRole,
)
from app.models.conversations import Conversation, ConversationMessage
from app.models.scheduling import AvailabilitySlot, Doctor, Specialty
from app.services.conversations import (
    ConversationCreate,
    ConversationMessageCreate,
    ConversationService,
)
from app.services.scheduling import SchedulingService

_ISO_DATE_PATTERN = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
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
_AVAILABILITY_KEYWORDS = [
    "available",
    "availability",
    "openings",
    "times",
    "slots",
    "appointments on",
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
    AVAILABILITY_REQUEST = "availability_request"
    AVAILABILITY_MISSING_DATE = "availability_missing_date"
    AVAILABILITY_MISSING_DOCTOR = "availability_missing_doctor"
    AVAILABILITY_RESULTS = "availability_results"
    AVAILABILITY_NO_SLOTS = "availability_no_slots"
    INVALID_DATE = "invalid_date"
    FALLBACK = "fallback"


_AVAILABILITY_CONTEXT_INTENTS = frozenset(
    {
        ChatReceptionistIntent.AVAILABILITY_MISSING_DATE,
        ChatReceptionistIntent.AVAILABILITY_MISSING_DOCTOR,
        ChatReceptionistIntent.AVAILABILITY_RESULTS,
        ChatReceptionistIntent.AVAILABILITY_NO_SLOTS,
        ChatReceptionistIntent.INVALID_DATE,
    }
)


@dataclass(frozen=True, slots=True)
class ChatReceptionistReply:
    intent: ChatReceptionistIntent
    content: str
    matched_specialty_id: UUID | None = None
    matched_specialty_name: str | None = None
    chat_context_updates: dict[str, Any] = field(default_factory=dict)
    availability_checked: bool = False


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
        reply = self._generate_reply(payload.message, conversation)
        if reply.chat_context_updates:
            conversation = self.conversations.merge_chat_context(
                conversation_id=conversation.id,
                chat_context=reply.chat_context_updates,
            )

        assistant_metadata: dict[str, Any] = {
            "source": "chat_api",
            "intent": reply.intent.value,
        }
        if reply.matched_specialty_id is not None:
            assistant_metadata["matched_specialty_id"] = str(reply.matched_specialty_id)
        if reply.matched_specialty_name is not None:
            assistant_metadata["matched_specialty_name"] = reply.matched_specialty_name
        if reply.chat_context_updates or reply.intent in _AVAILABILITY_CONTEXT_INTENTS:
            assistant_metadata["chat_context"] = conversation.conversation_metadata.get(
                "chat_context",
                {},
            )
        if reply.availability_checked:
            assistant_metadata["availability_checked"] = True

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

    def _generate_reply(
        self,
        message: str,
        conversation: Conversation,
    ) -> ChatReceptionistReply:
        normalized_message = message.lower()
        existing_context = dict(conversation.conversation_metadata.get("chat_context", {}))

        if self.responder._contains_any(normalized_message, _EMERGENCY_KEYWORDS):
            return self.responder.generate_reply(message=message)

        if self.responder._contains_any(normalized_message, _CANCEL_KEYWORDS):
            return self.responder.generate_reply(message=message)

        if self.responder._contains_any(normalized_message, _RESCHEDULE_KEYWORDS):
            return self.responder.generate_reply(message=message)

        if self._has_invalid_date_pattern(message):
            return ChatReceptionistReply(
                intent=ChatReceptionistIntent.INVALID_DATE,
                content=(
                    "That date does not look valid. "
                    "Please provide a date in YYYY-MM-DD format."
                ),
            )

        context_updates = self._extract_context_updates(normalized_message, message)
        merged_context = {**existing_context, **context_updates}

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
        completing_availability = self._should_complete_availability_from_context(
            merged_context=merged_context,
            context_updates=context_updates,
        )
        if (
            matched_specialty is not None
            and not self._is_availability_request(normalized_message)
            and not self._should_enter_availability_flow(
                normalized_message,
                merged_context,
            )
            and not completing_availability
        ):
            doctors = self.scheduling.list_doctors(specialty_id=matched_specialty.id)
            return ChatReceptionistReply(
                intent=ChatReceptionistIntent.SPECIALTY_DOCTORS,
                content=self._format_doctors(
                    doctors,
                    specialty_name=matched_specialty.name,
                ),
                matched_specialty_id=matched_specialty.id,
                matched_specialty_name=matched_specialty.name,
                chat_context_updates=context_updates,
            )

        if self._is_availability_request(normalized_message) or (
            self._should_enter_availability_flow(
                normalized_message,
                merged_context,
            )
        ) or completing_availability:
            return self._handle_availability_flow(
                merged_context=merged_context,
                context_updates=context_updates,
            )

        if self.responder._contains_any(normalized_message, _APPOINTMENT_KEYWORDS):
            return ChatReceptionistReply(
                intent=ChatReceptionistIntent.APPOINTMENT_REQUEST,
                content=(
                    "I can help with appointment scheduling. Please tell me the "
                    "specialty or doctor you would like to see."
                ),
                chat_context_updates=context_updates,
            )

        return self.responder.generate_reply(message=message)

    def _extract_context_updates(
        self,
        normalized_message: str,
        message: str,
    ) -> dict[str, Any]:
        context_updates: dict[str, Any] = {}

        extracted_date = self._extract_date(message)
        if extracted_date is not None:
            context_updates["requested_date"] = extracted_date.isoformat()

        matched_specialty = self._match_specialty_in_message(normalized_message)
        if matched_specialty is not None:
            context_updates["selected_specialty_id"] = str(matched_specialty.id)
            context_updates["selected_specialty_name"] = matched_specialty.name
            specialty_doctors = self.scheduling.list_doctors(
                specialty_id=matched_specialty.id,
            )
            if len(specialty_doctors) == 1:
                context_updates["selected_doctor_id"] = str(specialty_doctors[0].id)
                context_updates["selected_doctor_name"] = specialty_doctors[0].full_name

        matched_doctor = self._match_doctor_in_message(normalized_message)
        if matched_doctor is not None:
            context_updates["selected_doctor_id"] = str(matched_doctor.id)
            context_updates["selected_doctor_name"] = matched_doctor.full_name

        return context_updates

    def _handle_availability_flow(
        self,
        *,
        merged_context: dict[str, Any],
        context_updates: dict[str, Any],
    ) -> ChatReceptionistReply:
        selected_doctor_id = merged_context.get("selected_doctor_id")
        requested_date = merged_context.get("requested_date")

        if not selected_doctor_id:
            content = self._format_missing_doctor_prompt(merged_context)
            return ChatReceptionistReply(
                intent=ChatReceptionistIntent.AVAILABILITY_MISSING_DOCTOR,
                content=content,
                chat_context_updates=context_updates,
            )

        if not requested_date:
            doctor_name = merged_context.get("selected_doctor_name", "the selected doctor")
            return ChatReceptionistReply(
                intent=ChatReceptionistIntent.AVAILABILITY_MISSING_DATE,
                content=(
                    f"Please provide the date you would like to check for {doctor_name} "
                    "in YYYY-MM-DD format."
                ),
                chat_context_updates=context_updates,
            )

        slots = self._query_availability(
            doctor_id=UUID(str(selected_doctor_id)),
            requested_date=date.fromisoformat(str(requested_date)),
        )
        doctor_name = str(merged_context.get("selected_doctor_name", "the selected doctor"))

        if slots:
            return ChatReceptionistReply(
                intent=ChatReceptionistIntent.AVAILABILITY_RESULTS,
                content=self._format_availability_slots(
                    slots,
                    doctor_name=doctor_name,
                    requested_date=str(requested_date),
                ),
                chat_context_updates=context_updates,
                availability_checked=True,
            )

        return ChatReceptionistReply(
            intent=ChatReceptionistIntent.AVAILABILITY_NO_SLOTS,
            content=(
                f"I did not find open times for {doctor_name} on {requested_date}. "
                "Please try another date or doctor."
            ),
            chat_context_updates=context_updates,
            availability_checked=True,
        )

    def _format_missing_doctor_prompt(self, merged_context: dict[str, Any]) -> str:
        specialty_id = merged_context.get("selected_specialty_id")
        specialty_name = merged_context.get("selected_specialty_name")

        if specialty_id is not None:
            doctors = self.scheduling.list_doctors(specialty_id=UUID(str(specialty_id)))
            if doctors:
                doctor_names = self._join_names([doctor.full_name for doctor in doctors])
                return (
                    f"Please choose a doctor for {specialty_name}: {doctor_names}. "
                    "Tell me the doctor name so I can check availability."
                )

        return (
            "Please tell me which doctor or specialty you would like to check. "
            "You can ask for our doctor list or mention a specialty."
        )

    def _query_availability(
        self,
        *,
        doctor_id: UUID,
        requested_date: date,
    ) -> Sequence[AvailabilitySlot]:
        start_from = datetime(
            requested_date.year,
            requested_date.month,
            requested_date.day,
            tzinfo=UTC,
        )
        start_to = start_from + timedelta(days=1)

        return self.scheduling.check_availability(
            doctor_id=doctor_id,
            start_from=start_from,
            start_to=start_to,
        )

    def _format_availability_slots(
        self,
        slots: Sequence[AvailabilitySlot],
        *,
        doctor_name: str,
        requested_date: str,
    ) -> str:
        shown_slots = list(slots[:5])
        times = [slot.start_time.strftime("%H:%M") for slot in shown_slots]
        times_text = self._join_names(times)
        suffix = ""

        if len(slots) > 5:
            suffix = f" There are {len(slots) - 5} more openings available."

        return (
            f"Open times for {doctor_name} on {requested_date}: {times_text}.{suffix} "
            "You can choose a time, and booking will be handled in a later step."
        )

    def _extract_date(self, message: str) -> date | None:
        match = _ISO_DATE_PATTERN.search(message)

        if match is None:
            return None

        try:
            return date.fromisoformat(match.group(1))
        except ValueError:
            return None

    def _has_invalid_date_pattern(self, message: str) -> bool:
        match = _ISO_DATE_PATTERN.search(message)

        if match is None:
            return False

        try:
            date.fromisoformat(match.group(1))
        except ValueError:
            return True

        return False

    def _is_availability_request(self, normalized_message: str) -> bool:
        return self.responder._contains_any(normalized_message, _AVAILABILITY_KEYWORDS)

    def _should_enter_availability_flow(
        self,
        normalized_message: str,
        merged_context: dict[str, Any],
    ) -> bool:
        if not self.responder._contains_any(normalized_message, _APPOINTMENT_KEYWORDS):
            return False

        return bool(
            merged_context.get("selected_doctor_id")
            or merged_context.get("requested_date"),
        )

    def _should_complete_availability_from_context(
        self,
        *,
        merged_context: dict[str, Any],
        context_updates: dict[str, Any],
    ) -> bool:
        has_doctor = bool(merged_context.get("selected_doctor_id"))
        has_date = bool(merged_context.get("requested_date"))

        if not has_doctor or not has_date:
            return False

        relevant_updates = {
            "requested_date",
            "selected_doctor_id",
            "selected_specialty_id",
        }

        return bool(relevant_updates & context_updates.keys())

    def _match_doctor_in_message(self, normalized_message: str) -> Doctor | None:
        doctors = sorted(
            self.scheduling.list_doctors(),
            key=lambda doctor: len(doctor.full_name),
            reverse=True,
        )

        for doctor in doctors:
            if self._doctor_name_in_message(normalized_message, doctor.full_name):
                return doctor

        return None

    def _doctor_name_in_message(self, normalized_message: str, full_name: str) -> bool:
        normalized_name = full_name.replace(".", "").lower()

        if normalized_name in normalized_message:
            return True

        name_terms = normalized_name.split()

        return all(term in normalized_message for term in name_terms)

    def _match_specialty_in_message(self, normalized_message: str) -> Specialty | None:
        specialties = sorted(
            self.scheduling.list_specialties(),
            key=lambda specialty: len(specialty.name),
            reverse=True,
        )

        for specialty in specialties:
            if any(
                term in normalized_message
                for term in self._specialty_match_terms(specialty)
            ):
                return specialty

        return None

    def _specialty_match_terms(self, specialty: Specialty) -> list[str]:
        name = specialty.name.lower()
        terms = [name]

        if name.endswith("ology"):
            terms.append(f"{name.removesuffix('ology')}ologist")

        return terms

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
