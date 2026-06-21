from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID

from app.ai.receptionist_output import ReceptionistLLMIntent
from app.ai.reliability import LLMFailureReason
from app.domain.conversations.enums import (
    ConversationChannel,
    ConversationMessageRole,
    ConversationStatus,
)
from app.domain.human_escalations import (
    HumanEscalationReason,
    HumanEscalationSource,
)
from app.models.conversations import Conversation, ConversationMessage
from app.models.scheduling import AvailabilitySlot, Doctor, Patient, Specialty
from app.services.appointment_booking import (
    AppointmentBookingRequest,
    AppointmentBookingService,
    AppointmentSlotAlreadyBookedError,
    BookingAvailabilitySlotNotFoundError,
    BookingAvailabilitySlotUnavailableError,
    BookingDoctorNotFoundError,
    BookingPatientNotFoundError,
)
from app.services.appointment_holds import (
    AppointmentHoldMismatchError,
    AppointmentHoldNotFoundError,
    AppointmentHoldOwnershipError,
    AppointmentHoldService,
    AppointmentSlotAlreadyHeldError,
)
from app.services.conversation_health import (
    ConversationHealthResult,
    ConversationHealthService,
    EscalationReason,
)
from app.services.conversations import (
    ConversationCreate,
    ConversationMessageCreate,
    ConversationService,
)
from app.services.date_parsing import (
    DateParseStatus,
    NaturalLanguageDateParser,
)
from app.services.human_escalations import HumanEscalationService
from app.services.human_handoff_notifications import HumanHandoffNotificationService
from app.services.llm_receptionist import (
    LLMReceptionistAnalysisService,
    ReceptionistAnalysisRequest,
    ReceptionistAnalysisResult,
)
from app.services.scheduling import (
    AvailabilitySlotNotFoundError,
    AvailabilitySlotUnavailableError,
    InsufficientPatientIdentityError,
    PatientLookupCriteria,
    SchedulingService,
)
from app.services.slot_filling import (
    LLMChatSlotFillingService,
    SlotFillingRejectedField,
    SlotFillingResult,
)
from app.services.time_preferences import (
    TimePreferenceParser,
    TimePreferenceStatus,
    TimeWindow,
    is_time_in_window,
)

_ISO_DATE_PATTERN = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_TIME_PATTERN = re.compile(r"\b(\d{1,2}):(\d{2})\b")
_ISO_DATETIME_PATTERN = re.compile(
    r"\b(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?)\b",
)
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
_HOLD_KEYWORDS = [
    "hold",
    "take",
    "i'll take",
    "i will take",
    "reserve",
    "that works",
    "works for me",
    "first one",
    "second one",
    "third one",
]
_ORDINAL_SLOT_KEYWORDS = {
    "first one": 0,
    "second one": 1,
    "third one": 2,
}
_EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE_PATTERN = re.compile(r"(?:\+?\d[\d\s\-().]{6,}\d|\b\d{10,14}\b)")
_MY_NAME_IS_PATTERN = re.compile(r"my name is\s+(.+)", re.IGNORECASE)
_CONFIRMATION_PHRASES = [
    "confirm",
    "yes, book",
    "book it",
    "schedule it",
    "go ahead",
]
_PATIENT_IDENTITY_FIELD_LABELS = {
    "full_name": "full name",
    "date_of_birth": "date of birth",
    "phone": "phone",
    "email": "email",
}
_SLOT_FILLING_MIN_CONFIDENCE = 0.65
_RECENT_MESSAGES_LIMIT = 25
_HUMAN_HANDOFF_MESSAGE = (
    "I'll mark this conversation for human follow-up. "
    "A human receptionist can review it."
)
_HANDOFF_CONTEXT_KEYS = (
    "hold_id",
    "hold_expires_at",
    "selected_doctor_name",
    "requested_date",
    "selected_start_time",
)
_ESCALATION_SUGGESTION_SUFFIX = (
    " If you prefer, I can transfer this to a human receptionist."
)
_DATE_CLARIFICATION_MESSAGE = (
    "Please provide a specific date in YYYY-MM-DD or say something like "
    "tomorrow or next Monday."
)
_TIME_PREFERENCE_CLARIFICATION_MESSAGE = (
    "Please specify a time-of-day preference such as morning, afternoon, or "
    "evening, or provide an exact time in HH:MM format."
)
_HELD_TIME_PREFERENCE_CLARIFICATION_MESSAGE = (
    "You already have a time held. Please complete or release that hold before "
    "changing your time-of-day preference."
)


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
    AVAILABILITY_NO_MATCHING_TIME_WINDOW = "availability_no_matching_time_window"
    INVALID_DATE = "invalid_date"
    INVALID_TIME_PREFERENCE = "invalid_time_preference"
    HOLD_REQUEST = "hold_request"
    HOLD_CREATED = "hold_created"
    HOLD_MISSING_AVAILABILITY = "hold_missing_availability"
    HOLD_SLOT_NOT_FOUND = "hold_slot_not_found"
    HOLD_CONFLICT = "hold_conflict"
    PATIENT_IDENTITY_PARTIAL = "patient_identity_partial"
    PATIENT_IDENTITY_COMPLETE = "patient_identity_complete"
    BOOKING_IDENTITY_MISSING = "booking_identity_missing"
    BOOKING_CONFIRMATION_REQUIRED = "booking_confirmation_required"
    BOOKING_CONFIRMED = "booking_confirmed"
    BOOKING_HOLD_MISSING = "booking_hold_missing"
    BOOKING_HOLD_EXPIRED = "booking_hold_expired"
    BOOKING_CONFLICT = "booking_conflict"
    HUMAN_ESCALATION_REQUESTED = "human_escalation_requested"
    ESCALATION_SUGGESTED = "escalation_suggested"
    FALLBACK = "fallback"


_AVAILABILITY_CONTEXT_INTENTS = frozenset(
    {
        ChatReceptionistIntent.AVAILABILITY_MISSING_DATE,
        ChatReceptionistIntent.AVAILABILITY_MISSING_DOCTOR,
        ChatReceptionistIntent.AVAILABILITY_RESULTS,
        ChatReceptionistIntent.AVAILABILITY_NO_SLOTS,
        ChatReceptionistIntent.AVAILABILITY_NO_MATCHING_TIME_WINDOW,
        ChatReceptionistIntent.INVALID_DATE,
        ChatReceptionistIntent.INVALID_TIME_PREFERENCE,
    }
)
_MAX_OFFERED_SLOTS = 5
_HOLD_CONTEXT_INTENTS = frozenset(
    {
        ChatReceptionistIntent.HOLD_REQUEST,
        ChatReceptionistIntent.HOLD_CREATED,
        ChatReceptionistIntent.HOLD_MISSING_AVAILABILITY,
        ChatReceptionistIntent.HOLD_SLOT_NOT_FOUND,
        ChatReceptionistIntent.HOLD_CONFLICT,
    }
)
_BOOKING_CONTEXT_INTENTS = frozenset(
    {
        ChatReceptionistIntent.PATIENT_IDENTITY_PARTIAL,
        ChatReceptionistIntent.PATIENT_IDENTITY_COMPLETE,
        ChatReceptionistIntent.BOOKING_IDENTITY_MISSING,
        ChatReceptionistIntent.BOOKING_CONFIRMATION_REQUIRED,
        ChatReceptionistIntent.BOOKING_CONFIRMED,
        ChatReceptionistIntent.BOOKING_HOLD_MISSING,
        ChatReceptionistIntent.BOOKING_HOLD_EXPIRED,
        ChatReceptionistIntent.BOOKING_CONFLICT,
    }
)
_SUCCESSFUL_FLOW_INTENTS = frozenset(
    {
        ChatReceptionistIntent.AVAILABILITY_RESULTS,
        ChatReceptionistIntent.HOLD_CREATED,
        ChatReceptionistIntent.BOOKING_CONFIRMED,
        ChatReceptionistIntent.BOOKING_CONFIRMATION_REQUIRED,
        ChatReceptionistIntent.PATIENT_IDENTITY_PARTIAL,
        ChatReceptionistIntent.PATIENT_IDENTITY_COMPLETE,
        ChatReceptionistIntent.BOOKING_IDENTITY_MISSING,
    }
)


@dataclass(frozen=True, slots=True)
class ChatPatientIdentity:
    full_name: str | None = None
    date_of_birth: str | None = None
    phone: str | None = None
    email: str | None = None

    def missing_fields(self) -> list[str]:
        missing: list[str] = []
        if not self.full_name:
            missing.append("full_name")
        if not self.date_of_birth:
            missing.append("date_of_birth")
        if not self.phone:
            missing.append("phone")
        if not self.email:
            missing.append("email")
        return missing

    def is_complete(self) -> bool:
        return not self.missing_fields()


def message_has_confirmation(message: str) -> bool:
    normalized = message.lower()
    return any(phrase in normalized for phrase in _CONFIRMATION_PHRASES)


def merge_patient_identity(
    existing: dict[str, Any],
    parsed: ChatPatientIdentity,
) -> dict[str, Any]:
    merged = dict(existing)
    for field_name in ("full_name", "date_of_birth", "phone", "email"):
        if merged.get(field_name):
            continue
        value = getattr(parsed, field_name)
        if value:
            merged[field_name] = value
    return merged


@dataclass(frozen=True, slots=True)
class PendingHoldRelease:
    doctor_id: UUID
    start_time: datetime
    owner_id: str


@dataclass(frozen=True, slots=True)
class _RequestedDateExtraction:
    normalized_date: str | None = None
    date_parsing: dict[str, object] | None = None
    requires_clarification: bool = False


@dataclass(frozen=True, slots=True)
class _TimePreferenceExtraction:
    window: dict[str, str] | None = None
    time_preference_parsing: dict[str, object] | None = None
    requires_clarification: bool = False


@dataclass(frozen=True, slots=True)
class ChatReceptionistReply:
    intent: ChatReceptionistIntent
    content: str
    matched_specialty_id: UUID | None = None
    matched_specialty_name: str | None = None
    chat_context_updates: dict[str, Any] = field(default_factory=dict)
    availability_checked: bool = False
    offered_slot_count: int | None = None
    hold_created: bool | None = None
    hold_id: str | None = None
    appointment_id: str | None = None
    booking_attempted: bool = False
    booking_confirmed: bool = False
    booked_patient_id: UUID | None = None
    booked_appointment_start_time: datetime | None = None
    pending_hold_release: PendingHoldRelease | None = None
    date_parsing: dict[str, object] | None = None
    time_preference_parsing: dict[str, object] | None = None


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
    appointment_id: UUID | None = None
    confirmation_email_job_id: UUID | None = None
    human_handoff_notification_email_job_id: UUID | None = None
    hold_id_to_release: str | None = None
    booking_confirmed: bool = False
    booked_patient_id: UUID | None = None
    booked_appointment_start_time: datetime | None = None
    pending_hold_release: PendingHoldRelease | None = None


@dataclass(frozen=True, slots=True)
class _HumanEscalationRecordingResult:
    escalation_metadata: dict[str, Any]
    notification_metadata: dict[str, Any] | None = None
    notification_email_job_id: UUID | None = None


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
        appointment_holds: AppointmentHoldService,
        appointment_booking: AppointmentBookingService,
        responder: DeterministicChatResponder | None = None,
        llm_analysis: LLMReceptionistAnalysisService | None = None,
        slot_filling: LLMChatSlotFillingService | None = None,
        conversation_health: ConversationHealthService | None = None,
        human_escalations: HumanEscalationService | None = None,
        human_handoff_notifications: HumanHandoffNotificationService | None = None,
        date_parser: NaturalLanguageDateParser | None = None,
        time_preference_parser: TimePreferenceParser | None = None,
    ) -> None:
        self.conversations = conversations
        self.scheduling = scheduling
        self.appointment_holds = appointment_holds
        self.appointment_booking = appointment_booking
        self.responder = responder or DeterministicChatResponder()
        self.llm_analysis = llm_analysis
        self.slot_filling = slot_filling
        self.conversation_health = conversation_health
        self.human_escalations = human_escalations
        self.human_handoff_notifications = human_handoff_notifications
        self.date_parser = date_parser
        self.time_preference_parser = time_preference_parser

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
        llm_analysis_result = None
        slot_filling_result: SlotFillingResult | None = None
        if self.llm_analysis is not None:
            chat_context = conversation.conversation_metadata.get("chat_context", {})
            llm_analysis_result = self.llm_analysis.analyze_message(
                ReceptionistAnalysisRequest(
                    user_message=payload.message,
                    conversation_context=chat_context,
                ),
            )
            if self.slot_filling is not None:
                if self._is_slot_filling_eligible(llm_analysis_result):
                    slot_filling_result = self.slot_filling.apply_analysis(
                        analysis=llm_analysis_result.analysis,
                        chat_context=chat_context,
                    )
                    conversation = self.conversations.merge_chat_context(
                        conversation_id=conversation.id,
                        chat_context=slot_filling_result.updated_chat_context,
                    )
                else:
                    slot_filling_result = self._skipped_slot_filling_result()
        reply = self._generate_reply(
            payload.message,
            conversation,
            request_patient_id=payload.patient_id,
        )
        if reply.chat_context_updates:
            conversation = self.conversations.merge_chat_context(
                conversation_id=conversation.id,
                chat_context=reply.chat_context_updates,
            )

        health_result: ConversationHealthResult | None = None
        if self.conversation_health is not None:
            chat_context = conversation.conversation_metadata.get("chat_context", {})
            recent_messages = self.conversations.list_messages(
                conversation_id=conversation.id,
                limit=_RECENT_MESSAGES_LIMIT,
            )
            health_result = self.conversation_health.evaluate(
                user_message=payload.message,
                chat_context=chat_context,
                recent_messages=recent_messages,
            )
            reply, conversation = self._apply_conversation_health(
                reply=reply,
                health_result=health_result,
                conversation=conversation,
            )

        human_escalation_metadata: dict[str, Any] | None = None
        human_handoff_notification_metadata: dict[str, Any] | None = None
        human_handoff_notification_email_job_id: UUID | None = None
        if health_result is not None:
            escalation_recording = self._record_human_escalation_if_needed(
                health_result=health_result,
                conversation=conversation,
                request_patient_id=payload.patient_id,
            )
            if escalation_recording is not None:
                human_escalation_metadata = escalation_recording.escalation_metadata
                human_handoff_notification_metadata = escalation_recording.notification_metadata
                human_handoff_notification_email_job_id = (
                    escalation_recording.notification_email_job_id
                )

        assistant_metadata: dict[str, Any] = {
            "source": "chat_api",
            "intent": reply.intent.value,
        }
        if reply.matched_specialty_id is not None:
            assistant_metadata["matched_specialty_id"] = str(reply.matched_specialty_id)
        if reply.matched_specialty_name is not None:
            assistant_metadata["matched_specialty_name"] = reply.matched_specialty_name
        if (
            reply.chat_context_updates
            or reply.intent in _AVAILABILITY_CONTEXT_INTENTS
            or reply.intent in _HOLD_CONTEXT_INTENTS
            or reply.intent in _BOOKING_CONTEXT_INTENTS
        ):
            assistant_metadata["chat_context"] = conversation.conversation_metadata.get(
                "chat_context",
                {},
            )
        if reply.availability_checked:
            assistant_metadata["availability_checked"] = True
        if reply.offered_slot_count is not None:
            assistant_metadata["offered_slot_count"] = reply.offered_slot_count
        if reply.hold_created is not None:
            assistant_metadata["hold_created"] = reply.hold_created
        if reply.hold_id is not None:
            assistant_metadata["hold_id"] = reply.hold_id
        if reply.appointment_id is not None:
            assistant_metadata["appointment_id"] = reply.appointment_id
        if reply.booking_attempted:
            assistant_metadata["booking_attempted"] = True
        if reply.booking_confirmed:
            assistant_metadata["booking_confirmed"] = True
        if llm_analysis_result is not None:
            analysis = llm_analysis_result.analysis
            assistant_metadata["llm_shadow_analysis"] = {
                "intent": analysis.intent.value,
                "confidence": analysis.confidence,
                "urgency": analysis.urgency.value,
                "requires_human": analysis.requires_human,
                "safety_flags": analysis.safety_flags,
                "used_fallback": llm_analysis_result.used_fallback,
                "failure_reason": llm_analysis_result.failure_reason.value,
                "model": llm_analysis_result.model,
                "input_tokens": llm_analysis_result.input_tokens,
                "output_tokens": llm_analysis_result.output_tokens,
                "estimated_cost_micros": llm_analysis_result.estimated_cost_micros,
                "latency_ms": llm_analysis_result.latency_ms,
                "attempt_count": llm_analysis_result.attempt_count,
            }
        if slot_filling_result is not None:
            assistant_metadata["slot_filling"] = slot_filling_result.to_metadata()
        if health_result is not None:
            assistant_metadata["conversation_health"] = health_result.to_metadata()
        if human_escalation_metadata is not None:
            assistant_metadata["human_escalation"] = human_escalation_metadata
        if human_handoff_notification_metadata is not None:
            assistant_metadata["human_handoff_notification"] = (
                human_handoff_notification_metadata
            )
        if reply.date_parsing is not None:
            assistant_metadata["date_parsing"] = reply.date_parsing
        if reply.time_preference_parsing is not None:
            assistant_metadata["time_preference"] = reply.time_preference_parsing

        assistant_message = self.conversations.append_message(
            ConversationMessageCreate(
                conversation_id=conversation.id,
                role=ConversationMessageRole.ASSISTANT,
                content=reply.content,
                message_metadata=assistant_metadata,
            ),
        )

        booking_confirmed = reply.booking_confirmed
        appointment_id = (
            UUID(reply.appointment_id) if reply.appointment_id is not None else None
        )

        return ChatMessageResult(
            conversation=conversation,
            user_message=user_message,
            assistant_message=assistant_message,
            intent=reply.intent,
            reply=reply.content,
            appointment_id=appointment_id,
            hold_id_to_release=reply.hold_id if booking_confirmed else None,
            booking_confirmed=booking_confirmed,
            booked_patient_id=reply.booked_patient_id,
            booked_appointment_start_time=reply.booked_appointment_start_time,
            pending_hold_release=reply.pending_hold_release,
            human_handoff_notification_email_job_id=(
                human_handoff_notification_email_job_id
            ),
        )

    def _apply_conversation_health(
        self,
        *,
        reply: ChatReceptionistReply,
        health_result: ConversationHealthResult,
        conversation: Conversation,
    ) -> tuple[ChatReceptionistReply, Conversation]:
        if health_result.should_escalate_immediately:
            if health_result.escalation_reason == EscalationReason.USER_REQUESTED_HUMAN:
                conversation = self.conversations.update_conversation_status(
                    conversation_id=conversation.id,
                    status=ConversationStatus.ESCALATED,
                )
                return (
                    replace(
                        reply,
                        intent=ChatReceptionistIntent.HUMAN_ESCALATION_REQUESTED,
                        content=_HUMAN_HANDOFF_MESSAGE,
                        chat_context_updates={},
                        availability_checked=False,
                        offered_slot_count=None,
                        hold_created=None,
                        hold_id=None,
                        appointment_id=None,
                        booking_attempted=False,
                        booking_confirmed=False,
                        booked_patient_id=None,
                        booked_appointment_start_time=None,
                        pending_hold_release=None,
                    ),
                    conversation,
                )

            return reply, conversation

        if self._should_append_escalation_suggestion(reply, health_result):
            new_intent = (
                ChatReceptionistIntent.ESCALATION_SUGGESTED
                if reply.intent == ChatReceptionistIntent.FALLBACK
                else reply.intent
            )
            return (
                replace(
                    reply,
                    intent=new_intent,
                    content=f"{reply.content}{_ESCALATION_SUGGESTION_SUFFIX}",
                ),
                conversation,
            )

        return reply, conversation

    def _record_human_escalation_if_needed(
        self,
        *,
        health_result: ConversationHealthResult,
        conversation: Conversation,
        request_patient_id: UUID | None,
    ) -> _HumanEscalationRecordingResult | None:
        if self.human_escalations is None or not health_result.should_escalate_immediately:
            return None

        if health_result.escalation_reason not in {
            EscalationReason.USER_REQUESTED_HUMAN,
            EscalationReason.MEDICAL_EMERGENCY,
        }:
            return None

        existing = self.human_escalations.get_active_escalation_for_conversation(
            conversation.id,
        )
        escalation = self.human_escalations.create_or_get_active_escalation(
            conversation_id=conversation.id,
            reason=self._map_human_escalation_reason(health_result.escalation_reason),
            source=HumanEscalationSource.CHAT,
            patient_id=conversation.patient_id or request_patient_id,
            appointment_id=conversation.appointment_id,
            summary=self._build_human_escalation_summary(health_result.escalation_reason),
            created_by="chat_receptionist",
            handoff_context=self._build_handoff_context(conversation),
        )

        escalation_metadata = {
            "created": existing is None,
            "escalation_id": str(escalation.id),
            "reason": escalation.reason.value,
            "priority": escalation.priority.value,
            "status": escalation.status.value,
        }
        notification_metadata: dict[str, Any] | None = None
        notification_email_job_id: UUID | None = None

        if self.human_handoff_notifications is not None:
            notification_result = (
                self.human_handoff_notifications.create_or_get_notification_job(
                    escalation=escalation,
                )
            )
            notification_email_job_id = notification_result.email_job_id
            notification_metadata = {
                "email_job_id": str(notification_result.email_job_id),
                "created": notification_result.created,
            }

        return _HumanEscalationRecordingResult(
            escalation_metadata=escalation_metadata,
            notification_metadata=notification_metadata,
            notification_email_job_id=notification_email_job_id,
        )

    def _map_human_escalation_reason(
        self,
        reason: EscalationReason,
    ) -> HumanEscalationReason:
        if reason == EscalationReason.USER_REQUESTED_HUMAN:
            return HumanEscalationReason.USER_REQUESTED_HUMAN

        if reason == EscalationReason.MEDICAL_EMERGENCY:
            return HumanEscalationReason.MEDICAL_EMERGENCY

        return HumanEscalationReason.UNKNOWN

    def _build_human_escalation_summary(self, reason: EscalationReason) -> str:
        if reason == EscalationReason.USER_REQUESTED_HUMAN:
            return "User requested human receptionist handoff."

        if reason == EscalationReason.MEDICAL_EMERGENCY:
            return "Medical emergency signal detected in chat conversation."

        return "Human escalation requested from chat conversation."

    def _build_handoff_context(self, conversation: Conversation) -> dict[str, Any] | None:
        chat_context = conversation.conversation_metadata.get("chat_context", {})
        if not isinstance(chat_context, dict):
            return None

        handoff_context: dict[str, Any] = {}
        hold_id = chat_context.get("hold_id")
        active_hold_present = bool(hold_id) and not chat_context.get("appointment_id")

        if active_hold_present:
            handoff_context["active_hold_present"] = True

        for key in _HANDOFF_CONTEXT_KEYS:
            value = chat_context.get(key)
            if value is not None and value != "":
                handoff_context[key] = value

        return handoff_context or None

    def _should_append_escalation_suggestion(
        self,
        reply: ChatReceptionistReply,
        health_result: ConversationHealthResult,
    ) -> bool:
        if not health_result.should_suggest_escalation:
            return False

        if reply.booking_confirmed or reply.intent == ChatReceptionistIntent.BOOKING_CONFIRMED:
            return False

        if reply.intent in _SUCCESSFUL_FLOW_INTENTS:
            return False

        return self._is_low_quality_reply(reply, health_result)

    def _is_low_quality_reply(
        self,
        reply: ChatReceptionistReply,
        health_result: ConversationHealthResult,
    ) -> bool:
        if reply.intent == ChatReceptionistIntent.FALLBACK:
            return True

        signals = health_result.signals
        return (
            signals.last_intent == ChatReceptionistIntent.FALLBACK.value
            and signals.repeated_intent_count >= 2
        )

    def _is_slot_filling_eligible(
        self,
        result: ReceptionistAnalysisResult,
    ) -> bool:
        if result.used_fallback:
            return False

        if result.failure_reason != LLMFailureReason.NONE:
            return False

        if result.analysis.confidence < _SLOT_FILLING_MIN_CONFIDENCE:
            return False

        if result.analysis.intent == ReceptionistLLMIntent.EMERGENCY:
            return False

        return not result.analysis.safety_flags

    def _skipped_slot_filling_result(self) -> SlotFillingResult:
        return SlotFillingResult(
            updated_chat_context={},
            used_llm_analysis=False,
            rejected_fields=[
                SlotFillingRejectedField(
                    field="analysis",
                    value=None,
                    reason="analysis_not_eligible",
                ),
            ],
        )

    def _generate_reply(
        self,
        message: str,
        conversation: Conversation,
        *,
        request_patient_id: UUID | None = None,
    ) -> ChatReceptionistReply:
        normalized_message = message.lower()
        existing_context = dict(conversation.conversation_metadata.get("chat_context", {}))

        if self.responder._contains_any(normalized_message, _EMERGENCY_KEYWORDS):
            return self.responder.generate_reply(message=message)

        if self.responder._contains_any(normalized_message, _CANCEL_KEYWORDS):
            return self.responder.generate_reply(message=message)

        if self.responder._contains_any(normalized_message, _RESCHEDULE_KEYWORDS):
            return self.responder.generate_reply(message=message)

        date_extraction = self._extract_requested_date(message)
        time_extraction = self._extract_time_preference(message)

        def finish(reply: ChatReceptionistReply) -> ChatReceptionistReply:
            if date_extraction.date_parsing is not None:
                reply = replace(reply, date_parsing=date_extraction.date_parsing)
            if time_extraction.time_preference_parsing is not None:
                reply = replace(
                    reply,
                    time_preference_parsing=time_extraction.time_preference_parsing,
                )
            return reply

        if date_extraction.requires_clarification and self._is_clearly_asking_availability(
            normalized_message,
            date_parsing=date_extraction.date_parsing,
        ):
            context_updates = self._extract_context_updates(
                normalized_message,
                message,
                existing_context=existing_context,
                date_extraction=date_extraction,
                time_extraction=time_extraction,
            )
            return finish(
                ChatReceptionistReply(
                    intent=ChatReceptionistIntent.INVALID_DATE,
                    content=_DATE_CLARIFICATION_MESSAGE,
                    chat_context_updates=context_updates,
                ),
            )

        if (
            time_extraction.requires_clarification
            and self._is_clearly_asking_availability_with_time_preference(
                normalized_message,
                time_preference_parsing=time_extraction.time_preference_parsing,
            )
        ):
            context_updates = self._extract_context_updates(
                normalized_message,
                message,
                existing_context=existing_context,
                date_extraction=date_extraction,
                time_extraction=time_extraction,
            )
            return finish(
                ChatReceptionistReply(
                    intent=ChatReceptionistIntent.INVALID_TIME_PREFERENCE,
                    content=_TIME_PREFERENCE_CLARIFICATION_MESSAGE,
                    chat_context_updates=context_updates,
                ),
            )

        if self.date_parser is None and self._has_invalid_date_pattern(message):
            return finish(
                ChatReceptionistReply(
                    intent=ChatReceptionistIntent.INVALID_DATE,
                    content=(
                        "That date does not look valid. "
                        "Please provide a date in YYYY-MM-DD format."
                    ),
                ),
            )

        if (
            self.date_parser is not None
            and date_extraction.date_parsing is not None
            and date_extraction.date_parsing.get("status") == DateParseStatus.INVALID.value
        ):
            return finish(
                ChatReceptionistReply(
                    intent=ChatReceptionistIntent.INVALID_DATE,
                    content=(
                        "That date does not look valid. "
                        "Please provide a date in YYYY-MM-DD format."
                    ),
                ),
            )

        context_updates = self._extract_context_updates(
            normalized_message,
            message,
            existing_context=existing_context,
            date_extraction=date_extraction,
            time_extraction=time_extraction,
        )
        merged_context = {**existing_context, **context_updates}

        if (
            time_extraction.window is not None
            and existing_context.get("hold_id")
            and isinstance(existing_context.get("requested_time_window"), dict)
            and not self._time_windows_equal(
                existing_context["requested_time_window"],
                time_extraction.window,
            )
        ):
            return finish(
                ChatReceptionistReply(
                    intent=ChatReceptionistIntent.HOLD_REQUEST,
                    content=_HELD_TIME_PREFERENCE_CLARIFICATION_MESSAGE,
                    chat_context_updates=context_updates,
                    hold_created=False,
                ),
            )

        booking_reply = self._handle_booking_flow(
            message=message,
            conversation=conversation,
            merged_context=merged_context,
            context_updates=context_updates,
            request_patient_id=request_patient_id,
        )
        if booking_reply is not None:
            return finish(booking_reply)

        if self.responder._contains_any(normalized_message, _SPECIALTY_LIST_KEYWORDS):
            specialties = self.scheduling.list_specialties()
            return finish(
                ChatReceptionistReply(
                    intent=ChatReceptionistIntent.LIST_SPECIALTIES,
                    content=self._format_specialties(specialties),
                ),
            )

        if self.responder._contains_any(normalized_message, _DOCTOR_LIST_KEYWORDS):
            doctors = self.scheduling.list_doctors()
            return finish(
                ChatReceptionistReply(
                    intent=ChatReceptionistIntent.LIST_DOCTORS,
                    content=self._format_doctors(doctors),
                ),
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
            return finish(
                ChatReceptionistReply(
                    intent=ChatReceptionistIntent.SPECIALTY_DOCTORS,
                    content=self._format_doctors(
                        doctors,
                        specialty_name=matched_specialty.name,
                    ),
                    matched_specialty_id=matched_specialty.id,
                    matched_specialty_name=matched_specialty.name,
                    chat_context_updates=context_updates,
                ),
            )

        if self._is_hold_request(normalized_message, merged_context, message):
            return finish(
                self._handle_hold_flow(
                    message=message,
                    normalized_message=normalized_message,
                    conversation=conversation,
                    merged_context=merged_context,
                    context_updates=context_updates,
                ),
            )

        if self._is_availability_request(normalized_message) or (
            self._should_enter_availability_flow(
                normalized_message,
                merged_context,
            )
        ) or completing_availability:
            return finish(
                self._handle_availability_flow(
                    merged_context=merged_context,
                    context_updates=context_updates,
                ),
            )

        if self.responder._contains_any(normalized_message, _APPOINTMENT_KEYWORDS):
            return finish(
                ChatReceptionistReply(
                    intent=ChatReceptionistIntent.APPOINTMENT_REQUEST,
                    content=(
                        "I can help with appointment scheduling. Please tell me the "
                        "specialty or doctor you would like to see."
                    ),
                    chat_context_updates=context_updates,
                ),
            )

        return finish(self.responder.generate_reply(message=message))

    def _extract_context_updates(
        self,
        normalized_message: str,
        message: str,
        *,
        existing_context: dict[str, Any] | None = None,
        date_extraction: _RequestedDateExtraction | None = None,
        time_extraction: _TimePreferenceExtraction | None = None,
    ) -> dict[str, Any]:
        context_updates: dict[str, Any] = {}
        context = existing_context or {}

        if date_extraction is None:
            date_extraction = self._extract_requested_date(message)

        if time_extraction is None:
            time_extraction = self._extract_time_preference(message)

        if date_extraction.normalized_date is not None and not context.get("hold_id"):
            context_updates["requested_date"] = date_extraction.normalized_date

        if time_extraction.window is not None and not context.get("hold_id"):
            context_updates["requested_time_window"] = time_extraction.window

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

    def parse_patient_identity(
        self,
        message: str,
        *,
        booking_context: bool = False,
    ) -> ChatPatientIdentity:
        email_match = _EMAIL_PATTERN.search(message)
        email = email_match.group(0) if email_match is not None else None

        phone = self._extract_phone(message)
        date_of_birth = self._extract_date_of_birth(
            message,
            booking_context=booking_context,
        )
        full_name = self._extract_full_name(
            message,
            email=email,
            phone=phone,
            date_of_birth=date_of_birth,
            booking_context=booking_context,
        )

        return ChatPatientIdentity(
            full_name=full_name,
            date_of_birth=date_of_birth,
            phone=phone,
            email=email,
        )

    def _extract_phone(self, message: str) -> str | None:
        for match in _PHONE_PATTERN.finditer(message):
            candidate = match.group(0).strip()
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", candidate):
                continue

            digits = re.sub(r"\D", "", candidate)
            if 7 <= len(digits) <= 15:
                return candidate
        return None

    def _extract_date_of_birth(
        self,
        message: str,
        *,
        booking_context: bool = False,
    ) -> str | None:
        match = _ISO_DATE_PATTERN.search(message)
        if match is None:
            return None

        try:
            date.fromisoformat(match.group(1))
        except ValueError:
            return None

        has_dob_cue = bool(
            re.search(r"\b(dob|date of birth|born)\b", message, re.IGNORECASE)
            or "," in message
            or _MY_NAME_IS_PATTERN.search(message)
            or booking_context
        )
        if not has_dob_cue:
            return None

        return match.group(1)

    def _extract_full_name(
        self,
        message: str,
        *,
        email: str | None,
        phone: str | None,
        date_of_birth: str | None,
        booking_context: bool = False,
    ) -> str | None:
        name_match = _MY_NAME_IS_PATTERN.search(message)
        if name_match is not None:
            remainder = name_match.group(1).strip()
            cut_points: list[int] = []

            if "," in remainder:
                cut_points.append(remainder.index(","))

            if date_of_birth is not None:
                dob_index = remainder.find(date_of_birth)
                if dob_index >= 0:
                    cut_points.append(dob_index)

            if email is not None:
                email_index = remainder.lower().find(email.lower())
                if email_index >= 0:
                    cut_points.append(email_index)

            if phone is not None:
                phone_index = remainder.find(phone)
                if phone_index >= 0:
                    cut_points.append(phone_index)

            for keyword in ("phone", "email", "dob", "date of birth"):
                keyword_index = remainder.lower().find(keyword)
                if keyword_index >= 0:
                    cut_points.append(keyword_index)

            if cut_points:
                remainder = remainder[: min(cut_points)].strip()

            return remainder or None

        segments = [segment.strip() for segment in message.split(",")]
        if len(segments) < 2:
            if (
                booking_context
                and segments
                and len(segments[0].split()) >= 2
                and not self._looks_like_scheduling_text(segments[0])
            ):
                return segments[0]
            return None

        first_segment = segments[0]
        if first_segment.lower().startswith("my name is "):
            return None

        if len(first_segment.split()) >= 2:
            return first_segment

        return None

    def _looks_like_scheduling_text(self, value: str) -> bool:
        normalized = value.lower()
        scheduling_terms = (
            "dr.",
            "doctor",
            "specialt",
            "availab",
            "appointment",
            "schedule",
            "book",
        )
        if any(term in normalized for term in scheduling_terms):
            return True

        return _ISO_DATE_PATTERN.search(value) is not None

    def _patient_identity_from_context(
        self,
        identity_data: dict[str, Any],
    ) -> ChatPatientIdentity:
        return ChatPatientIdentity(
            full_name=identity_data.get("full_name") or None,
            date_of_birth=identity_data.get("date_of_birth") or None,
            phone=identity_data.get("phone") or None,
            email=identity_data.get("email") or None,
        )

    def _format_missing_identity_fields(self, missing_fields: list[str]) -> str:
        labels = [_PATIENT_IDENTITY_FIELD_LABELS[field] for field in missing_fields]
        return self._join_names(labels)

    def _handle_booking_flow(
        self,
        *,
        message: str,
        conversation: Conversation,
        merged_context: dict[str, Any],
        context_updates: dict[str, Any],
        request_patient_id: UUID | None = None,
    ) -> ChatReceptionistReply | None:
        hold_id = merged_context.get("hold_id")
        booking_context = bool(hold_id)
        has_confirmation = message_has_confirmation(message)
        offered_slots = merged_context.get("offered_slots") or []

        parsed = self.parse_patient_identity(
            message,
            booking_context=booking_context,
        )
        has_identity_fields = any(
            (
                parsed.full_name,
                parsed.date_of_birth,
                parsed.phone,
                parsed.email,
            )
        )

        if not self._should_enter_booking_flow(
            hold_id=hold_id,
            has_identity_fields=has_identity_fields,
            has_confirmation=has_confirmation,
            offered_slots=offered_slots,
        ):
            return None

        existing_raw = merged_context.get("patient_identity")
        existing_identity = existing_raw if isinstance(existing_raw, dict) else {}
        merged_identity = merge_patient_identity(existing_identity, parsed)
        identity = self._patient_identity_from_context(merged_identity)
        identity_updates = {
            **context_updates,
            "patient_identity": merged_identity,
        }

        if has_confirmation and not hold_id and not offered_slots:
            return ChatReceptionistReply(
                intent=ChatReceptionistIntent.BOOKING_HOLD_MISSING,
                content=(
                    "Please choose an available time and hold it first "
                    "before confirming a booking."
                ),
                chat_context_updates=identity_updates if has_identity_fields else context_updates,
                booking_attempted=True,
            )

        if not hold_id:
            if has_identity_fields and not identity.is_complete():
                missing_text = self._format_missing_identity_fields(
                    identity.missing_fields(),
                )
                return ChatReceptionistReply(
                    intent=ChatReceptionistIntent.PATIENT_IDENTITY_PARTIAL,
                    content=(
                        f"I've noted your details. I still need your {missing_text}."
                    ),
                    chat_context_updates=identity_updates,
                )

            if has_identity_fields and identity.is_complete():
                return ChatReceptionistReply(
                    intent=ChatReceptionistIntent.PATIENT_IDENTITY_COMPLETE,
                    content="I have all of your patient details on file.",
                    chat_context_updates=identity_updates,
                )

            return None

        if not identity.is_complete():
            missing_text = self._format_missing_identity_fields(
                identity.missing_fields(),
            )
            return ChatReceptionistReply(
                intent=ChatReceptionistIntent.BOOKING_IDENTITY_MISSING,
                content=(
                    f"I still need your {missing_text} to confirm the booking. "
                    "Your hold is still active."
                ),
                chat_context_updates=identity_updates,
                hold_id=str(hold_id),
                booking_attempted=True,
            )

        if not has_confirmation:
            return ChatReceptionistReply(
                intent=ChatReceptionistIntent.BOOKING_CONFIRMATION_REQUIRED,
                content=(
                    "I have your patient details on file. "
                    "Please confirm to book the held appointment."
                ),
                chat_context_updates=identity_updates,
                hold_id=str(hold_id),
            )

        return self._attempt_booking(
            conversation=conversation,
            merged_context=merged_context,
            merged_identity=merged_identity,
            identity_updates=identity_updates,
            request_patient_id=request_patient_id,
        )

    def _should_enter_booking_flow(
        self,
        *,
        hold_id: Any,
        has_identity_fields: bool,
        has_confirmation: bool,
        offered_slots: list[Any],
    ) -> bool:
        if hold_id and (has_identity_fields or has_confirmation):
            return True

        if has_confirmation and not hold_id and not offered_slots:
            return True

        if has_identity_fields and not hold_id:
            return True

        return False

    def _attempt_booking(
        self,
        *,
        conversation: Conversation,
        merged_context: dict[str, Any],
        merged_identity: dict[str, Any],
        identity_updates: dict[str, Any],
        request_patient_id: UUID | None = None,
    ) -> ChatReceptionistReply:
        hold_id_raw = merged_context.get("hold_id")
        slot_id_raw = merged_context.get("selected_availability_slot_id")
        hold_id = str(hold_id_raw) if hold_id_raw else None

        if not hold_id or not slot_id_raw:
            return ChatReceptionistReply(
                intent=ChatReceptionistIntent.BOOKING_HOLD_MISSING,
                content=(
                    "Please choose an available time and hold it first "
                    "before confirming a booking."
                ),
                chat_context_updates=identity_updates,
                hold_id=hold_id,
                booking_attempted=True,
            )

        owner_id = str(merged_context.get("hold_owner_id") or conversation.id)

        try:
            patient = self._resolve_patient_for_booking(
                merged_identity,
                conversation_patient_id=conversation.patient_id,
                request_patient_id=request_patient_id,
            )
        except InsufficientPatientIdentityError:
            missing_text = self._format_missing_identity_fields(
                self._patient_identity_from_context(merged_identity).missing_fields(),
            )
            return ChatReceptionistReply(
                intent=ChatReceptionistIntent.BOOKING_IDENTITY_MISSING,
                content=(
                    f"I still need your {missing_text} to confirm the booking. "
                    "Your hold is still active."
                ),
                chat_context_updates=identity_updates,
                hold_id=hold_id,
                booking_attempted=True,
            )

        if patient is None:
            return ChatReceptionistReply(
                intent=ChatReceptionistIntent.BOOKING_CONFLICT,
                content=(
                    "I could not find a matching patient record for those details. "
                    "Please contact the clinic for assistance."
                ),
                chat_context_updates=identity_updates,
                hold_id=hold_id,
                booking_attempted=True,
            )

        try:
            booking_result = self.appointment_booking.book_appointment(
                AppointmentBookingRequest(
                    hold_id=UUID(hold_id),
                    availability_slot_id=UUID(str(slot_id_raw)),
                    patient_id=patient.id,
                    owner_id=owner_id,
                ),
            )
        except AppointmentHoldNotFoundError:
            return ChatReceptionistReply(
                intent=ChatReceptionistIntent.BOOKING_HOLD_EXPIRED,
                content=(
                    "Your temporary hold was not found or has expired. "
                    "Please check availability and choose a time again."
                ),
                chat_context_updates=identity_updates,
                hold_id=hold_id,
                booking_attempted=True,
            )
        except (
            AppointmentHoldMismatchError,
            AppointmentHoldOwnershipError,
        ):
            return ChatReceptionistReply(
                intent=ChatReceptionistIntent.BOOKING_HOLD_EXPIRED,
                content=(
                    "Your temporary hold is no longer valid for this booking. "
                    "Please check availability and choose a time again."
                ),
                chat_context_updates=identity_updates,
                hold_id=hold_id,
                booking_attempted=True,
            )
        except (
            AppointmentSlotAlreadyBookedError,
            BookingAvailabilitySlotUnavailableError,
            BookingAvailabilitySlotNotFoundError,
            BookingDoctorNotFoundError,
            BookingPatientNotFoundError,
            AppointmentSlotAlreadyHeldError,
        ):
            return ChatReceptionistReply(
                intent=ChatReceptionistIntent.BOOKING_CONFLICT,
                content=(
                    "That time is no longer available for booking. "
                    "Please choose another available time."
                ),
                chat_context_updates=identity_updates,
                hold_id=hold_id,
                booking_attempted=True,
            )

        appointment = booking_result.appointment
        hold = booking_result.hold
        doctor_name = str(merged_context.get("selected_doctor_name", "the selected doctor"))
        appointment_date = self._format_booking_date(merged_context)
        display_time = self._format_booking_display_time(merged_context)
        booking_confirmed_at = datetime.now(tz=UTC).isoformat()

        return ChatReceptionistReply(
            intent=ChatReceptionistIntent.BOOKING_CONFIRMED,
            content=(
                f"Your appointment with {doctor_name} on {appointment_date} at "
                f"{display_time} has been booked. A confirmation email will be sent "
                "if an email address is available."
            ),
            chat_context_updates={
                **identity_updates,
                "appointment_id": str(appointment.id),
                "booking_confirmed_at": booking_confirmed_at,
            },
            hold_id=hold_id,
            appointment_id=str(appointment.id),
            booking_attempted=True,
            booking_confirmed=True,
            booked_patient_id=patient.id,
            booked_appointment_start_time=appointment.start_time,
            pending_hold_release=PendingHoldRelease(
                doctor_id=hold.doctor_id,
                start_time=hold.start_time,
                owner_id=owner_id,
            ),
        )

    def _resolve_patient_for_booking(
        self,
        merged_identity: dict[str, Any],
        *,
        conversation_patient_id: UUID | None,
        request_patient_id: UUID | None = None,
    ) -> Patient | None:
        linked_patient_id = conversation_patient_id or request_patient_id
        if linked_patient_id is not None:
            patient = self.scheduling.patients.get_by_id(linked_patient_id)

            if patient is not None:
                return patient

        return self.scheduling.lookup_patient(
            PatientLookupCriteria(
                full_name=str(merged_identity["full_name"]),
                date_of_birth=date.fromisoformat(str(merged_identity["date_of_birth"])),
                phone_number=merged_identity.get("phone"),
                email=merged_identity.get("email"),
            ),
        )

    def _format_booking_display_time(self, merged_context: dict[str, Any]) -> str:
        start_time_raw = merged_context.get("selected_start_time")

        if start_time_raw is None:
            return "the selected time"

        try:
            parsed = datetime.fromisoformat(str(start_time_raw))
        except ValueError:
            return "the selected time"

        return parsed.strftime("%H:%M")

    def _format_booking_date(self, merged_context: dict[str, Any]) -> str:
        start_time_raw = merged_context.get("selected_start_time")

        if start_time_raw is not None:
            try:
                return datetime.fromisoformat(str(start_time_raw)).date().isoformat()
            except ValueError:
                pass

        return str(merged_context.get("requested_date", ""))

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
        requested_time_window = merged_context.get("requested_time_window")

        if isinstance(requested_time_window, dict):
            filtered_slots = self._filter_slots_by_time_window(
                slots,
                requested_time_window,
            )
            if slots and not filtered_slots:
                label = str(requested_time_window.get("label", "requested"))
                return ChatReceptionistReply(
                    intent=ChatReceptionistIntent.AVAILABILITY_NO_MATCHING_TIME_WINDOW,
                    content=(
                        f"I don't see any {label} openings for that date. "
                        "Would you like another time window or another date?"
                    ),
                    chat_context_updates={
                        **context_updates,
                        "offered_slots": [],
                    },
                    availability_checked=True,
                    offered_slot_count=0,
                )
            slots = filtered_slots

        if slots:
            shown_slots = list(slots[:_MAX_OFFERED_SLOTS])
            offered_slots = self._serialize_offered_slots(shown_slots)
            return ChatReceptionistReply(
                intent=ChatReceptionistIntent.AVAILABILITY_RESULTS,
                content=self._format_availability_slots(
                    slots,
                    doctor_name=doctor_name,
                    requested_date=str(requested_date),
                ),
                chat_context_updates={
                    **context_updates,
                    "offered_slots": offered_slots,
                },
                availability_checked=True,
                offered_slot_count=len(offered_slots),
            )

        return ChatReceptionistReply(
            intent=ChatReceptionistIntent.AVAILABILITY_NO_SLOTS,
            content=(
                f"I did not find open times for {doctor_name} on {requested_date}. "
                "Please try another date or doctor."
            ),
            chat_context_updates={
                **context_updates,
                "offered_slots": [],
            },
            availability_checked=True,
            offered_slot_count=0,
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

    def _serialize_offered_slots(
        self,
        slots: Sequence[AvailabilitySlot],
    ) -> list[dict[str, Any]]:
        return [
            {
                "availability_slot_id": str(slot.id),
                "doctor_id": str(slot.doctor_id),
                "start_time": slot.start_time.isoformat(),
                "display_time": slot.start_time.strftime("%H:%M"),
            }
            for slot in slots
        ]

    def _format_availability_slots(
        self,
        slots: Sequence[AvailabilitySlot],
        *,
        doctor_name: str,
        requested_date: str,
    ) -> str:
        shown_slots = list(slots[:_MAX_OFFERED_SLOTS])
        times = [slot.start_time.strftime("%H:%M") for slot in shown_slots]
        times_text = self._join_names(times)
        suffix = ""

        if len(slots) > _MAX_OFFERED_SLOTS:
            suffix = (
                f" There are {len(slots) - _MAX_OFFERED_SLOTS} more openings available."
            )

        return (
            f"Open times for {doctor_name} on {requested_date}: {times_text}.{suffix} "
            "You can choose a time, and booking will be handled in a later step."
        )

    def _extract_requested_date(self, message: str) -> _RequestedDateExtraction:
        if self.date_parser is None:
            extracted_date = self._extract_iso_date(message)
            if extracted_date is not None:
                return _RequestedDateExtraction(
                    normalized_date=extracted_date.isoformat(),
                )
            return _RequestedDateExtraction()

        parse_result = self.date_parser.parse(message)
        metadata = parse_result.to_metadata()

        if (
            parse_result.status == DateParseStatus.PARSED
            and parse_result.normalized_date is not None
        ):
            return _RequestedDateExtraction(
                normalized_date=parse_result.normalized_date,
                date_parsing=metadata,
            )

        if parse_result.status == DateParseStatus.NOT_FOUND:
            extracted_date = self._extract_iso_date(message)
            if extracted_date is not None:
                return _RequestedDateExtraction(
                    normalized_date=extracted_date.isoformat(),
                    date_parsing={
                        "status": DateParseStatus.PARSED.value,
                        "normalized_date": extracted_date.isoformat(),
                        "source_text": extracted_date.isoformat(),
                        "reason": None,
                    },
                )
            return _RequestedDateExtraction()

        extracted_date = self._extract_iso_date(message)
        if extracted_date is not None:
            return _RequestedDateExtraction(
                normalized_date=extracted_date.isoformat(),
                date_parsing={
                    "status": DateParseStatus.PARSED.value,
                    "normalized_date": extracted_date.isoformat(),
                    "source_text": extracted_date.isoformat(),
                    "reason": None,
                },
            )

        stripped_message = self._strip_time_preference_markers(message)
        if stripped_message != message:
            retry_result = self.date_parser.parse(stripped_message)
            if (
                retry_result.status == DateParseStatus.PARSED
                and retry_result.normalized_date is not None
            ):
                return _RequestedDateExtraction(
                    normalized_date=retry_result.normalized_date,
                    date_parsing=retry_result.to_metadata(),
                )

        return _RequestedDateExtraction(
            date_parsing=metadata,
            requires_clarification=True,
        )

    def _strip_time_preference_markers(self, message: str) -> str:
        stripped = message
        for label in ("morning", "afternoon", "evening"):
            stripped = re.sub(rf"\b{label}\b", " ", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\bor\b", " ", stripped, flags=re.IGNORECASE)
        return re.sub(r"\s+", " ", stripped).strip()

    def _extract_time_preference(self, message: str) -> _TimePreferenceExtraction:
        if self.time_preference_parser is None:
            return _TimePreferenceExtraction()

        parse_result = self.time_preference_parser.parse(message)
        metadata = parse_result.to_metadata()

        if (
            parse_result.status == TimePreferenceStatus.PARSED
            and parse_result.window is not None
        ):
            window = parse_result.window
            return _TimePreferenceExtraction(
                window={
                    "label": window.label,
                    "start_time": window.start_time,
                    "end_time": window.end_time,
                },
                time_preference_parsing=metadata,
            )

        if parse_result.status == TimePreferenceStatus.NOT_FOUND:
            return _TimePreferenceExtraction()

        return _TimePreferenceExtraction(
            time_preference_parsing=metadata,
            requires_clarification=True,
        )

    def _is_clearly_asking_availability_with_time_preference(
        self,
        normalized_message: str,
        *,
        time_preference_parsing: dict[str, object] | None,
    ) -> bool:
        if self._is_availability_request(normalized_message):
            return True

        has_scheduling_target = (
            self._match_doctor_in_message(normalized_message) is not None
            or self._match_specialty_in_message(normalized_message) is not None
        )
        if not has_scheduling_target:
            return False

        if time_preference_parsing is None:
            return False

        status = time_preference_parsing.get("status")
        return status in {
            TimePreferenceStatus.UNSUPPORTED.value,
            TimePreferenceStatus.AMBIGUOUS.value,
        }

    def _filter_slots_by_time_window(
        self,
        slots: Sequence[AvailabilitySlot],
        window: dict[str, Any],
    ) -> list[AvailabilitySlot]:
        label = window.get("label")
        start_time = window.get("start_time")
        end_time = window.get("end_time")
        if (
            not isinstance(label, str)
            or not isinstance(start_time, str)
            or not isinstance(end_time, str)
        ):
            return list(slots)

        time_window = TimeWindow(
            label=label,
            start_time=start_time,
            end_time=end_time,
        )
        return [
            slot
            for slot in slots
            if is_time_in_window(
                time_value=slot.start_time.strftime("%H:%M"),
                window=time_window,
            )
        ]

    def _time_windows_equal(
        self,
        existing: dict[str, Any],
        window: dict[str, str],
    ) -> bool:
        return (
            existing.get("label") == window["label"]
            and existing.get("start_time") == window["start_time"]
            and existing.get("end_time") == window["end_time"]
        )

    def _is_clearly_asking_availability(
        self,
        normalized_message: str,
        *,
        date_parsing: dict[str, object] | None,
    ) -> bool:
        if self._is_availability_request(normalized_message):
            return True

        has_scheduling_target = (
            self._match_doctor_in_message(normalized_message) is not None
            or self._match_specialty_in_message(normalized_message) is not None
        )
        if not has_scheduling_target:
            return False

        if date_parsing is None:
            return False

        status = date_parsing.get("status")
        return status in {
            DateParseStatus.INVALID.value,
            DateParseStatus.AMBIGUOUS.value,
            DateParseStatus.UNSUPPORTED.value,
        }

    def _extract_iso_date(self, message: str) -> date | None:
        match = _ISO_DATE_PATTERN.search(message)

        if match is None:
            return None

        try:
            return date.fromisoformat(match.group(1))
        except ValueError:
            return None

    def _extract_date(self, message: str) -> date | None:
        return self._extract_iso_date(message)

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
            "requested_time_window",
        }

        return bool(relevant_updates & context_updates.keys())

    def _is_hold_request(
        self,
        normalized_message: str,
        merged_context: dict[str, Any],
        message: str,
    ) -> bool:
        offered_slots = merged_context.get("offered_slots") or []

        if offered_slots and "book" in normalized_message:
            return True

        if self.responder._contains_any(normalized_message, _HOLD_KEYWORDS):
            return True

        if self._message_has_time_pattern(normalized_message):
            return True

        return self._extract_iso_datetime(message) is not None

    def _handle_hold_flow(
        self,
        *,
        message: str,
        normalized_message: str,
        conversation: Conversation,
        merged_context: dict[str, Any],
        context_updates: dict[str, Any],
    ) -> ChatReceptionistReply:
        offered_slots = merged_context.get("offered_slots") or []

        if not offered_slots:
            return ChatReceptionistReply(
                intent=ChatReceptionistIntent.HOLD_MISSING_AVAILABILITY,
                content=(
                    "Please check availability first so I can hold one of the "
                    "available times."
                ),
                chat_context_updates=context_updates,
                hold_created=False,
            )

        selected_slot = self._select_offered_slot(
            message,
            normalized_message,
            offered_slots,
        )

        if selected_slot is None:
            if self._message_has_slot_selection_attempt(normalized_message, message):
                return ChatReceptionistReply(
                    intent=ChatReceptionistIntent.HOLD_SLOT_NOT_FOUND,
                    content=(
                        "I could not match that time to one of the available slots. "
                        "Please choose one of the listed times."
                    ),
                    chat_context_updates=context_updates,
                    hold_created=False,
                )

            return ChatReceptionistReply(
                intent=ChatReceptionistIntent.HOLD_REQUEST,
                content=(
                    "Please choose one of the listed times so I can hold it for you "
                    "temporarily."
                ),
                chat_context_updates=context_updates,
                hold_created=False,
            )

        owner_id = str(conversation.id)

        try:
            slot = self.scheduling.get_available_slot_for_hold(
                UUID(str(selected_slot["availability_slot_id"])),
            )
            hold = self.appointment_holds.create_hold(
                availability_slot_id=slot.id,
                doctor_id=slot.doctor_id,
                start_time=slot.start_time,
                end_time=slot.end_time,
                owner_id=owner_id,
            )
        except AvailabilitySlotNotFoundError:
            return ChatReceptionistReply(
                intent=ChatReceptionistIntent.HOLD_SLOT_NOT_FOUND,
                content=(
                    "I could not match that time to one of the available slots. "
                    "Please choose one of the listed times."
                ),
                chat_context_updates=context_updates,
                hold_created=False,
            )
        except (AvailabilitySlotUnavailableError, AppointmentSlotAlreadyHeldError):
            return ChatReceptionistReply(
                intent=ChatReceptionistIntent.HOLD_CONFLICT,
                content=(
                    "That time was just taken or is already being held. "
                    "Please choose another available time."
                ),
                chat_context_updates=context_updates,
                hold_created=False,
            )

        hold_expires_at = hold.created_at + timedelta(
            seconds=self.appointment_holds.ttl_seconds,
        )
        display_time = str(selected_slot.get("display_time", slot.start_time.strftime("%H:%M")))
        doctor_name = str(merged_context.get("selected_doctor_name", "the selected doctor"))

        return ChatReceptionistReply(
            intent=ChatReceptionistIntent.HOLD_CREATED,
            content=(
                f"I temporarily held {display_time} with {doctor_name}. "
                "This is not booked yet. To confirm, please provide the patient's "
                "full name, date of birth, phone, and email."
            ),
            chat_context_updates={
                **context_updates,
                "selected_availability_slot_id": str(slot.id),
                "selected_start_time": slot.start_time.isoformat(),
                "hold_id": str(hold.hold_id),
                "hold_expires_at": hold_expires_at.isoformat(),
                "hold_owner_id": owner_id,
            },
            hold_created=True,
            hold_id=str(hold.hold_id),
        )

    def _select_offered_slot(
        self,
        message: str,
        normalized_message: str,
        offered_slots: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        for keyword, index in _ORDINAL_SLOT_KEYWORDS.items():
            if keyword in normalized_message and index < len(offered_slots):
                return offered_slots[index]

        time_match = _TIME_PATTERN.search(normalized_message)
        if time_match is not None:
            normalized_time = self._normalize_time_text(
                int(time_match.group(1)),
                int(time_match.group(2)),
            )
            for offered_slot in offered_slots:
                if offered_slot.get("display_time") == normalized_time:
                    return offered_slot

        iso_datetime = self._extract_iso_datetime(message)
        if iso_datetime is not None:
            for offered_slot in offered_slots:
                if offered_slot.get("start_time") == iso_datetime.isoformat():
                    return offered_slot

        return None

    def _message_has_slot_selection_attempt(
        self,
        normalized_message: str,
        message: str,
    ) -> bool:
        if any(keyword in normalized_message for keyword in _ORDINAL_SLOT_KEYWORDS):
            return True

        if self._message_has_time_pattern(normalized_message):
            return True

        return self._extract_iso_datetime(message) is not None

    def _message_has_time_pattern(self, normalized_message: str) -> bool:
        return _TIME_PATTERN.search(normalized_message) is not None

    def _normalize_time_text(self, hour: int, minute: int) -> str:
        return f"{hour:02d}:{minute:02d}"

    def _extract_iso_datetime(self, message: str) -> datetime | None:
        match = _ISO_DATETIME_PATTERN.search(message)

        if match is None:
            return None

        raw_value = match.group(1).replace(" ", "T")

        try:
            parsed = datetime.fromisoformat(raw_value)
        except ValueError:
            return None

        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)

        return parsed

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
        if not names:
            return ""

        if len(names) == 1:
            return names[0]

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
