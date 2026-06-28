from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from redis.exceptions import RedisError

from app.ai.receptionist_output import ReceptionistLLMIntent
from app.ai.reliability import LLMFailureReason
from app.core.request_context import get_correlation_id, get_request_id
from app.domain.conversations.enums import (
    ConversationChannel,
    ConversationMessageRole,
    ConversationStatus,
)
from app.domain.human_escalations import (
    HumanEscalationReason,
    HumanEscalationSource,
)
from app.domain.receptionist.enums import ReceptionistResponseMode
from app.domain.scheduling.appointment_holds import AppointmentHold
from app.domain.scheduling.availability import AvailabilityCheckStatus
from app.domain.scheduling.expressions import (
    DateExpression,
    DateExpressionKind,
    DateResolutionStatus,
)
from app.models.conversations import Conversation, ConversationMessage
from app.models.scheduling import AvailabilitySlot, Doctor, Patient, Specialty
from app.schemas.retell_tools import CheckAvailabilityToolArguments
from app.schemas.scheduling_expressions import DateExpressionSchema
from app.services.appointment_booking import (
    AppointmentBookingRequest,
    AppointmentBookingResult,
    AppointmentBookingService,
    AppointmentSlotAlreadyBookedError,
    BookingAvailabilitySlotNotFoundError,
    BookingAvailabilitySlotUnavailableError,
    BookingDoctorNotFoundError,
    BookingPatientNotFoundError,
)
from app.services.appointment_cancellation import AppointmentCancellationService
from app.services.appointment_holds import (
    AppointmentHoldMismatchError,
    AppointmentHoldNotFoundError,
    AppointmentHoldOwnershipError,
    AppointmentHoldService,
    AppointmentHoldServiceError,
    AppointmentHoldStoreUnavailableError,
    AppointmentSlotAlreadyHeldError,
)
from app.services.appointment_rescheduling import AppointmentReschedulingService
from app.services.appointment_time_normalization import (
    normalize_appointment_time_expression,
)
from app.services.chat_appointment_cancellation import (
    _CANCELLATION_APPOINTMENT_SELECTION_NO_MATCH,
    _CANCELLATION_CONFIRMATION_REPROMPT,
    _CANCELLATION_IDENTITY_DOB_ONLY_MESSAGE,
    _CANCELLATION_IDENTITY_NAME_ONLY_MESSAGE,
    _CANCELLATION_IDENTITY_REPROMPT_MESSAGE,
    APPOINTMENT_MANAGEMENT_AWAITING_APPOINTMENT_SELECTION,
    APPOINTMENT_MANAGEMENT_AWAITING_CANCELLATION_CONFIRMATION,
    APPOINTMENT_MANAGEMENT_AWAITING_COMPLETED,
    APPOINTMENT_MANAGEMENT_AWAITING_PATIENT_IDENTITY,
    APPOINTMENT_MANAGEMENT_MODE_CANCEL,
    CancellationFlowResult,
    ChatAppointmentCancellationOrchestrator,
)
from app.services.chat_appointment_intake import (
    APPOINTMENT_AVAILABILITY_RANGE_NEXT_WEEK,
    APPOINTMENT_AVAILABILITY_RANGE_THIS_WEEK,
    APPOINTMENT_INTAKE_AWAITING_DATE_OR_TIME,
    APPOINTMENT_INTAKE_AWAITING_SLOT_SELECTION,
    EARLIEST_AVAILABILITY_SEARCH_HORIZON_DAYS,
    ChatAppointmentIntakeOrchestrator,
    ChatAppointmentIntakeResult,
)
from app.services.chat_appointment_lookup import (
    _LOOKUP_IDENTITY_DOB_ONLY_MESSAGE,
    _LOOKUP_IDENTITY_ENTRY_MESSAGE,
    _LOOKUP_IDENTITY_NAME_ONLY_MESSAGE,
    _LOOKUP_IDENTITY_REPROMPT_MESSAGE,
    APPOINTMENT_MANAGEMENT_MODE_LOOKUP,
    ChatAppointmentLookupOrchestrator,
    LookupFlowResult,
    is_appointment_lookup_message,
)
from app.services.chat_appointment_lookup import (
    APPOINTMENT_MANAGEMENT_AWAITING_PATIENT_IDENTITY as LOOKUP_AWAITING_PATIENT_IDENTITY,
)
from app.services.chat_appointment_rescheduling import (
    APPOINTMENT_MANAGEMENT_AWAITING_NEW_SLOT_SELECTION,
    APPOINTMENT_MANAGEMENT_AWAITING_NEW_TIME_PREFERENCE,
    APPOINTMENT_MANAGEMENT_AWAITING_RESCHEDULE_CONFIRMATION,
    APPOINTMENT_MANAGEMENT_MODE_RESCHEDULE,
    RESCHEDULE_APPOINTMENT_SELECTION_NO_MATCH,
    RESCHEDULE_CONFIRMATION_REPROMPT_STUB,
    RESCHEDULE_IDENTITY_DOB_ONLY_MESSAGE,
    RESCHEDULE_IDENTITY_ENTRY_MESSAGE,
    RESCHEDULE_IDENTITY_NAME_ONLY_MESSAGE,
    RESCHEDULE_IDENTITY_REPROMPT_MESSAGE,
    RESCHEDULE_NEW_SLOT_SELECTION_REPROMPT,
    RESCHEDULE_NEW_TIME_PREFERENCE_REPROMPT,
    ChatAppointmentReschedulingOrchestrator,
    RescheduleFlowResult,
)
from app.services.chat_booking_identity import (
    APPOINTMENT_MANAGEMENT_EMPTY_FOLLOWUP_KEY,
    APPOINTMENT_MANAGEMENT_EMPTY_OFFER_BOOKING,
    APPOINTMENT_MANAGEMENT_EMPTY_OFFER_HELP,
    APPOINTMENT_MANAGEMENT_IDENTITY_KEY,
    BookingIdentityFlowResult,
    ChatBookingIdentityOrchestrator,
    ChatBookingIdentityStep,
    ParsedPatientFields,
    appointment_management_missing_identity_prompt,
    build_resolved_patient_context_updates,
    read_resolved_patient_context,
)
from app.services.chat_confirmation import (
    ConfirmationType,
    is_confirmation_confirmed,
    is_simple_affirmative,
    normalize_email_address,
    normalize_patient_display_name,
)
from app.services.chat_turn_understanding_interpreter import ChatTurnUnderstandingInterpreter
from app.services.chat_turn_understanding_records import ChatTurnUnderstandingRecordService
from app.services.clinic_time import (
    ClinicTimeService,
    format_clinic_local_time_label,
    to_clinic_local_datetime,
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
from app.services.patient_identity_resolution import PatientIdentityResolutionService
from app.services.post_booking_turn import (
    DeterministicPostBookingTurnClassifier,
    PostBookingTurnClassifier,
    PostBookingTurnDecision,
    PostBookingTurnUnderstanding,
)
from app.services.post_cancellation_turn import (
    PostCancellationTurnDecision,
    classify_post_cancellation_turn,
)
from app.services.post_completion_turn_classification import (
    POST_COMPLETION_ACTIONABLE_DECISIONS,
    PostCompletionTurnDecision,
    classify_post_completion_turn,
)
from app.services.post_reschedule_turn import (
    PostRescheduleTurnDecision,
    classify_post_reschedule_turn,
)
from app.services.receptionist_response_generator import (
    DeterministicReceptionistResponseGenerator,
    ReceptionistResponseGenerator,
)
from app.services.receptionist_response_planning import (
    chat_reply_snapshot_from_reply,
    render_chat_reply,
)
from app.services.scheduling import (
    AvailabilitySlotNotFoundError,
    AvailabilitySlotUnavailableError,
    DoctorAttributedAvailabilitySlot,
    SchedulingService,
    SpecialtyAvailabilityCheckResult,
)
from app.services.scheduling_availability import SchedulingAvailabilityResolver
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

logger = logging.getLogger(__name__)

_ISO_DATE_PATTERN = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_TIME_PATTERN = re.compile(r"\b(\d{1,2}):(\d{2})\b")
# A message that is exactly an integer (e.g. ``1`` or ``10``), used for
# slot-aware option-number / clock-time interpretation during slot selection.
_BARE_INTEGER_PATTERN = re.compile(r"\d{1,2}")
_DR_MENTION_PATTERN = re.compile(r"\bdr\.?\s+[a-z]", re.IGNORECASE)
_SPECIALTY_MENTION_PATTERN = re.compile(
    r"\b\w+(?:ology|iatry|surgery|ologist)\b",
    re.IGNORECASE,
)
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
_RESCHEDULE_KEYWORDS = [
    "reschedule",
    "move appointment",
    "move my appointment",
    "move an appointment",
]
_CANCELLATION_IDENTITY_ENTRY_MESSAGE = (
    "Of course. I can look it up first. What is the patient's full name and date of birth?"
)
# Phrases that signal the request is for a different patient than the one already
# resolved in the conversation. Kept intentionally small; this is not broad
# family-member support, just a guard against silently reusing the wrong patient.
_DIFFERENT_PATIENT_PHRASES = (
    "someone else",
    "somebody else",
    "different patient",
    "another patient",
    "different person",
    "another person",
    "not me",
    "not for me",
    "it's for my",
    "it is for my",
)
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
_NATURAL_DOB_PATTERN = re.compile(
    r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|"
    r"nov(?:ember)?|dec(?:ember)?)\s+(\d{1,2})(?:st|nd|rd|th)?\s*,?\s*(\d{4})\b",
    re.IGNORECASE,
)
_MONTH_TOKEN_TO_NUMBER = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
_PATIENT_IDENTITY_FIELD_LABELS = {
    "full_name": "full name",
    "date_of_birth": "date of birth",
    "phone": "phone",
    "email": "email",
}
_SLOT_FILLING_MIN_CONFIDENCE = 0.65
_RECENT_MESSAGES_LIMIT = 25
_HUMAN_HANDOFF_MESSAGE = (
    "I'll mark this conversation for human follow-up. A human receptionist can review it."
)
_HANDOFF_CONTEXT_KEYS = (
    "hold_id",
    "hold_expires_at",
    "selected_doctor_name",
    "requested_date",
    "selected_start_time",
)
_ESCALATION_SUGGESTION_SUFFIX = " If you prefer, I can transfer this to a human receptionist."
_DATE_CLARIFICATION_MESSAGE = (
    "What day works best? You can say something like tomorrow or next Monday."
)
_TIME_PREFERENCE_CLARIFICATION_MESSAGE = (
    "Please specify a time-of-day preference such as morning, afternoon, or "
    "evening, or provide an exact time in HH:MM format."
)
_SELECTED_TIME_STILL_AVAILABLE_SUFFIX = " That time is still available."
_HELD_TIME_PREFERENCE_CLARIFICATION_MESSAGE = (
    "You already have a time held. Please complete or release that hold before "
    "changing your time-of-day preference."
)
_APPOINTMENT_CLARIFICATION_FALLBACK_MESSAGE = (
    "Could you tell me a bit more about the appointment you are looking for?"
)
_APPOINTMENT_INTAKE_REPROMPT_MESSAGE = (
    "What day or time would you like me to check?"
)
_GENERIC_SCHEDULING_FALLBACK_MESSAGE = (
    "I can help with clinic scheduling questions. Please tell me whether "
    "you want to book, cancel, or reschedule an appointment."
)
_SLOT_SELECTION_REPROMPT_MESSAGE = (
    "Please choose one of the appointment times I offered."
)
_FINAL_BOOKING_CONFIRMATION_REPROMPT_MESSAGE = (
    "Please confirm whether you want me to book that appointment."
)
_ALREADY_CONFIRMED_MESSAGE = (
    "Your appointment is already confirmed. "
    "Is there anything else I can help with?"
)
_POST_BOOKING_CLOSING_MESSAGE = "You're all set. Have a great day!"
_BOOKING_SUCCESS_FOLLOW_UP_SUFFIX = " Is there anything else I can help with?"
_POST_BOOKING_NEEDS_MORE_HELP_MESSAGE = (
    "Sure — would you like to schedule, cancel, or reschedule an appointment?"
)
_POST_BOOKING_UNKNOWN_MESSAGE = (
    "Your appointment is confirmed. Would you like to schedule, cancel, "
    "or reschedule anything else?"
)
_POST_CANCELLATION_UNKNOWN_MESSAGE = (
    "Your appointment has been cancelled. Would you like to schedule, cancel, "
    "or reschedule anything else?"
)
_POST_RESCHEDULE_UNKNOWN_MESSAGE = (
    "Your appointment has been rescheduled. Would you like to schedule, cancel, "
    "or reschedule anything else?"
)


def _format_offered_slot_selection_reprompt(offered_slots: list[Any]) -> str:
    display_times: list[str] = []
    for offered_slot in offered_slots:
        if not isinstance(offered_slot, dict):
            continue
        display_time = offered_slot.get("display_time")
        if isinstance(display_time, str) and display_time:
            display_times.append(display_time)
        if len(display_times) >= 3:
            break

    if display_times:
        examples = ", ".join(display_times[:3])
        return f"Please choose one of the offered times, such as {examples}."
    return _SLOT_SELECTION_REPROMPT_MESSAGE


def _format_date_or_time_reprompt(chat_context: dict[str, Any]) -> str:
    doctor_name = chat_context.get("selected_doctor_name")
    if isinstance(doctor_name, str) and doctor_name.strip():
        return f"What day or time works best for {doctor_name.strip()}?"

    specialty_name = chat_context.get("selected_specialty_name")
    if isinstance(specialty_name, str) and specialty_name.strip():
        return f"What day or time works best for {specialty_name.strip()}?"

    return _APPOINTMENT_INTAKE_REPROMPT_MESSAGE


def _booking_identity_step_reprompt(
    step: ChatBookingIdentityStep,
    chat_context: dict[str, Any],
) -> tuple[ChatReceptionistIntent, str] | None:
    if step is ChatBookingIdentityStep.AWAIT_FINAL_BOOKING_CONFIRMATION:
        return (
            ChatReceptionistIntent.BOOKING_CONFIRMATION_REQUIRED,
            _FINAL_BOOKING_CONFIRMATION_REPROMPT_MESSAGE,
        )

    if step is ChatBookingIdentityStep.ASK_SEEN_BEFORE:
        return (
            ChatReceptionistIntent.PATIENT_IDENTITY_PARTIAL,
            "Have you been seen here before?",
        )

    if step is ChatBookingIdentityStep.COLLECT_EXISTING_IDENTITY:
        return (
            ChatReceptionistIntent.BOOKING_IDENTITY_MISSING,
            "I still need the patient's full name and date of birth.",
        )

    if step is ChatBookingIdentityStep.COLLECT_NEW_NAME:
        return (
            ChatReceptionistIntent.BOOKING_IDENTITY_MISSING,
            "What name should I put on the appointment?",
        )

    if step is ChatBookingIdentityStep.COLLECT_NEW_DOB:
        return (
            ChatReceptionistIntent.BOOKING_IDENTITY_MISSING,
            "What is your date of birth?",
        )

    if step in {
        ChatBookingIdentityStep.COLLECT_NEW_EMAIL,
        ChatBookingIdentityStep.COLLECT_CONFIRMATION_EMAIL,
    }:
        return (
            ChatReceptionistIntent.BOOKING_IDENTITY_MISSING,
            "What email should we use for the confirmation?",
        )

    if step in {
        ChatBookingIdentityStep.CONFIRM_NEW_EMAIL,
        ChatBookingIdentityStep.CONFIRM_CONFIRMATION_EMAIL,
    }:
        pending_email = chat_context.get("pending_confirmation_email")
        if isinstance(pending_email, str) and pending_email.strip():
            return (
                ChatReceptionistIntent.PATIENT_IDENTITY_PARTIAL,
                f"I heard {pending_email.strip()} — is that correct?",
            )
        return (
            ChatReceptionistIntent.PATIENT_IDENTITY_PARTIAL,
            "Please confirm whether that email is correct.",
        )

    if step is ChatBookingIdentityStep.CONFIRM_POSSIBLE_MATCH:
        return (
            ChatReceptionistIntent.BOOKING_IDENTITY_MISSING,
            "Please let me know if that profile is yours.",
        )

    return None


def _build_contextual_fallback_reply(chat_context: dict[str, Any]) -> str | None:
    resolved = _resolve_contextual_fallback_reply(chat_context)
    if resolved is None:
        return None
    return resolved[1]


def _is_appointment_lookup_request(normalized_message: str) -> bool:
    return is_appointment_lookup_message(normalized_message)


def _cleared_appointment_management_completed_flow_updates() -> dict[str, Any]:
    """Clear stale appointment-management frame keys after a completed flow."""
    return {
        "appointment_management_mode": None,
        "appointment_management_awaiting": None,
        APPOINTMENT_MANAGEMENT_EMPTY_FOLLOWUP_KEY: None,
        "cancellation_status": None,
        "reschedule_status": None,
        "lookup_status": None,
        "offered_appointments": None,
        "offered_slots": [],
        "appointment_intake_awaiting": None,
    }


def _cleared_post_conversation_terminal_updates() -> dict[str, Any]:
    """Clear completed-flow keys after the user closes the conversation."""
    return {
        **_cleared_appointment_management_completed_flow_updates(),
        "appointment_id": None,
        "booking_identity_step": None,
    }


def _cleared_completed_flow_for_new_intent_updates() -> dict[str, Any]:
    """Clear completed-flow keys so a new intent can start in the same conversation."""
    return {
        **_cleared_appointment_management_completed_flow_updates(),
        "appointment_id": None,
        "booking_identity_step": None,
    }


def _post_completion_decision_is_actionable(
    decision: PostCompletionTurnDecision,
) -> bool:
    return decision in POST_COMPLETION_ACTIONABLE_DECISIONS


def _parse_natural_date_of_birth(message: str) -> str | None:
    match = _NATURAL_DOB_PATTERN.search(message)
    if match is None:
        return None

    month_token = match.group(1).lower()[:3]
    month = _MONTH_TOKEN_TO_NUMBER.get(month_token)
    if month is None:
        return None

    day = int(match.group(2))
    year = int(match.group(3))
    try:
        date(year, month, day)
    except ValueError:
        return None

    return f"{year}-{month:02d}-{day:02d}"


def _is_awaiting_reschedule_patient_identity(chat_context: dict[str, Any]) -> bool:
    return (
        chat_context.get("appointment_management_mode")
        == APPOINTMENT_MANAGEMENT_MODE_RESCHEDULE
        and chat_context.get("appointment_management_awaiting")
        == APPOINTMENT_MANAGEMENT_AWAITING_PATIENT_IDENTITY
    )


def _is_awaiting_reschedule_appointment_selection(chat_context: dict[str, Any]) -> bool:
    return (
        chat_context.get("appointment_management_mode")
        == APPOINTMENT_MANAGEMENT_MODE_RESCHEDULE
        and chat_context.get("appointment_management_awaiting")
        == APPOINTMENT_MANAGEMENT_AWAITING_APPOINTMENT_SELECTION
    )


def _is_awaiting_reschedule_new_time_preference(chat_context: dict[str, Any]) -> bool:
    return (
        chat_context.get("appointment_management_mode")
        == APPOINTMENT_MANAGEMENT_MODE_RESCHEDULE
        and chat_context.get("appointment_management_awaiting")
        == APPOINTMENT_MANAGEMENT_AWAITING_NEW_TIME_PREFERENCE
    )


def _is_awaiting_reschedule_new_slot_selection(chat_context: dict[str, Any]) -> bool:
    return (
        chat_context.get("appointment_management_mode")
        == APPOINTMENT_MANAGEMENT_MODE_RESCHEDULE
        and chat_context.get("appointment_management_awaiting")
        == APPOINTMENT_MANAGEMENT_AWAITING_NEW_SLOT_SELECTION
    )


def _is_awaiting_reschedule_confirmation(chat_context: dict[str, Any]) -> bool:
    return (
        chat_context.get("appointment_management_mode")
        == APPOINTMENT_MANAGEMENT_MODE_RESCHEDULE
        and chat_context.get("appointment_management_awaiting")
        == APPOINTMENT_MANAGEMENT_AWAITING_RESCHEDULE_CONFIRMATION
    )


def _is_awaiting_cancellation_patient_identity(chat_context: dict[str, Any]) -> bool:
    return (
        chat_context.get("appointment_management_mode") == APPOINTMENT_MANAGEMENT_MODE_CANCEL
        and chat_context.get("appointment_management_awaiting")
        == APPOINTMENT_MANAGEMENT_AWAITING_PATIENT_IDENTITY
    )


def _is_awaiting_cancellation_appointment_selection(chat_context: dict[str, Any]) -> bool:
    return (
        chat_context.get("appointment_management_mode") == APPOINTMENT_MANAGEMENT_MODE_CANCEL
        and chat_context.get("appointment_management_awaiting")
        == APPOINTMENT_MANAGEMENT_AWAITING_APPOINTMENT_SELECTION
    )


def _is_awaiting_cancellation_confirmation(chat_context: dict[str, Any]) -> bool:
    return (
        chat_context.get("appointment_management_mode") == APPOINTMENT_MANAGEMENT_MODE_CANCEL
        and chat_context.get("appointment_management_awaiting")
        == APPOINTMENT_MANAGEMENT_AWAITING_CANCELLATION_CONFIRMATION
    )


def _is_in_post_cancellation_frame(chat_context: dict[str, Any]) -> bool:
    if chat_context.get("appointment_management_mode") != APPOINTMENT_MANAGEMENT_MODE_CANCEL:
        return False
    if (
        chat_context.get("appointment_management_awaiting")
        != APPOINTMENT_MANAGEMENT_AWAITING_COMPLETED
    ):
        return False
    if chat_context.get("cancellation_status") != "cancelled":
        return False
    if chat_context.get("hold_id"):
        return False
    if chat_context.get("appointment_intake_awaiting"):
        return False
    booking_step = chat_context.get("booking_identity_step")
    if isinstance(booking_step, str) and booking_step != (
        ChatBookingIdentityStep.BOOKING_COMPLETED.value
    ):
        return False
    return True


def _is_in_post_booking_follow_up_frame(chat_context: dict[str, Any]) -> bool:
    if not chat_context.get("appointment_id"):
        return False
    if chat_context.get("hold_id"):
        return False
    if chat_context.get("appointment_intake_awaiting"):
        return False
    booking_step = chat_context.get("booking_identity_step")
    if isinstance(booking_step, str) and booking_step != (
        ChatBookingIdentityStep.BOOKING_COMPLETED.value
    ):
        return False
    return True


def _is_awaiting_lookup_patient_identity(chat_context: dict[str, Any]) -> bool:
    return (
        chat_context.get("appointment_management_mode") == APPOINTMENT_MANAGEMENT_MODE_LOOKUP
        and chat_context.get("appointment_management_awaiting")
        == LOOKUP_AWAITING_PATIENT_IDENTITY
    )


def _is_in_lookup_flow(chat_context: dict[str, Any]) -> bool:
    return _is_awaiting_lookup_patient_identity(chat_context)


def _is_in_cancellation_flow(chat_context: dict[str, Any]) -> bool:
    if chat_context.get("appointment_management_mode") != APPOINTMENT_MANAGEMENT_MODE_CANCEL:
        return False
    return (
        chat_context.get("appointment_management_awaiting")
        != APPOINTMENT_MANAGEMENT_AWAITING_COMPLETED
    )


def _is_in_reschedule_flow(chat_context: dict[str, Any]) -> bool:
    if chat_context.get("appointment_management_mode") != APPOINTMENT_MANAGEMENT_MODE_RESCHEDULE:
        return False
    return (
        chat_context.get("appointment_management_awaiting")
        != APPOINTMENT_MANAGEMENT_AWAITING_COMPLETED
    )


def _is_in_post_reschedule_frame(chat_context: dict[str, Any]) -> bool:
    if chat_context.get("appointment_management_mode") != APPOINTMENT_MANAGEMENT_MODE_RESCHEDULE:
        return False
    if (
        chat_context.get("appointment_management_awaiting")
        != APPOINTMENT_MANAGEMENT_AWAITING_COMPLETED
    ):
        return False
    if chat_context.get("reschedule_status") != "rescheduled":
        return False
    if chat_context.get("hold_id"):
        return False
    if chat_context.get("appointment_intake_awaiting"):
        return False
    booking_step = chat_context.get("booking_identity_step")
    if isinstance(booking_step, str) and booking_step != (
        ChatBookingIdentityStep.BOOKING_COMPLETED.value
    ):
        return False
    return True


def _appointment_management_empty_followup(chat_context: dict[str, Any]) -> str | None:
    """Return the empty-state follow-up mode, if the turn is in that frame.

    After an appointment-management flow resolves a patient but finds nothing to
    list/cancel/reschedule, it parks in a safe completed state and records how an
    affirmative reply should be honored. This frame lets the next turn accept
    yes/no, a fresh intent, or a farewell without re-asking for patient identity.
    """
    if (
        chat_context.get("appointment_management_awaiting")
        != APPOINTMENT_MANAGEMENT_AWAITING_COMPLETED
    ):
        return None
    if chat_context.get("hold_id"):
        return None
    if chat_context.get("appointment_intake_awaiting"):
        return None
    followup = chat_context.get(APPOINTMENT_MANAGEMENT_EMPTY_FOLLOWUP_KEY)
    if followup in {
        APPOINTMENT_MANAGEMENT_EMPTY_OFFER_BOOKING,
        APPOINTMENT_MANAGEMENT_EMPTY_OFFER_HELP,
    }:
        return str(followup)
    return None


def _resolve_contextual_fallback_reply(
    chat_context: dict[str, Any],
) -> tuple[ChatReceptionistIntent, str] | None:
    if _is_awaiting_reschedule_patient_identity(chat_context):
        identity_raw = chat_context.get(APPOINTMENT_MANAGEMENT_IDENTITY_KEY)
        identity = identity_raw if isinstance(identity_raw, dict) else {}
        prompt = appointment_management_missing_identity_prompt(
            identity,
            both_prompt=RESCHEDULE_IDENTITY_REPROMPT_MESSAGE,
            name_prompt=RESCHEDULE_IDENTITY_NAME_ONLY_MESSAGE,
            dob_prompt=RESCHEDULE_IDENTITY_DOB_ONLY_MESSAGE,
        )
        return (
            ChatReceptionistIntent.RESCHEDULE_REQUEST,
            prompt or RESCHEDULE_IDENTITY_REPROMPT_MESSAGE,
        )

    if _is_awaiting_reschedule_appointment_selection(chat_context):
        return (
            ChatReceptionistIntent.RESCHEDULE_REQUEST,
            RESCHEDULE_APPOINTMENT_SELECTION_NO_MATCH,
        )

    if _is_awaiting_reschedule_new_time_preference(chat_context):
        return (
            ChatReceptionistIntent.RESCHEDULE_REQUEST,
            RESCHEDULE_NEW_TIME_PREFERENCE_REPROMPT,
        )

    if _is_awaiting_reschedule_new_slot_selection(chat_context):
        return (
            ChatReceptionistIntent.RESCHEDULE_REQUEST,
            RESCHEDULE_NEW_SLOT_SELECTION_REPROMPT,
        )

    if _is_awaiting_reschedule_confirmation(chat_context):
        return (
            ChatReceptionistIntent.RESCHEDULE_REQUEST,
            RESCHEDULE_CONFIRMATION_REPROMPT_STUB,
        )

    if _is_awaiting_cancellation_patient_identity(chat_context):
        identity_raw = chat_context.get(APPOINTMENT_MANAGEMENT_IDENTITY_KEY)
        identity = identity_raw if isinstance(identity_raw, dict) else {}
        prompt = appointment_management_missing_identity_prompt(
            identity,
            both_prompt=_CANCELLATION_IDENTITY_REPROMPT_MESSAGE,
            name_prompt=_CANCELLATION_IDENTITY_NAME_ONLY_MESSAGE,
            dob_prompt=_CANCELLATION_IDENTITY_DOB_ONLY_MESSAGE,
        )
        return (
            ChatReceptionistIntent.CANCEL_REQUEST,
            prompt or _CANCELLATION_IDENTITY_REPROMPT_MESSAGE,
        )

    if _is_awaiting_cancellation_appointment_selection(chat_context):
        return (
            ChatReceptionistIntent.CANCEL_REQUEST,
            _CANCELLATION_APPOINTMENT_SELECTION_NO_MATCH,
        )

    if _is_awaiting_cancellation_confirmation(chat_context):
        return (
            ChatReceptionistIntent.CANCEL_REQUEST,
            _CANCELLATION_CONFIRMATION_REPROMPT,
        )

    if _is_awaiting_lookup_patient_identity(chat_context):
        identity_raw = chat_context.get(APPOINTMENT_MANAGEMENT_IDENTITY_KEY)
        identity = identity_raw if isinstance(identity_raw, dict) else {}
        prompt = appointment_management_missing_identity_prompt(
            identity,
            both_prompt=_LOOKUP_IDENTITY_REPROMPT_MESSAGE,
            name_prompt=_LOOKUP_IDENTITY_NAME_ONLY_MESSAGE,
            dob_prompt=_LOOKUP_IDENTITY_DOB_ONLY_MESSAGE,
        )
        return (
            ChatReceptionistIntent.LIST_APPOINTMENTS,
            prompt or _LOOKUP_IDENTITY_REPROMPT_MESSAGE,
        )

    if chat_context.get("appointment_id"):
        return None

    step_raw = chat_context.get("booking_identity_step")
    if isinstance(step_raw, str):
        try:
            step = ChatBookingIdentityStep(step_raw)
        except ValueError:
            step = None
        if step is not None and step is not ChatBookingIdentityStep.BOOKING_COMPLETED:
            identity_reprompt = _booking_identity_step_reprompt(step, chat_context)
            if identity_reprompt is not None:
                return identity_reprompt

    offered_slots = chat_context.get("offered_slots") or []
    awaiting = chat_context.get("appointment_intake_awaiting")
    if offered_slots or awaiting == APPOINTMENT_INTAKE_AWAITING_SLOT_SELECTION:
        return (
            ChatReceptionistIntent.APPOINTMENT_REQUEST,
            _format_offered_slot_selection_reprompt(offered_slots),
        )

    if (
        awaiting == APPOINTMENT_INTAKE_AWAITING_DATE_OR_TIME
        or chat_context.get("selected_doctor_id")
        or chat_context.get("selected_specialty_id")
    ):
        return (
            ChatReceptionistIntent.APPOINTMENT_REQUEST,
            _format_date_or_time_reprompt(chat_context),
        )

    return None


class ChatReceptionistIntent(StrEnum):
    GREETING = "greeting"
    APPOINTMENT_REQUEST = "appointment_request"
    CANCEL_REQUEST = "cancel_request"
    RESCHEDULE_REQUEST = "reschedule_request"
    LIST_APPOINTMENTS = "list_appointments"
    EMERGENCY = "emergency"
    LIST_SPECIALTIES = "list_specialties"
    LIST_DOCTORS = "list_doctors"
    SPECIALTY_DOCTORS = "specialty_doctors"
    AVAILABILITY_REQUEST = "availability_request"
    AVAILABILITY_MISSING_DATE = "availability_missing_date"
    AVAILABILITY_MISSING_DOCTOR = "availability_missing_doctor"
    AVAILABILITY_UNKNOWN_SPECIALTY = "availability_unknown_specialty"
    AVAILABILITY_UNKNOWN_DOCTOR = "availability_unknown_doctor"
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
        ChatReceptionistIntent.AVAILABILITY_UNKNOWN_SPECIALTY,
        ChatReceptionistIntent.AVAILABILITY_UNKNOWN_DOCTOR,
        ChatReceptionistIntent.AVAILABILITY_RESULTS,
        ChatReceptionistIntent.AVAILABILITY_NO_SLOTS,
        ChatReceptionistIntent.AVAILABILITY_NO_MATCHING_TIME_WINDOW,
        ChatReceptionistIntent.INVALID_DATE,
        ChatReceptionistIntent.INVALID_TIME_PREFERENCE,
    }
)
_MAX_OFFERED_SLOTS = 5
_EARLIEST_NO_AVAILABILITY_MESSAGE = (
    "I'm not seeing openings for that request. "
    "Would you like me to check another day or a different time window?"
)
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
    return is_confirmation_confirmed(
        confirmation_type=ConfirmationType.FINAL_BOOKING_CONFIRMATION,
        message=message,
    )


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
class _OfferedSlotSelection:
    slot: dict[str, Any] | None = None
    ambiguous: bool = False


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
            content=_GENERIC_SCHEDULING_FALLBACK_MESSAGE,
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
        clinic_time_service: ClinicTimeService | None = None,
        response_generator: ReceptionistResponseGenerator | None = None,
        response_generation_mode: ReceptionistResponseMode = (
            ReceptionistResponseMode.DETERMINISTIC
        ),
        patient_identity_resolution: PatientIdentityResolutionService,
        appointment_cancellation: AppointmentCancellationService,
        appointment_rescheduling: AppointmentReschedulingService,
        chat_turn_understanding_records: ChatTurnUnderstandingRecordService | None = None,
        chat_turn_understanding_interpreter: ChatTurnUnderstandingInterpreter | None = None,
        post_booking_turn_classifier: PostBookingTurnClassifier | None = None,
        chat_appointment_hold_ttl_seconds: int = 600,
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
        self.clinic_time_service = clinic_time_service
        self.response_generator = response_generator or DeterministicReceptionistResponseGenerator()
        self.response_generation_mode = response_generation_mode
        self.patient_identity_resolution = patient_identity_resolution
        self.chat_turn_understanding_records = chat_turn_understanding_records
        effective_clinic_time = clinic_time_service or scheduling._clinic_time_service
        if effective_clinic_time is None:
            msg = "clinic_time_service is required for appointment cancellation"
            raise ValueError(msg)
        self._booking_identity = ChatBookingIdentityOrchestrator(
            patient_identity_resolution=patient_identity_resolution,
            chat_turn_understanding_interpreter=chat_turn_understanding_interpreter,
            clinic_timezone=effective_clinic_time.timezone,
        )
        self._appointment_cancellation = ChatAppointmentCancellationOrchestrator(
            patient_identity_resolution=patient_identity_resolution,
            appointments=scheduling.appointments,
            appointment_cancellation=appointment_cancellation,
            scheduling_metadata=scheduling,
            clinic_time_service=effective_clinic_time,
            chat_turn_understanding_interpreter=chat_turn_understanding_interpreter,
        )
        self._appointment_intake = ChatAppointmentIntakeOrchestrator(
            scheduling=scheduling,
            date_parser=date_parser,
            time_preference_parser=time_preference_parser,
            clinic_time_service=clinic_time_service,
            chat_turn_understanding_interpreter=chat_turn_understanding_interpreter,
        )
        self._post_booking_turn_classifier = (
            post_booking_turn_classifier or DeterministicPostBookingTurnClassifier()
        )
        self._chat_appointment_hold_ttl_seconds = chat_appointment_hold_ttl_seconds
        self._appointment_rescheduling = ChatAppointmentReschedulingOrchestrator(
            patient_identity_resolution=patient_identity_resolution,
            appointments=scheduling.appointments,
            scheduling=scheduling,
            scheduling_metadata=scheduling,
            clinic_time_service=effective_clinic_time,
            date_parser=date_parser,
            time_preference_parser=time_preference_parser,
            chat_turn_understanding_interpreter=chat_turn_understanding_interpreter,
            appointment_holds=appointment_holds,
            appointment_rescheduling=appointment_rescheduling,
            chat_appointment_hold_ttl_seconds=chat_appointment_hold_ttl_seconds,
        )
        self._appointment_lookup = ChatAppointmentLookupOrchestrator(
            patient_identity_resolution=patient_identity_resolution,
            appointments=scheduling.appointments,
            scheduling_metadata=scheduling,
            clinic_time_service=effective_clinic_time,
            chat_turn_understanding_interpreter=chat_turn_understanding_interpreter,
        )

    def _clinic_timezone(self) -> ZoneInfo:
        if self.clinic_time_service is not None:
            return self.clinic_time_service.timezone
        clinic_time = self.scheduling._clinic_time_service
        if clinic_time is not None:
            return clinic_time.timezone
        msg = "clinic_time_service is required for clinic-local time formatting"
        raise ValueError(msg)

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

        generated_response = render_chat_reply(
            chat_reply_snapshot_from_reply(reply),
            conversation=conversation,
            response_generator=self.response_generator,
            response_mode=self.response_generation_mode,
        )
        reply = replace(reply, content=generated_response.text)

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
                "failure_category": llm_analysis_result.failure_category.value,
                "prompt_version": llm_analysis_result.prompt_version,
                "model": llm_analysis_result.model,
                "provider": llm_analysis_result.provider,
                "primary_provider": llm_analysis_result.primary_provider,
                "fallback_provider": llm_analysis_result.fallback_provider,
                "used_fallback_provider": llm_analysis_result.used_fallback_provider,
                "input_tokens": llm_analysis_result.input_tokens,
                "output_tokens": llm_analysis_result.output_tokens,
                "estimated_cost_micros": llm_analysis_result.estimated_cost_micros,
                "latency_ms": llm_analysis_result.latency_ms,
                "attempt_count": llm_analysis_result.attempt_count,
                "primary_attempt_count": llm_analysis_result.primary_attempt_count,
                "fallback_attempt_count": llm_analysis_result.fallback_attempt_count,
                "used_repair": llm_analysis_result.used_repair,
            }
        if slot_filling_result is not None:
            assistant_metadata["slot_filling"] = slot_filling_result.to_metadata()
        if health_result is not None:
            assistant_metadata["conversation_health"] = health_result.to_metadata()
        if human_escalation_metadata is not None:
            assistant_metadata["human_escalation"] = human_escalation_metadata
        if human_handoff_notification_metadata is not None:
            assistant_metadata["human_handoff_notification"] = human_handoff_notification_metadata
        if reply.date_parsing is not None:
            assistant_metadata["date_parsing"] = reply.date_parsing
        if reply.time_preference_parsing is not None:
            assistant_metadata["time_preference"] = reply.time_preference_parsing
        assistant_metadata["response_generation"] = {
            "mode": generated_response.mode.value,
            "used_fallback": generated_response.used_fallback,
            **generated_response.metadata,
        }

        assistant_message = self.conversations.append_message(
            ConversationMessageCreate(
                conversation_id=conversation.id,
                role=ConversationMessageRole.ASSISTANT,
                content=reply.content,
                message_metadata=assistant_metadata,
            ),
        )

        if self.chat_turn_understanding_records is not None:
            self.chat_turn_understanding_records.record_best_effort(
                conversation_id=conversation.id,
                user_message_id=user_message.id,
                assistant_message_id=assistant_message.id,
                request_id=get_request_id(),
                correlation_id=get_correlation_id(),
                analysis_result=llm_analysis_result,
                slot_filling_result=slot_filling_result,
            )

        booking_confirmed = reply.booking_confirmed
        appointment_id = UUID(reply.appointment_id) if reply.appointment_id is not None else None

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
            human_handoff_notification_email_job_id=(human_handoff_notification_email_job_id),
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
            notification_result = self.human_handoff_notifications.create_or_get_notification_job(
                escalation=escalation,
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

        # If the user starts a new scheduling request while stuck in a stale final
        # booking confirmation whose hold is gone, clear the stale confirmation/hold
        # state so normal scheduling intake/routing can restart instead of looping
        # on the confirmation prompt.
        stale_confirmation_cleanup: dict[str, Any] = {}
        if self._should_override_stale_final_confirmation(
            existing_context=existing_context,
            message=message,
            normalized_message=normalized_message,
        ):
            stale_confirmation_cleanup = self._cleared_hold_context_updates()
            existing_context = {**existing_context, **stale_confirmation_cleanup}

        completed_flow_cleanup: dict[str, Any] = {}

        if _is_in_post_cancellation_frame(existing_context):
            cancellation_decision = self._resolve_post_cancellation_decision(
                message=message,
                chat_context=existing_context,
            )
            if cancellation_decision is PostCancellationTurnDecision.END_CONVERSATION:
                completed_flow_cleanup = _cleared_post_conversation_terminal_updates()
                return self._finish_reply_with_context(
                    self._post_cancellation_reply_for_decision(
                        decision=cancellation_decision,
                        context_updates=completed_flow_cleanup,
                    ),
                    stale_confirmation_cleanup=stale_confirmation_cleanup,
                    completed_flow_cleanup=completed_flow_cleanup,
                )
            if _post_completion_decision_is_actionable(
                PostCompletionTurnDecision(cancellation_decision.value),
            ):
                completed_flow_cleanup = _cleared_completed_flow_for_new_intent_updates()
                existing_context = {**existing_context, **completed_flow_cleanup}
            else:
                post_cancellation_reply = self._post_cancellation_reply_for_decision(
                    decision=cancellation_decision,
                    context_updates={},
                )
                if post_cancellation_reply is not None:
                    return self._finish_reply_with_context(
                        post_cancellation_reply,
                        stale_confirmation_cleanup=stale_confirmation_cleanup,
                        completed_flow_cleanup=completed_flow_cleanup,
                    )

        if _is_in_post_reschedule_frame(existing_context):
            reschedule_decision = self._resolve_post_reschedule_decision(
                message=message,
                chat_context=existing_context,
            )
            if reschedule_decision is PostRescheduleTurnDecision.END_CONVERSATION:
                completed_flow_cleanup = _cleared_post_conversation_terminal_updates()
                return self._finish_reply_with_context(
                    self._post_reschedule_reply_for_decision(
                        decision=reschedule_decision,
                        context_updates=completed_flow_cleanup,
                    ),
                    stale_confirmation_cleanup=stale_confirmation_cleanup,
                    completed_flow_cleanup=completed_flow_cleanup,
                )
            if _post_completion_decision_is_actionable(
                PostCompletionTurnDecision(reschedule_decision.value),
            ):
                completed_flow_cleanup = _cleared_completed_flow_for_new_intent_updates()
                existing_context = {**existing_context, **completed_flow_cleanup}
            else:
                post_reschedule_reply = self._post_reschedule_reply_for_decision(
                    decision=reschedule_decision,
                    context_updates={},
                )
                if post_reschedule_reply is not None:
                    return self._finish_reply_with_context(
                        post_reschedule_reply,
                        stale_confirmation_cleanup=stale_confirmation_cleanup,
                        completed_flow_cleanup=completed_flow_cleanup,
                    )

        if _is_in_post_booking_follow_up_frame(existing_context):
            post_booking_understanding = self._resolve_post_booking_decision(
                message=message,
                chat_context=existing_context,
            )
            booking_decision = post_booking_understanding.decision
            if booking_decision is PostBookingTurnDecision.END_CONVERSATION:
                completed_flow_cleanup = _cleared_post_conversation_terminal_updates()
                return self._finish_reply_with_context(
                    self._post_booking_reply_for_decision(
                        decision=booking_decision,
                        appointment_id=existing_context["appointment_id"],
                        hold_id=existing_context.get("hold_id"),
                        context_updates=completed_flow_cleanup,
                    ),
                    stale_confirmation_cleanup=stale_confirmation_cleanup,
                    completed_flow_cleanup=completed_flow_cleanup,
                )
            if _post_completion_decision_is_actionable(
                PostCompletionTurnDecision(booking_decision.value),
            ):
                completed_flow_cleanup = _cleared_completed_flow_for_new_intent_updates()
                existing_context = {**existing_context, **completed_flow_cleanup}
            else:
                post_booking_reply = self._post_booking_reply_for_decision(
                    decision=booking_decision,
                    appointment_id=existing_context["appointment_id"],
                    hold_id=existing_context.get("hold_id"),
                    context_updates={},
                )
                if post_booking_reply is not None:
                    return self._finish_reply_with_context(
                        post_booking_reply,
                        stale_confirmation_cleanup=stale_confirmation_cleanup,
                        completed_flow_cleanup=completed_flow_cleanup,
                    )

        empty_followup = _appointment_management_empty_followup(existing_context)
        if empty_followup is not None:
            empty_state_reply = self._handle_appointment_management_empty_followup(
                message=message,
                followup=empty_followup,
            )
            if empty_state_reply is not None:
                return self._finish_reply_with_context(
                    empty_state_reply,
                    stale_confirmation_cleanup=stale_confirmation_cleanup,
                    completed_flow_cleanup=completed_flow_cleanup,
                )
            # An actionable intent (e.g. "cancel", "book another") was detected:
            # clear the parked empty-state frame and let normal routing handle it.
            completed_flow_cleanup = _cleared_completed_flow_for_new_intent_updates()
            existing_context = {**existing_context, **completed_flow_cleanup}

        date_extraction = self._extract_requested_date(message)
        time_extraction = self._extract_time_preference(message)

        def finish(reply: ChatReceptionistReply) -> ChatReceptionistReply:
            return self._finish_reply_with_context(
                reply,
                stale_confirmation_cleanup=stale_confirmation_cleanup,
                completed_flow_cleanup=completed_flow_cleanup,
                date_extraction=date_extraction,
                time_extraction=time_extraction,
            )

        if _is_in_cancellation_flow(existing_context):
            return finish(
                self._handle_cancellation_flow(
                    message=message,
                    conversation=conversation,
                    chat_context=existing_context,
                ),
            )

        if _is_in_reschedule_flow(existing_context):
            return finish(
                self._handle_reschedule_flow(
                    message=message,
                    conversation=conversation,
                    chat_context=existing_context,
                ),
            )

        if _is_in_lookup_flow(existing_context):
            return finish(
                self._handle_lookup_flow(
                    message=message,
                    conversation=conversation,
                    chat_context=existing_context,
                ),
            )

        if self.responder._contains_any(normalized_message, _CANCEL_KEYWORDS):
            return finish(
                self._enter_cancellation_task_frame(
                    context_updates={},
                    message=message,
                    chat_context=existing_context,
                ),
            )

        if self.responder._contains_any(normalized_message, _RESCHEDULE_KEYWORDS):
            return finish(
                self._enter_reschedule_task_frame(
                    context_updates={},
                    message=message,
                    chat_context=existing_context,
                ),
            )

        if _is_appointment_lookup_request(normalized_message):
            return finish(
                self._enter_lookup_task_frame(
                    context_updates={},
                    message=message,
                    chat_context=existing_context,
                ),
            )

        intake_result = self._try_appointment_intake(
            message=message,
            existing_context=existing_context,
            normalized_message=normalized_message,
        )
        if intake_result is not None and intake_result.intent == "clarification":
            return finish(
                ChatReceptionistReply(
                    intent=ChatReceptionistIntent.APPOINTMENT_REQUEST,
                    content=intake_result.content or _APPOINTMENT_CLARIFICATION_FALLBACK_MESSAGE,
                ),
            )
        intake_updates = self._appointment_intake_context_updates(intake_result)

        def merge_intake_context_updates(
            context_updates: dict[str, Any],
        ) -> dict[str, Any]:
            if not intake_updates:
                return context_updates
            return {**context_updates, **intake_updates}

        if intake_updates.get("availability_range_label"):
            context_updates = merge_intake_context_updates(
                self._extract_context_updates(
                    normalized_message,
                    message,
                    existing_context=existing_context,
                    date_extraction=date_extraction,
                    time_extraction=time_extraction,
                ),
            )
            merged_context = {**existing_context, **context_updates}
            return finish(
                self._handle_availability_range_flow(
                    merged_context=merged_context,
                    context_updates=context_updates,
                ),
            )

        if (
            date_extraction.requires_clarification
            and self._is_clearly_asking_availability(
                normalized_message,
                date_parsing=date_extraction.date_parsing,
            )
            and not intake_updates.get("requested_date")
            and not self._intake_enables_earliest_search(intake_updates)
        ):
            context_updates = merge_intake_context_updates(
                self._extract_context_updates(
                    normalized_message,
                    message,
                    existing_context=existing_context,
                    date_extraction=date_extraction,
                    time_extraction=time_extraction,
                ),
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
            and not intake_updates.get("requested_time_window")
        ):
            context_updates = merge_intake_context_updates(
                self._extract_context_updates(
                    normalized_message,
                    message,
                    existing_context=existing_context,
                    date_extraction=date_extraction,
                    time_extraction=time_extraction,
                ),
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
                        "That date doesn't look quite right. What day should I check? "
                        "You can say tomorrow or next Monday."
                    ),
                ),
            )

        if (
            self.date_parser is not None
            and date_extraction.date_parsing is not None
            and date_extraction.date_parsing.get("status") == DateParseStatus.INVALID.value
            and not intake_updates.get("requested_date")
        ):
            return finish(
                ChatReceptionistReply(
                    intent=ChatReceptionistIntent.INVALID_DATE,
                    content=(
                        "That date doesn't look quite right. What day should I check? "
                        "You can say tomorrow or next Monday."
                    ),
                ),
            )

        context_updates = merge_intake_context_updates(
            self._extract_context_updates(
                normalized_message,
                message,
                existing_context=existing_context,
                date_extraction=date_extraction,
                time_extraction=time_extraction,
            ),
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
                    chat_context_updates={
                        "offered_doctors": self._serialize_offered_doctors(doctors),
                    },
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
                    content=self._format_specialty_doctors_reply(
                        doctors,
                        specialty_name=matched_specialty.name,
                    ),
                    matched_specialty_id=matched_specialty.id,
                    matched_specialty_name=matched_specialty.name,
                    chat_context_updates={
                        **context_updates,
                        "selected_specialty_id": str(matched_specialty.id),
                        "selected_specialty_name": matched_specialty.name,
                        "offered_doctors": self._serialize_offered_doctors(
                            doctors,
                            specialty=matched_specialty,
                        ),
                        "appointment_intake_awaiting": APPOINTMENT_INTAKE_AWAITING_DATE_OR_TIME,
                    },
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

        if (
            self._is_availability_request(normalized_message)
            or (
                self._should_enter_availability_flow(
                    normalized_message,
                    merged_context,
                )
            )
            or completing_availability
            or self._should_enter_availability_after_intake(
                context_updates=context_updates,
                merged_context=merged_context,
            )
        ):
            return finish(
                self._handle_availability_flow(
                    merged_context=merged_context,
                    context_updates=context_updates,
                    normalized_message=normalized_message,
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

        contextual_fallback = _resolve_contextual_fallback_reply(merged_context)
        if contextual_fallback is not None:
            contextual_intent, contextual_content = contextual_fallback
            return finish(
                ChatReceptionistReply(
                    intent=contextual_intent,
                    content=contextual_content,
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

        follow_up_updates = self._appointment_intake.extract_contextual_follow_up_updates(
            message=message,
            chat_context={**context, **context_updates},
        )
        if follow_up_updates:
            context_updates = {**context_updates, **follow_up_updates}

        return context_updates

    def _try_appointment_intake(
        self,
        *,
        message: str,
        existing_context: dict[str, Any],
        normalized_message: str,
    ) -> ChatAppointmentIntakeResult | None:
        if not self._should_run_appointment_intake(
            existing_context=existing_context,
            normalized_message=normalized_message,
            message=message,
        ):
            return None

        return self._appointment_intake.handle(
            message=message,
            chat_context=existing_context,
        )

    def _should_run_appointment_intake(
        self,
        *,
        existing_context: dict[str, Any],
        normalized_message: str,
        message: str,
    ) -> bool:
        if self._appointment_intake.chat_turn_understanding_interpreter is None:
            return False

        if existing_context.get("appointment_id"):
            return False

        if existing_context.get("booking_identity_step"):
            return False

        hold_id = existing_context.get("hold_id")
        if hold_id and self._booking_identity.is_active(existing_context):
            return False

        if self._is_hold_request(normalized_message, existing_context, message):
            return False

        return True

    def _should_override_stale_final_confirmation(
        self,
        *,
        existing_context: dict[str, Any],
        message: str,
        normalized_message: str,
    ) -> bool:
        if existing_context.get("appointment_id"):
            return False

        if (
            existing_context.get("booking_identity_step")
            != ChatBookingIdentityStep.AWAIT_FINAL_BOOKING_CONFIRMATION.value
        ):
            return False

        hold_id_raw = existing_context.get("hold_id")
        if not hold_id_raw:
            return False

        # A genuine confirmation is handled by the booking flow (which now refreshes
        # an expired hold), so only override for clearly new scheduling requests.
        if is_confirmation_confirmed(
            confirmation_type=ConfirmationType.FINAL_BOOKING_CONFIRMATION,
            message=message,
        ):
            return False

        if not self._looks_like_new_scheduling_request(normalized_message):
            return False

        # Only override when the hold is actually missing/expired; an active hold
        # means the pending confirmation is still valid.
        return not self._hold_is_present(hold_id_raw)

    def _looks_like_new_scheduling_request(self, normalized_message: str) -> bool:
        if not normalized_message:
            return False

        if any(
            phrase in normalized_message
            for phrase in ("start over", "start again", "restart")
        ):
            return True

        if self.responder._contains_any(normalized_message, _APPOINTMENT_KEYWORDS):
            return True

        if self._is_availability_request(normalized_message):
            return True

        if self.responder._contains_any(normalized_message, _SPECIALTY_LIST_KEYWORDS):
            return True

        if self.responder._contains_any(normalized_message, _DOCTOR_LIST_KEYWORDS):
            return True

        if self._match_specialty_in_message(normalized_message) is not None:
            return True

        if self._mentions_unknown_specialty(normalized_message):
            return True

        if self._match_doctor_in_message(normalized_message) is not None:
            return True

        return self._mentions_unknown_doctor(normalized_message)

    def _hold_is_present(self, hold_id_raw: Any) -> bool:
        try:
            hold_id = UUID(str(hold_id_raw))
        except (ValueError, TypeError):
            return False

        try:
            return self.appointment_holds.get_hold_by_id(hold_id) is not None
        except (RedisError, AppointmentHoldServiceError):
            # If the hold store cannot confirm the hold, treat it as gone so the
            # user is not trapped repeating the confirmation prompt.
            return False

    def _appointment_intake_context_updates(
        self,
        intake_result: ChatAppointmentIntakeResult | None,
    ) -> dict[str, Any]:
        if intake_result is None or intake_result.intent != "appointment_intake":
            return {}

        context_updates = dict(intake_result.chat_context_updates)
        search_criteria = intake_result.search_criteria
        if search_criteria is None:
            return context_updates

        if search_criteria.soonest_requested:
            context_updates["soonest_requested"] = True
        if search_criteria.search_start_date is not None:
            context_updates["search_start_date"] = search_criteria.search_start_date
        if search_criteria.search_end_date is not None:
            context_updates["search_end_date"] = search_criteria.search_end_date

        return context_updates

    def _format_availability_missing_date_prompt(
        self,
        target_name: str,
        *,
        doctor_just_selected: bool = False,
    ) -> str:
        if doctor_just_selected and target_name:
            return f"Great. What day works best for {target_name}?"
        if target_name:
            return f"What day works best for {target_name}?"
        return "What day works best for your appointment?"

    def parse_patient_identity(
        self,
        message: str,
        *,
        booking_context: bool = False,
    ) -> ChatPatientIdentity:
        email_match = _EMAIL_PATTERN.search(message)
        email = (
            normalize_email_address(email_match.group(0))
            if email_match is not None
            else None
        )

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
        if match is not None:
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

        natural = _parse_natural_date_of_birth(message)
        if natural is None:
            return None

        has_dob_cue = bool(
            re.search(r"\b(dob|date of birth|born)\b", message, re.IGNORECASE)
            or "," in message
            or _MY_NAME_IS_PATTERN.search(message)
            or booking_context
        )
        if not has_dob_cue:
            return None

        return natural

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
                remainder = re.sub(r"\s+and\s+my$", "", remainder, flags=re.IGNORECASE).strip()

            normalized = normalize_patient_display_name(remainder) if remainder else None
            return normalized or None

        segments = [segment.strip() for segment in message.split(",")]
        if len(segments) < 2:
            if (
                booking_context
                and segments
                and len(segments[0].split()) >= 2
                and not self._looks_like_scheduling_text(segments[0])
            ):
                return normalize_patient_display_name(segments[0])
            return None

        first_segment = segments[0]
        if first_segment.lower().startswith("my name is "):
            return None

        if len(first_segment.split()) >= 2:
            return normalize_patient_display_name(first_segment)

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

    def _parse_booking_patient_fields(
        self,
        message: str,
        *,
        booking_context: bool = False,
    ) -> ParsedPatientFields:
        parsed = self.parse_patient_identity(message, booking_context=booking_context)
        return ParsedPatientFields(
            full_name=parsed.full_name,
            date_of_birth=parsed.date_of_birth,
            email=parsed.email,
            phone=parsed.phone,
        )

    def _booking_flow_result_to_reply(
        self,
        flow: BookingIdentityFlowResult,
    ) -> ChatReceptionistReply:
        return ChatReceptionistReply(
            intent=ChatReceptionistIntent(flow.intent),
            content=flow.content,
            chat_context_updates=flow.chat_context_updates,
            hold_id=flow.hold_id,
            booking_attempted=flow.booking_attempted,
        )

    def _handle_cancellation_flow(
        self,
        *,
        message: str,
        conversation: Conversation,
        chat_context: dict[str, Any],
    ) -> ChatReceptionistReply:
        awaiting = chat_context.get("appointment_management_awaiting")
        if awaiting == APPOINTMENT_MANAGEMENT_AWAITING_PATIENT_IDENTITY:
            flow = self._appointment_cancellation.handle_patient_identity_intake(
                message=message,
                conversation=conversation,
                chat_context=chat_context,
                parse_patient_fields=self._parse_booking_patient_fields,
            )
            return self._cancellation_flow_result_to_reply(flow)

        if awaiting == APPOINTMENT_MANAGEMENT_AWAITING_APPOINTMENT_SELECTION:
            flow = self._appointment_cancellation.handle_appointment_selection(
                message=message,
                chat_context=chat_context,
            )
            return self._cancellation_flow_result_to_reply(flow)

        if awaiting == APPOINTMENT_MANAGEMENT_AWAITING_CANCELLATION_CONFIRMATION:
            flow = self._appointment_cancellation.handle_cancellation_confirmation(
                message=message,
                conversation_id=conversation.id,
                chat_context=chat_context,
            )
            return self._cancellation_flow_result_to_reply(flow)

        return ChatReceptionistReply(
            intent=ChatReceptionistIntent.CANCEL_REQUEST,
            content=_CANCELLATION_IDENTITY_REPROMPT_MESSAGE,
            chat_context_updates={
                "appointment_management_mode": APPOINTMENT_MANAGEMENT_MODE_CANCEL,
                "appointment_management_awaiting": (
                    APPOINTMENT_MANAGEMENT_AWAITING_PATIENT_IDENTITY
                ),
            },
        )

    def _cancellation_flow_result_to_reply(
        self,
        flow: CancellationFlowResult,
    ) -> ChatReceptionistReply:
        return ChatReceptionistReply(
            intent=ChatReceptionistIntent(flow.intent),
            content=flow.content,
            chat_context_updates=flow.chat_context_updates,
        )

    def _enter_cancellation_task_frame(
        self,
        *,
        context_updates: dict[str, Any],
        message: str = "",
        chat_context: dict[str, Any] | None = None,
    ) -> ChatReceptionistReply:
        if chat_context is not None and not self._wants_different_patient(
            message=message,
            chat_context=chat_context,
        ):
            reuse = self._appointment_cancellation.list_appointments_for_resolved_patient(
                chat_context=chat_context,
            )
            if reuse is not None:
                if context_updates:
                    reuse = replace(
                        reuse,
                        chat_context_updates={
                            **context_updates,
                            **reuse.chat_context_updates,
                        },
                    )
                return self._cancellation_flow_result_to_reply(reuse)

        return ChatReceptionistReply(
            intent=ChatReceptionistIntent.CANCEL_REQUEST,
            content=_CANCELLATION_IDENTITY_ENTRY_MESSAGE,
            chat_context_updates={
                **context_updates,
                "appointment_management_mode": APPOINTMENT_MANAGEMENT_MODE_CANCEL,
                "appointment_management_awaiting": (
                    APPOINTMENT_MANAGEMENT_AWAITING_PATIENT_IDENTITY
                ),
                # Start the intake with a clean partial-identity buffer.
                APPOINTMENT_MANAGEMENT_IDENTITY_KEY: None,
            },
        )

    def _handle_lookup_flow(
        self,
        *,
        message: str,
        conversation: Conversation,
        chat_context: dict[str, Any],
    ) -> ChatReceptionistReply:
        awaiting = chat_context.get("appointment_management_awaiting")
        if awaiting == LOOKUP_AWAITING_PATIENT_IDENTITY:
            flow = self._appointment_lookup.handle_patient_identity_intake(
                message=message,
                conversation=conversation,
                chat_context=chat_context,
                parse_patient_fields=self._parse_booking_patient_fields,
            )
            return self._lookup_flow_result_to_reply(flow)

        return self._enter_lookup_task_frame(
            context_updates={},
            message=message,
            chat_context=chat_context,
        )

    def _lookup_flow_result_to_reply(
        self,
        flow: LookupFlowResult,
    ) -> ChatReceptionistReply:
        return ChatReceptionistReply(
            intent=ChatReceptionistIntent(flow.intent),
            content=flow.content,
            chat_context_updates=flow.chat_context_updates,
        )

    def _enter_lookup_task_frame(
        self,
        *,
        context_updates: dict[str, Any],
        message: str = "",
        chat_context: dict[str, Any] | None = None,
    ) -> ChatReceptionistReply:
        if chat_context is not None and not self._wants_different_patient(
            message=message,
            chat_context=chat_context,
        ):
            reuse = self._appointment_lookup.list_appointments_for_resolved_patient(
                chat_context=chat_context,
            )
            if reuse is not None:
                if context_updates:
                    reuse = replace(
                        reuse,
                        chat_context_updates={
                            **context_updates,
                            **reuse.chat_context_updates,
                        },
                    )
                return self._lookup_flow_result_to_reply(reuse)

        return ChatReceptionistReply(
            intent=ChatReceptionistIntent.LIST_APPOINTMENTS,
            content=_LOOKUP_IDENTITY_ENTRY_MESSAGE,
            chat_context_updates={
                **context_updates,
                "appointment_management_mode": APPOINTMENT_MANAGEMENT_MODE_LOOKUP,
                "appointment_management_awaiting": LOOKUP_AWAITING_PATIENT_IDENTITY,
                APPOINTMENT_MANAGEMENT_IDENTITY_KEY: None,
            },
        )

    def _handle_reschedule_flow(
        self,
        *,
        message: str,
        conversation: Conversation,
        chat_context: dict[str, Any],
    ) -> ChatReceptionistReply:
        awaiting = chat_context.get("appointment_management_awaiting")
        if awaiting == APPOINTMENT_MANAGEMENT_AWAITING_PATIENT_IDENTITY:
            flow = self._appointment_rescheduling.handle_patient_identity_intake(
                message=message,
                conversation=conversation,
                chat_context=chat_context,
                parse_patient_fields=self._parse_booking_patient_fields,
            )
            return self._reschedule_flow_result_to_reply(flow)

        if awaiting == APPOINTMENT_MANAGEMENT_AWAITING_APPOINTMENT_SELECTION:
            flow = self._appointment_rescheduling.handle_appointment_selection(
                message=message,
                chat_context=chat_context,
            )
            return self._reschedule_flow_result_to_reply(flow)

        if awaiting == APPOINTMENT_MANAGEMENT_AWAITING_NEW_TIME_PREFERENCE:
            flow = self._appointment_rescheduling.handle_new_time_preference(
                message=message,
                chat_context=chat_context,
            )
            return self._reschedule_flow_result_to_reply(flow)

        if awaiting == APPOINTMENT_MANAGEMENT_AWAITING_NEW_SLOT_SELECTION:
            flow = self._appointment_rescheduling.handle_new_slot_selection(
                message=message,
                conversation=conversation,
                chat_context=chat_context,
            )
            return self._reschedule_flow_result_to_reply(flow)

        if awaiting == APPOINTMENT_MANAGEMENT_AWAITING_RESCHEDULE_CONFIRMATION:
            flow = self._appointment_rescheduling.handle_reschedule_confirmation(
                message=message,
                conversation=conversation,
                chat_context=chat_context,
            )
            return self._reschedule_flow_result_to_reply(flow)

        return self._enter_reschedule_task_frame(
            context_updates={},
            message=message,
            chat_context=chat_context,
        )

    def _reschedule_flow_result_to_reply(
        self,
        flow: RescheduleFlowResult,
    ) -> ChatReceptionistReply:
        return ChatReceptionistReply(
            intent=ChatReceptionistIntent(flow.intent),
            content=flow.content,
            chat_context_updates=flow.chat_context_updates,
        )

    def _enter_reschedule_task_frame(
        self,
        *,
        context_updates: dict[str, Any],
        message: str = "",
        chat_context: dict[str, Any] | None = None,
    ) -> ChatReceptionistReply:
        if chat_context is not None and not self._wants_different_patient(
            message=message,
            chat_context=chat_context,
        ):
            reuse = self._appointment_rescheduling.list_appointments_for_resolved_patient(
                chat_context=chat_context,
            )
            if reuse is not None:
                if context_updates:
                    reuse = replace(
                        reuse,
                        chat_context_updates={
                            **context_updates,
                            **reuse.chat_context_updates,
                        },
                    )
                return self._reschedule_flow_result_to_reply(reuse)

        return ChatReceptionistReply(
            intent=ChatReceptionistIntent.RESCHEDULE_REQUEST,
            content=RESCHEDULE_IDENTITY_ENTRY_MESSAGE,
            chat_context_updates={
                **context_updates,
                "appointment_management_mode": APPOINTMENT_MANAGEMENT_MODE_RESCHEDULE,
                "appointment_management_awaiting": (
                    APPOINTMENT_MANAGEMENT_AWAITING_PATIENT_IDENTITY
                ),
                # Start the intake with a clean partial-identity buffer.
                APPOINTMENT_MANAGEMENT_IDENTITY_KEY: None,
            },
        )

    def _wants_different_patient(
        self,
        *,
        message: str,
        chat_context: dict[str, Any],
    ) -> bool:
        """Decide whether the user is asking about a different patient.

        We only override a remembered patient through normal identity resolution,
        so when the user clearly signals a different person (an explicit phrase, a
        new date of birth, or a different full name) we skip reuse and fall back
        to the standard name + date-of-birth intake.
        """
        resolved = read_resolved_patient_context(chat_context)
        if resolved is None:
            return False

        normalized = message.lower()
        if any(phrase in normalized for phrase in _DIFFERENT_PATIENT_PHRASES):
            return True

        parsed = self.parse_patient_identity(message, booking_context=False)
        if parsed.date_of_birth:
            return True
        if parsed.full_name and resolved.name:
            if (
                normalize_patient_display_name(parsed.full_name).lower()
                != resolved.name.lower()
            ):
                return True
        return False

    def _hold_booking_identity_unavailable_reply(
        self,
        *,
        hold_id: str,
        context_updates: dict[str, Any],
        booking_attempted: bool = False,
    ) -> ChatReceptionistReply:
        return ChatReceptionistReply(
            intent=ChatReceptionistIntent.BOOKING_IDENTITY_MISSING,
            content=(
                "I need to confirm patient details through our secure booking flow "
                "before I can finish this appointment. Please check availability and "
                "choose a time again."
            ),
            chat_context_updates=context_updates,
            hold_id=hold_id,
            booking_attempted=booking_attempted,
        )

    def _resolve_post_cancellation_decision(
        self,
        *,
        message: str,
        chat_context: dict[str, Any],
    ) -> PostCancellationTurnDecision:
        return classify_post_cancellation_turn(
            message=message,
            chat_context=chat_context,
        ).decision

    def _post_cancellation_reply_for_decision(
        self,
        *,
        decision: PostCancellationTurnDecision,
        context_updates: dict[str, Any],
    ) -> ChatReceptionistReply | None:
        if decision in {
            PostCancellationTurnDecision.NEW_SCHEDULING_REQUEST,
            PostCancellationTurnDecision.CANCEL_REQUEST,
            PostCancellationTurnDecision.RESCHEDULE_REQUEST,
            PostCancellationTurnDecision.APPOINTMENT_LOOKUP_REQUEST,
        }:
            return None

        content_by_decision = {
            PostCancellationTurnDecision.END_CONVERSATION: _POST_BOOKING_CLOSING_MESSAGE,
            PostCancellationTurnDecision.NEEDS_MORE_HELP: _POST_BOOKING_NEEDS_MORE_HELP_MESSAGE,
            PostCancellationTurnDecision.UNKNOWN: _POST_CANCELLATION_UNKNOWN_MESSAGE,
        }
        content = content_by_decision.get(decision)
        if content is None:
            return None

        return ChatReceptionistReply(
            intent=ChatReceptionistIntent.CANCEL_REQUEST,
            content=content,
            chat_context_updates=context_updates,
        )

    def _handle_post_cancellation_message(
        self,
        *,
        message: str,
        merged_context: dict[str, Any],
    ) -> ChatReceptionistReply | None:
        decision = self._resolve_post_cancellation_decision(
            message=message,
            chat_context=merged_context,
        )
        return self._post_cancellation_reply_for_decision(
            decision=decision,
            context_updates={},
        )

    def _resolve_post_reschedule_decision(
        self,
        *,
        message: str,
        chat_context: dict[str, Any],
    ) -> PostRescheduleTurnDecision:
        return classify_post_reschedule_turn(
            message=message,
            chat_context=chat_context,
        ).decision

    def _post_reschedule_reply_for_decision(
        self,
        *,
        decision: PostRescheduleTurnDecision,
        context_updates: dict[str, Any],
    ) -> ChatReceptionistReply | None:
        if decision in {
            PostRescheduleTurnDecision.NEW_SCHEDULING_REQUEST,
            PostRescheduleTurnDecision.CANCEL_REQUEST,
            PostRescheduleTurnDecision.RESCHEDULE_REQUEST,
            PostRescheduleTurnDecision.APPOINTMENT_LOOKUP_REQUEST,
        }:
            return None

        content_by_decision = {
            PostRescheduleTurnDecision.END_CONVERSATION: _POST_BOOKING_CLOSING_MESSAGE,
            PostRescheduleTurnDecision.NEEDS_MORE_HELP: _POST_BOOKING_NEEDS_MORE_HELP_MESSAGE,
            PostRescheduleTurnDecision.UNKNOWN: _POST_RESCHEDULE_UNKNOWN_MESSAGE,
        }
        content = content_by_decision.get(decision)
        if content is None:
            return None

        return ChatReceptionistReply(
            intent=ChatReceptionistIntent.RESCHEDULE_REQUEST,
            content=content,
            chat_context_updates=context_updates,
        )

    def _handle_post_reschedule_message(
        self,
        *,
        message: str,
        merged_context: dict[str, Any],
    ) -> ChatReceptionistReply | None:
        decision = self._resolve_post_reschedule_decision(
            message=message,
            chat_context=merged_context,
        )
        return self._post_reschedule_reply_for_decision(
            decision=decision,
            context_updates={},
        )

    def _handle_appointment_management_empty_followup(
        self,
        *,
        message: str,
        followup: str,
    ) -> ChatReceptionistReply | None:
        """Honor the follow-up after a "resolved patient, no appointments" state.

        Returns ``None`` when the user expressed a fresh actionable intent (book,
        cancel, reschedule, or list), so the caller clears the parked frame and
        lets normal routing handle it. Otherwise returns the appropriate reply
        for an affirmative, a farewell, or an unclear turn.
        """
        decision = classify_post_completion_turn(message=message).decision
        if _post_completion_decision_is_actionable(decision):
            return None

        offers_booking = followup == APPOINTMENT_MANAGEMENT_EMPTY_OFFER_BOOKING
        intent = (
            ChatReceptionistIntent.APPOINTMENT_REQUEST
            if offers_booking
            else ChatReceptionistIntent.CANCEL_REQUEST
        )

        if decision is PostCompletionTurnDecision.END_CONVERSATION:
            return ChatReceptionistReply(
                intent=intent,
                content=_POST_BOOKING_CLOSING_MESSAGE,
                chat_context_updates=_cleared_post_conversation_terminal_updates(),
            )

        new_intent_cleanup = _cleared_completed_flow_for_new_intent_updates()

        if decision is PostCompletionTurnDecision.NEEDS_MORE_HELP:
            if offers_booking:
                # Affirmative to "would you like to schedule/book?": start the
                # existing appointment intake by asking what they want to book.
                return ChatReceptionistReply(
                    intent=ChatReceptionistIntent.APPOINTMENT_REQUEST,
                    content=self._format_missing_scheduling_target_prompt(),
                    chat_context_updates=new_intent_cleanup,
                )
            return ChatReceptionistReply(
                intent=ChatReceptionistIntent.CANCEL_REQUEST,
                content=_POST_BOOKING_NEEDS_MORE_HELP_MESSAGE,
                chat_context_updates=new_intent_cleanup,
            )

        # Unclear turn: clear the parked frame and offer a gentle next step.
        return ChatReceptionistReply(
            intent=intent,
            content=_GENERIC_SCHEDULING_FALLBACK_MESSAGE,
            chat_context_updates=new_intent_cleanup,
        )

    def _resolve_post_booking_decision(
        self,
        *,
        message: str,
        chat_context: dict[str, Any],
    ) -> PostBookingTurnUnderstanding:
        return self._post_booking_turn_classifier.classify(
            message=message,
            chat_context=chat_context,
        )

    def _post_booking_reply_for_decision(
        self,
        *,
        decision: PostBookingTurnDecision,
        appointment_id: Any,
        hold_id: Any,
        context_updates: dict[str, Any],
    ) -> ChatReceptionistReply | None:
        if decision in {
            PostBookingTurnDecision.NEW_SCHEDULING_REQUEST,
            PostBookingTurnDecision.CANCEL_REQUEST,
            PostBookingTurnDecision.RESCHEDULE_REQUEST,
            PostBookingTurnDecision.APPOINTMENT_LOOKUP_REQUEST,
        }:
            return None

        content_by_decision = {
            PostBookingTurnDecision.END_CONVERSATION: _POST_BOOKING_CLOSING_MESSAGE,
            PostBookingTurnDecision.NEEDS_MORE_HELP: _POST_BOOKING_NEEDS_MORE_HELP_MESSAGE,
            PostBookingTurnDecision.UNKNOWN: _POST_BOOKING_UNKNOWN_MESSAGE,
        }
        content = content_by_decision.get(decision)
        if content is None:
            return None

        return ChatReceptionistReply(
            intent=ChatReceptionistIntent.BOOKING_CONFIRMED,
            content=content,
            chat_context_updates=context_updates,
            hold_id=str(hold_id) if hold_id else None,
            booking_confirmed=True,
            appointment_id=str(appointment_id),
        )

    def _handle_post_booking_message(
        self,
        *,
        message: str,
        merged_context: dict[str, Any],
        context_updates: dict[str, Any],
    ) -> ChatReceptionistReply | None:
        """Lifecycle guard for messages within the post-booking follow-up frame."""
        understanding = self._resolve_post_booking_decision(
            message=message,
            chat_context=merged_context,
        )
        return self._post_booking_reply_for_decision(
            decision=understanding.decision,
            appointment_id=merged_context["appointment_id"],
            hold_id=merged_context.get("hold_id"),
            context_updates=context_updates,
        )

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
        has_confirmation = is_confirmation_confirmed(
            confirmation_type=ConfirmationType.FINAL_BOOKING_CONFIRMATION,
            message=message,
        )
        offered_slots = merged_context.get("offered_slots") or []

        if merged_context.get("appointment_id"):
            return self._handle_post_booking_message(
                message=message,
                merged_context=merged_context,
                context_updates=context_updates,
            )

        if hold_id:
            hold_id_str = str(hold_id)
            if not self._booking_identity.is_active(merged_context):
                parsed_hold_identity = self.parse_patient_identity(
                    message,
                    booking_context=booking_context,
                )
                has_identity_fields = any(
                    (
                        parsed_hold_identity.full_name,
                        parsed_hold_identity.date_of_birth,
                        parsed_hold_identity.phone,
                        parsed_hold_identity.email,
                    )
                )
                if has_confirmation or has_identity_fields:
                    return self._hold_booking_identity_unavailable_reply(
                        hold_id=hold_id_str,
                        context_updates=context_updates,
                        booking_attempted=has_confirmation,
                    )
                return None

            flow_result = self._booking_identity.handle(
                message=message,
                conversation=conversation,
                merged_context=merged_context,
                context_updates=context_updates,
                parse_patient_fields=self._parse_booking_patient_fields,
                format_missing_identity_fields=self._format_missing_identity_fields,
                hold_id=hold_id_str,
            )
            if flow_result is not None:
                flow_reply = self._booking_flow_result_to_reply(flow_result)
                if flow_reply.intent == ChatReceptionistIntent.BOOKING_CONFIRMED:
                    return replace(
                        flow_reply,
                        booking_confirmed=True,
                        appointment_id=(
                            str(merged_context["appointment_id"])
                            if merged_context.get("appointment_id")
                            else flow_reply.appointment_id
                        ),
                    )
                return flow_reply

            flow_context = {**merged_context, **context_updates}
            if self._booking_identity.should_attempt_booking(
                merged_context=flow_context,
                message=message,
            ):
                identity_raw = flow_context.get("patient_identity")
                merged_identity = identity_raw if isinstance(identity_raw, dict) else {}
                identity_updates = {
                    **context_updates,
                    "patient_identity": merged_identity,
                }
                return self._attempt_booking(
                    conversation=conversation,
                    merged_context=flow_context,
                    merged_identity=merged_identity,
                    identity_updates=identity_updates,
                    request_patient_id=request_patient_id,
                )

            return None

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
            merged_context=merged_context,
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
                    "Please choose an available time and hold it first before confirming a booking."
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
                    content=(f"I've noted your details. I still need your {missing_text}."),
                    chat_context_updates=identity_updates,
                )

            if has_identity_fields and identity.is_complete():
                return ChatReceptionistReply(
                    intent=ChatReceptionistIntent.PATIENT_IDENTITY_COMPLETE,
                    content=(
                        "I have your contact details noted. "
                        "Please choose an available time to continue booking."
                    ),
                    chat_context_updates=identity_updates,
                )

            return None

        return None

    def _should_enter_booking_flow(
        self,
        *,
        hold_id: Any,
        has_identity_fields: bool,
        has_confirmation: bool,
        offered_slots: list[Any],
        merged_context: dict[str, Any] | None = None,
    ) -> bool:
        if (
            hold_id
            and merged_context is not None
            and self._booking_identity.is_active(merged_context)
        ):
            return True

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
        existing_appointment_id = merged_context.get("appointment_id")

        if existing_appointment_id:
            return ChatReceptionistReply(
                intent=ChatReceptionistIntent.BOOKING_CONFIRMED,
                content=_ALREADY_CONFIRMED_MESSAGE,
                chat_context_updates={
                    **identity_updates,
                    **self._booking_identity.booking_completed_context_updates(),
                },
                hold_id=hold_id,
                appointment_id=str(existing_appointment_id),
                booking_confirmed=True,
            )

        if not hold_id or not slot_id_raw:
            return ChatReceptionistReply(
                intent=ChatReceptionistIntent.BOOKING_HOLD_MISSING,
                content=(
                    "Please choose an available time and hold it first before confirming a booking."
                ),
                chat_context_updates=identity_updates,
                hold_id=hold_id,
                booking_attempted=True,
            )

        if not merged_context.get("patient_resolution_id"):
            return ChatReceptionistReply(
                intent=ChatReceptionistIntent.BOOKING_IDENTITY_MISSING,
                content=(
                    "I still need to verify patient details before booking."
                    f"{_SELECTED_TIME_STILL_AVAILABLE_SUFFIX}"
                ),
                chat_context_updates=identity_updates,
                hold_id=hold_id,
                booking_attempted=True,
            )

        if not merged_context.get("confirmed_booking_email"):
            return ChatReceptionistReply(
                intent=ChatReceptionistIntent.BOOKING_IDENTITY_MISSING,
                content=(
                    "I still need a confirmed email before booking."
                    f"{_SELECTED_TIME_STILL_AVAILABLE_SUFFIX}"
                ),
                chat_context_updates=identity_updates,
                hold_id=hold_id,
                booking_attempted=True,
            )

        owner_id = str(merged_context.get("hold_owner_id") or conversation.id)

        patient = self._resolve_patient_for_booking(
            merged_identity,
            conversation=conversation,
            conversation_patient_id=conversation.patient_id,
            request_patient_id=request_patient_id,
            merged_context=merged_context,
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
            booking_result = self._book_appointment_with_hold(
                hold_id=UUID(hold_id),
                availability_slot_id=UUID(str(slot_id_raw)),
                patient_id=patient.id,
                owner_id=owner_id,
            )
        except (
            AppointmentHoldNotFoundError,
            AppointmentHoldMismatchError,
            AppointmentHoldOwnershipError,
        ):
            # Do not book without a valid hold. Try to recover by refreshing the
            # hold for the still-selected slot, otherwise clear stale state.
            return self._recover_missing_hold(
                conversation=conversation,
                merged_context=merged_context,
                identity_updates=identity_updates,
                slot_id_raw=slot_id_raw,
                patient=patient,
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

        return self._booking_success_reply(
            booking_result=booking_result,
            merged_context=merged_context,
            identity_updates=identity_updates,
            owner_id=owner_id,
            hold_id=hold_id,
            patient=patient,
        )

    def _book_appointment_with_hold(
        self,
        *,
        hold_id: UUID,
        availability_slot_id: UUID,
        patient_id: UUID,
        owner_id: str,
    ) -> AppointmentBookingResult:
        return self.appointment_booking.book_appointment(
            AppointmentBookingRequest(
                hold_id=hold_id,
                availability_slot_id=availability_slot_id,
                patient_id=patient_id,
                owner_id=owner_id,
            ),
        )

    def _booking_success_reply(
        self,
        *,
        booking_result: AppointmentBookingResult,
        merged_context: dict[str, Any],
        identity_updates: dict[str, Any],
        owner_id: str,
        hold_id: str,
        patient: Patient,
    ) -> ChatReceptionistReply:
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
                f"{_BOOKING_SUCCESS_FOLLOW_UP_SUFFIX}"
            ),
            chat_context_updates={
                **identity_updates,
                "appointment_id": str(appointment.id),
                "booking_confirmed_at": booking_confirmed_at,
                # Remember the resolved patient so a later cancellation/reschedule
                # in this conversation can reuse it without re-asking name + DOB.
                **build_resolved_patient_context_updates(
                    patient_id=str(patient.id),
                    name=patient.full_name,
                    date_of_birth=patient.date_of_birth.isoformat(),
                    email=patient.email,
                    patient_resolution_id=(
                        merged_context.get("patient_resolution_id")
                        if isinstance(merged_context.get("patient_resolution_id"), str)
                        else None
                    ),
                ),
                **self._booking_identity.booking_completed_context_updates(),
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

    def _recover_missing_hold(
        self,
        *,
        conversation: Conversation,
        merged_context: dict[str, Any],
        identity_updates: dict[str, Any],
        slot_id_raw: Any,
        patient: Patient,
    ) -> ChatReceptionistReply:
        """Recover from a missing/expired hold at final booking confirmation.

        The hold is the concurrency boundary, so we never book without one. If the
        previously selected slot is still available, we create a fresh hold (owned
        by this conversation) and retry the booking. If the slot is gone or the
        retry still fails, we clear stale hold/selection state and ask the user to
        choose another time.
        """
        if not slot_id_raw:
            return self._unrecoverable_hold_reply(identity_updates=identity_updates)

        owner_id = str(conversation.id)

        try:
            slot = self.scheduling.get_available_slot_for_hold(UUID(str(slot_id_raw)))
            refreshed_hold = self.appointment_holds.create_hold(
                availability_slot_id=slot.id,
                doctor_id=slot.doctor_id,
                start_time=slot.start_time,
                end_time=slot.end_time,
                owner_id=owner_id,
                ttl_seconds=self._chat_appointment_hold_ttl_seconds,
            )
        except (
            AvailabilitySlotNotFoundError,
            AvailabilitySlotUnavailableError,
            AppointmentSlotAlreadyHeldError,
            AppointmentHoldStoreUnavailableError,
        ):
            return self._unrecoverable_hold_reply(identity_updates=identity_updates)

        hold_expires_at = refreshed_hold.created_at + timedelta(
            seconds=self._chat_appointment_hold_ttl_seconds,
        )
        refreshed_hold_context = {
            "selected_availability_slot_id": str(slot.id),
            "selected_start_time": slot.start_time.isoformat(),
            "selected_doctor_id": str(slot.doctor_id),
            "hold_id": str(refreshed_hold.hold_id),
            "hold_expires_at": hold_expires_at.isoformat(),
            "hold_owner_id": owner_id,
        }

        try:
            booking_result = self._book_appointment_with_hold(
                hold_id=refreshed_hold.hold_id,
                availability_slot_id=slot.id,
                patient_id=patient.id,
                owner_id=owner_id,
            )
        except (
            AppointmentHoldNotFoundError,
            AppointmentHoldMismatchError,
            AppointmentHoldOwnershipError,
            AppointmentSlotAlreadyBookedError,
            BookingAvailabilitySlotUnavailableError,
            BookingAvailabilitySlotNotFoundError,
            BookingDoctorNotFoundError,
            BookingPatientNotFoundError,
            AppointmentSlotAlreadyHeldError,
        ):
            self._release_hold_safely(hold=refreshed_hold, owner_id=owner_id)
            return self._unrecoverable_hold_reply(identity_updates=identity_updates)

        return self._booking_success_reply(
            booking_result=booking_result,
            merged_context={**merged_context, **refreshed_hold_context},
            identity_updates={**identity_updates, **refreshed_hold_context},
            owner_id=owner_id,
            hold_id=str(refreshed_hold.hold_id),
            patient=patient,
        )

    def _unrecoverable_hold_reply(
        self,
        *,
        identity_updates: dict[str, Any],
    ) -> ChatReceptionistReply:
        return ChatReceptionistReply(
            intent=ChatReceptionistIntent.BOOKING_HOLD_EXPIRED,
            content=(
                "That time is no longer available, so I could not complete the booking. "
                "Please choose another available time and I'll hold it for you."
            ),
            chat_context_updates={
                **identity_updates,
                **self._cleared_hold_context_updates(),
            },
            booking_attempted=True,
        )

    def _release_hold_safely(self, *, hold: AppointmentHold, owner_id: str) -> None:
        try:
            self.appointment_holds.release_hold(
                doctor_id=hold.doctor_id,
                start_time=hold.start_time,
                owner_id=owner_id,
            )
        except (AppointmentHoldServiceError, RedisError):
            logger.debug("failed to release refreshed hold after booking recovery failure")

    def _cleared_hold_context_updates(self) -> dict[str, Any]:
        """Transient keys to clear when a hold becomes unrecoverable.

        Stable user/session context (selected doctor/specialty, patient identity)
        is intentionally preserved so the user can retry quickly.
        """
        return {
            "hold_id": None,
            "hold_expires_at": None,
            "hold_owner_id": None,
            "selected_availability_slot_id": None,
            "selected_start_time": None,
            "offered_slots": [],
            "booking_identity_step": None,
            "pending_confirmation_email": None,
            "appointment_intake_awaiting": APPOINTMENT_INTAKE_AWAITING_DATE_OR_TIME,
        }

    @staticmethod
    def _finish_reply_with_context(
        reply: ChatReceptionistReply | None,
        *,
        stale_confirmation_cleanup: dict[str, Any],
        completed_flow_cleanup: dict[str, Any],
        date_extraction: _RequestedDateExtraction | None = None,
        time_extraction: _TimePreferenceExtraction | None = None,
    ) -> ChatReceptionistReply:
        if reply is None:
            msg = "expected a receptionist reply"
            raise ValueError(msg)

        context_updates = dict(reply.chat_context_updates)
        if stale_confirmation_cleanup:
            context_updates = {**stale_confirmation_cleanup, **context_updates}
        if completed_flow_cleanup:
            context_updates = {**completed_flow_cleanup, **context_updates}
        if context_updates != reply.chat_context_updates:
            reply = replace(reply, chat_context_updates=context_updates)

        if date_extraction is not None and date_extraction.date_parsing is not None:
            reply = replace(reply, date_parsing=date_extraction.date_parsing)
        if time_extraction is not None and time_extraction.time_preference_parsing is not None:
            reply = replace(
                reply,
                time_preference_parsing=time_extraction.time_preference_parsing,
            )
        return reply

    def _resolve_patient_for_booking(
        self,
        merged_identity: dict[str, Any],
        *,
        conversation: Conversation,
        conversation_patient_id: UUID | None,
        request_patient_id: UUID | None = None,
        merged_context: dict[str, Any] | None = None,
    ) -> Patient | None:
        del merged_identity, conversation_patient_id, request_patient_id
        if merged_context is None:
            return None

        resolution_id = merged_context.get("patient_resolution_id")
        if not isinstance(resolution_id, str):
            return None

        record = self.patient_identity_resolution.get_resolution_for_booking(
            patient_resolution_id=resolution_id,
            conversation_id=conversation.id,
        )
        if record is None:
            return None

        return self.scheduling.patients.get_by_id(record.patient_id)

    def _format_booking_display_time(self, merged_context: dict[str, Any]) -> str:
        start_time_raw = merged_context.get("selected_start_time")

        if start_time_raw is None:
            return "the selected time"

        try:
            parsed = datetime.fromisoformat(str(start_time_raw))
        except ValueError:
            return "the selected time"

        return format_clinic_local_time_label(parsed, self._clinic_timezone())

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
        normalized_message: str = "",
    ) -> ChatReceptionistReply:
        selected_doctor_id = merged_context.get("selected_doctor_id")
        selected_specialty_id = merged_context.get("selected_specialty_id")
        requested_date = merged_context.get("requested_date")

        if (
            not merged_context.get("selected_doctor_id")
            and self._mentions_unknown_doctor(normalized_message)
        ):
            return ChatReceptionistReply(
                intent=ChatReceptionistIntent.AVAILABILITY_UNKNOWN_DOCTOR,
                content=self._format_unknown_doctor_prompt(),
                chat_context_updates=context_updates,
            )

        if (
            not merged_context.get("selected_specialty_id")
            and not merged_context.get("selected_doctor_id")
            and self._mentions_unknown_specialty(normalized_message)
        ):
            return ChatReceptionistReply(
                intent=ChatReceptionistIntent.AVAILABILITY_UNKNOWN_SPECIALTY,
                content=self._format_unknown_specialty_prompt(),
                chat_context_updates=context_updates,
            )

        if not selected_doctor_id and not selected_specialty_id:
            return ChatReceptionistReply(
                intent=ChatReceptionistIntent.AVAILABILITY_MISSING_DOCTOR,
                content=self._format_missing_scheduling_target_prompt(),
                chat_context_updates=context_updates,
            )

        if self._should_search_earliest_availability(merged_context):
            return self._handle_earliest_availability_flow(
                merged_context=merged_context,
                context_updates=context_updates,
            )

        if not requested_date:
            target_name = self._availability_target_name(merged_context)
            doctor_just_selected = bool(
                context_updates.get("selected_doctor_id")
                and merged_context.get("offered_doctors"),
            )
            return ChatReceptionistReply(
                intent=ChatReceptionistIntent.AVAILABILITY_MISSING_DATE,
                content=self._format_availability_missing_date_prompt(
                    target_name,
                    doctor_just_selected=doctor_just_selected,
                ),
                chat_context_updates={
                    **context_updates,
                    "appointment_intake_awaiting": APPOINTMENT_INTAKE_AWAITING_DATE_OR_TIME,
                },
            )

        parsed_requested_date = date.fromisoformat(str(requested_date))
        scheduling_validation = self._validate_scheduling_date(parsed_requested_date)
        if scheduling_validation is not None:
            return replace(
                scheduling_validation,
                chat_context_updates={
                    **context_updates,
                    **scheduling_validation.chat_context_updates,
                },
            )

        if selected_doctor_id:
            return self._handle_doctor_availability_flow(
                merged_context=merged_context,
                context_updates=context_updates,
                requested_date=str(requested_date),
                parsed_requested_date=parsed_requested_date,
            )

        return self._handle_specialty_availability_flow(
            merged_context=merged_context,
            context_updates=context_updates,
            requested_date=str(requested_date),
            parsed_requested_date=parsed_requested_date,
        )

    def _handle_doctor_availability_flow(
        self,
        *,
        merged_context: dict[str, Any],
        context_updates: dict[str, Any],
        requested_date: str,
        parsed_requested_date: date,
    ) -> ChatReceptionistReply:
        selected_doctor_id = merged_context.get("selected_doctor_id")
        assert selected_doctor_id is not None

        slots = self._query_availability(
            doctor_id=UUID(str(selected_doctor_id)),
            requested_date=parsed_requested_date,
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

        exact_time_reply = self._handle_exact_time_doctor_availability(
            slots=slots,
            merged_context=merged_context,
            context_updates=context_updates,
            doctor_name=doctor_name,
            requested_date=requested_date,
            parsed_requested_date=parsed_requested_date,
        )
        if exact_time_reply is not None:
            return exact_time_reply

        if slots:
            shown_slots = list(slots[:_MAX_OFFERED_SLOTS])
            offered_slots = self._serialize_offered_slots(
                shown_slots,
                doctor_names={slot.doctor_id: doctor_name for slot in shown_slots},
                specialty_name=merged_context.get("selected_specialty_name"),
                display_date=requested_date,
            )
            return ChatReceptionistReply(
                intent=ChatReceptionistIntent.AVAILABILITY_RESULTS,
                content=self._format_doctor_availability_slots(
                    slots,
                    doctor_name=doctor_name,
                    requested_date=requested_date,
                ),
                chat_context_updates={
                    **context_updates,
                    "offered_slots": offered_slots,
                    "appointment_intake_awaiting": APPOINTMENT_INTAKE_AWAITING_SLOT_SELECTION,
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

    def _handle_specialty_availability_flow(
        self,
        *,
        merged_context: dict[str, Any],
        context_updates: dict[str, Any],
        requested_date: str,
        parsed_requested_date: date,
    ) -> ChatReceptionistReply:
        selected_specialty_id = merged_context.get("selected_specialty_id")
        specialty_name = str(
            merged_context.get("selected_specialty_name", "the selected specialty"),
        )
        assert selected_specialty_id is not None

        availability_result = self._query_specialty_availability(
            specialty_id=UUID(str(selected_specialty_id)),
            requested_date=parsed_requested_date,
        )
        requested_time_window = merged_context.get("requested_time_window")
        attributed_slots = list(availability_result.available_slots)

        if isinstance(requested_time_window, dict):
            filtered_slots = self._filter_attributed_slots_by_time_window(
                attributed_slots,
                requested_time_window,
            )
            if attributed_slots and not filtered_slots:
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
            attributed_slots = filtered_slots

        exact_time_reply = self._handle_exact_time_specialty_availability(
            attributed_slots=attributed_slots,
            merged_context=merged_context,
            context_updates=context_updates,
            specialty_name=specialty_name,
            requested_date=requested_date,
            parsed_requested_date=parsed_requested_date,
        )
        if exact_time_reply is not None:
            return exact_time_reply

        if attributed_slots:
            shown_slots = attributed_slots[:_MAX_OFFERED_SLOTS]
            offered_slots = self._serialize_attributed_offered_slots(
                shown_slots,
                specialty_name=specialty_name,
                display_date=requested_date,
            )
            return ChatReceptionistReply(
                intent=ChatReceptionistIntent.AVAILABILITY_RESULTS,
                content=self._format_specialty_availability_slots(
                    shown_slots,
                    specialty_name=specialty_name,
                    requested_date=requested_date,
                ),
                chat_context_updates={
                    **context_updates,
                    "offered_slots": offered_slots,
                    "appointment_intake_awaiting": APPOINTMENT_INTAKE_AWAITING_SLOT_SELECTION,
                },
                availability_checked=True,
                offered_slot_count=len(offered_slots),
            )

        if (
            availability_result.suggested_response_text is not None
            and availability_result.status is AvailabilityCheckStatus.OUTSIDE_BOOKING_HORIZON
        ):
            return ChatReceptionistReply(
                intent=ChatReceptionistIntent.INVALID_DATE,
                content=availability_result.suggested_response_text,
                chat_context_updates={
                    **context_updates,
                    "offered_slots": [],
                },
                availability_checked=True,
                offered_slot_count=0,
            )

        return ChatReceptionistReply(
            intent=ChatReceptionistIntent.AVAILABILITY_NO_SLOTS,
            content=(
                f"I did not find open times for {specialty_name} on {requested_date}. "
                "Please try another date or doctor."
            ),
            chat_context_updates={
                **context_updates,
                "offered_slots": [],
            },
            availability_checked=True,
            offered_slot_count=0,
        )

    def _intake_enables_earliest_search(self, intake_updates: dict[str, Any]) -> bool:
        if intake_updates.get("soonest_requested"):
            return True
        if not intake_updates.get("search_start_date"):
            return False
        has_provider = bool(
            intake_updates.get("selected_doctor_id")
            or intake_updates.get("selected_specialty_id"),
        )
        return has_provider and not intake_updates.get("requested_date")

    def _should_search_earliest_availability(self, merged_context: dict[str, Any]) -> bool:
        has_provider = bool(
            merged_context.get("selected_doctor_id")
            or merged_context.get("selected_specialty_id"),
        )
        if not has_provider:
            return False
        if merged_context.get("soonest_requested"):
            return True
        return not bool(merged_context.get("requested_date"))

    def _resolve_earliest_search_window(
        self,
        merged_context: dict[str, Any],
    ) -> tuple[date, date] | None:
        start_date: date | None = None
        search_start_raw = merged_context.get("search_start_date")
        if isinstance(search_start_raw, str):
            start_date = date.fromisoformat(search_start_raw)
        elif self.clinic_time_service is not None:
            start_date = self.clinic_time_service.clinic_today()

        if start_date is None:
            return None

        search_end_raw = merged_context.get("search_end_date")
        if isinstance(search_end_raw, str):
            end_date = date.fromisoformat(search_end_raw)
        else:
            end_date = start_date + timedelta(days=EARLIEST_AVAILABILITY_SEARCH_HORIZON_DAYS)

        return start_date, end_date

    def _build_availability_range_datetimes(
        self,
        *,
        start_date: date,
        end_date: date,
    ) -> tuple[datetime, datetime]:
        start_from = datetime(start_date.year, start_date.month, start_date.day, tzinfo=UTC)
        start_to = datetime(end_date.year, end_date.month, end_date.day, tzinfo=UTC) + timedelta(
            days=1,
        )
        return start_from, start_to

    def _handle_earliest_availability_flow(
        self,
        *,
        merged_context: dict[str, Any],
        context_updates: dict[str, Any],
    ) -> ChatReceptionistReply:
        search_window = self._resolve_earliest_search_window(merged_context)
        if search_window is None:
            target_name = self._availability_target_name(merged_context)
            return ChatReceptionistReply(
                intent=ChatReceptionistIntent.AVAILABILITY_MISSING_DATE,
                content=self._format_availability_missing_date_prompt(target_name),
                chat_context_updates=context_updates,
            )

        start_date, end_date = search_window
        selected_doctor_id = merged_context.get("selected_doctor_id")
        if selected_doctor_id:
            return self._handle_earliest_doctor_availability_flow(
                merged_context=merged_context,
                context_updates=context_updates,
                start_date=start_date,
                end_date=end_date,
            )

        return self._handle_earliest_specialty_availability_flow(
            merged_context=merged_context,
            context_updates=context_updates,
            start_date=start_date,
            end_date=end_date,
        )

    def _handle_earliest_doctor_availability_flow(
        self,
        *,
        merged_context: dict[str, Any],
        context_updates: dict[str, Any],
        start_date: date,
        end_date: date,
    ) -> ChatReceptionistReply:
        selected_doctor_id = merged_context.get("selected_doctor_id")
        assert selected_doctor_id is not None

        start_from, start_to = self._build_availability_range_datetimes(
            start_date=start_date,
            end_date=end_date,
        )
        availability_result = self.scheduling.check_availability_with_status(
            doctor_id=UUID(str(selected_doctor_id)),
            start_from=start_from,
            start_to=start_to,
        )
        doctor_name = str(merged_context.get("selected_doctor_name", "the selected doctor"))
        slots = list(availability_result.available_slots)
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
                        f"I don't see any {label} openings for that request. "
                        "Would you like another time window or another day?"
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
            offered_slots = self._serialize_offered_slots(
                shown_slots,
                doctor_names={slot.doctor_id: doctor_name for slot in shown_slots},
                specialty_name=merged_context.get("selected_specialty_name"),
                use_slot_date=True,
            )
            return ChatReceptionistReply(
                intent=ChatReceptionistIntent.AVAILABILITY_RESULTS,
                content=self._format_earliest_doctor_availability_slots(
                    shown_slots,
                    doctor_name=doctor_name,
                ),
                chat_context_updates={
                    **context_updates,
                    "offered_slots": offered_slots,
                    "appointment_intake_awaiting": APPOINTMENT_INTAKE_AWAITING_SLOT_SELECTION,
                },
                availability_checked=True,
                offered_slot_count=len(offered_slots),
            )

        return ChatReceptionistReply(
            intent=ChatReceptionistIntent.AVAILABILITY_NO_SLOTS,
            content=_EARLIEST_NO_AVAILABILITY_MESSAGE,
            chat_context_updates={
                **context_updates,
                "offered_slots": [],
            },
            availability_checked=True,
            offered_slot_count=0,
        )

    def _handle_earliest_specialty_availability_flow(
        self,
        *,
        merged_context: dict[str, Any],
        context_updates: dict[str, Any],
        start_date: date,
        end_date: date,
    ) -> ChatReceptionistReply:
        selected_specialty_id = merged_context.get("selected_specialty_id")
        specialty_name = str(
            merged_context.get("selected_specialty_name", "the selected specialty"),
        )
        assert selected_specialty_id is not None

        start_from, start_to = self._build_availability_range_datetimes(
            start_date=start_date,
            end_date=end_date,
        )
        availability_result = self.scheduling.check_availability_for_specialty(
            specialty_id=UUID(str(selected_specialty_id)),
            start_from=start_from,
            start_to=start_to,
            limit=_MAX_OFFERED_SLOTS,
        )
        requested_time_window = merged_context.get("requested_time_window")
        attributed_slots = list(availability_result.available_slots)

        if isinstance(requested_time_window, dict):
            filtered_slots = self._filter_attributed_slots_by_time_window(
                attributed_slots,
                requested_time_window,
            )
            if attributed_slots and not filtered_slots:
                label = str(requested_time_window.get("label", "requested"))
                return ChatReceptionistReply(
                    intent=ChatReceptionistIntent.AVAILABILITY_NO_MATCHING_TIME_WINDOW,
                    content=(
                        f"I don't see any {label} openings for that request. "
                        "Would you like another time window or another day?"
                    ),
                    chat_context_updates={
                        **context_updates,
                        "offered_slots": [],
                    },
                    availability_checked=True,
                    offered_slot_count=0,
                )
            attributed_slots = filtered_slots

        if attributed_slots:
            shown_slots = attributed_slots[:_MAX_OFFERED_SLOTS]
            slot_date = shown_slots[0].slot.start_time.date().isoformat()
            offered_slots = self._serialize_attributed_offered_slots(
                shown_slots,
                specialty_name=specialty_name,
                display_date=slot_date,
            )
            return ChatReceptionistReply(
                intent=ChatReceptionistIntent.AVAILABILITY_RESULTS,
                content=self._format_earliest_specialty_availability_slots(
                    shown_slots,
                    specialty_name=specialty_name,
                ),
                chat_context_updates={
                    **context_updates,
                    "offered_slots": offered_slots,
                    "appointment_intake_awaiting": APPOINTMENT_INTAKE_AWAITING_SLOT_SELECTION,
                },
                availability_checked=True,
                offered_slot_count=len(offered_slots),
            )

        return ChatReceptionistReply(
            intent=ChatReceptionistIntent.AVAILABILITY_NO_SLOTS,
            content=_EARLIEST_NO_AVAILABILITY_MESSAGE,
            chat_context_updates={
                **context_updates,
                "offered_slots": [],
            },
            availability_checked=True,
            offered_slot_count=0,
        )

    def _handle_availability_range_flow(
        self,
        *,
        merged_context: dict[str, Any],
        context_updates: dict[str, Any],
    ) -> ChatReceptionistReply:
        label = str(merged_context.get("availability_range_label") or "")
        search_window = self._resolve_earliest_search_window(merged_context)
        if search_window is None:
            target_name = self._availability_target_name(merged_context)
            return ChatReceptionistReply(
                intent=ChatReceptionistIntent.AVAILABILITY_MISSING_DATE,
                content=self._format_availability_missing_date_prompt(target_name),
                chat_context_updates=context_updates,
            )

        start_date, end_date = search_window
        if merged_context.get("selected_doctor_id"):
            return self._handle_availability_range_doctor_flow(
                merged_context=merged_context,
                context_updates=context_updates,
                start_date=start_date,
                end_date=end_date,
                label=label,
            )

        return self._handle_availability_range_specialty_flow(
            merged_context=merged_context,
            context_updates=context_updates,
            start_date=start_date,
            end_date=end_date,
            label=label,
        )

    def _handle_availability_range_doctor_flow(
        self,
        *,
        merged_context: dict[str, Any],
        context_updates: dict[str, Any],
        start_date: date,
        end_date: date,
        label: str,
    ) -> ChatReceptionistReply:
        selected_doctor_id = merged_context.get("selected_doctor_id")
        assert selected_doctor_id is not None

        start_from, start_to = self._build_availability_range_datetimes(
            start_date=start_date,
            end_date=end_date,
        )
        availability_result = self.scheduling.check_availability_with_status(
            doctor_id=UUID(str(selected_doctor_id)),
            start_from=start_from,
            start_to=start_to,
        )
        doctor_name = str(merged_context.get("selected_doctor_name", "the selected doctor"))
        slots = list(availability_result.available_slots)
        requested_time_window = merged_context.get("requested_time_window")

        if isinstance(requested_time_window, dict):
            filtered_slots = self._filter_slots_by_time_window(slots, requested_time_window)
            if slots and not filtered_slots:
                return self._availability_range_no_slots_reply(
                    context_updates=context_updates,
                    label=label,
                )
            slots = filtered_slots

        if slots:
            shown_slots = self._select_range_slots(slots, key=lambda slot: slot.start_time)
            offered_slots = self._serialize_offered_slots(
                shown_slots,
                doctor_names={slot.doctor_id: doctor_name for slot in shown_slots},
                specialty_name=merged_context.get("selected_specialty_name"),
                use_slot_date=True,
            )
            return ChatReceptionistReply(
                intent=ChatReceptionistIntent.AVAILABILITY_RESULTS,
                content=self._format_doctor_availability_range_slots(
                    shown_slots,
                    doctor_name=doctor_name,
                    label=label,
                ),
                chat_context_updates=self._availability_range_results_context_updates(
                    context_updates=context_updates,
                    offered_slots=offered_slots,
                    label=label,
                ),
                availability_checked=True,
                offered_slot_count=len(offered_slots),
            )

        return self._availability_range_no_slots_reply(
            context_updates=context_updates,
            label=label,
        )

    def _handle_availability_range_specialty_flow(
        self,
        *,
        merged_context: dict[str, Any],
        context_updates: dict[str, Any],
        start_date: date,
        end_date: date,
        label: str,
    ) -> ChatReceptionistReply:
        selected_specialty_id = merged_context.get("selected_specialty_id")
        specialty_name = str(
            merged_context.get("selected_specialty_name", "the selected specialty"),
        )
        assert selected_specialty_id is not None

        start_from, start_to = self._build_availability_range_datetimes(
            start_date=start_date,
            end_date=end_date,
        )
        availability_result = self.scheduling.check_availability_for_specialty(
            specialty_id=UUID(str(selected_specialty_id)),
            start_from=start_from,
            start_to=start_to,
            limit=_MAX_OFFERED_SLOTS,
        )
        attributed_slots = list(availability_result.available_slots)
        requested_time_window = merged_context.get("requested_time_window")

        if isinstance(requested_time_window, dict):
            filtered_slots = self._filter_attributed_slots_by_time_window(
                attributed_slots,
                requested_time_window,
            )
            if attributed_slots and not filtered_slots:
                return self._availability_range_no_slots_reply(
                    context_updates=context_updates,
                    label=label,
                )
            attributed_slots = filtered_slots

        if attributed_slots:
            shown_slots = self._select_range_slots(
                attributed_slots,
                key=lambda item: item.slot.start_time,
            )
            offered_slots = self._serialize_attributed_offered_slots(
                shown_slots,
                specialty_name=specialty_name,
                use_slot_date=True,
            )
            return ChatReceptionistReply(
                intent=ChatReceptionistIntent.AVAILABILITY_RESULTS,
                content=self._format_specialty_availability_range_slots(
                    shown_slots,
                    specialty_name=specialty_name,
                    label=label,
                ),
                chat_context_updates=self._availability_range_results_context_updates(
                    context_updates=context_updates,
                    offered_slots=offered_slots,
                    label=label,
                ),
                availability_checked=True,
                offered_slot_count=len(offered_slots),
            )

        return self._availability_range_no_slots_reply(
            context_updates=context_updates,
            label=label,
        )

    def _availability_range_results_context_updates(
        self,
        *,
        context_updates: dict[str, Any],
        offered_slots: list[dict[str, Any]],
        label: str,
    ) -> dict[str, Any]:
        return {
            **context_updates,
            "offered_slots": offered_slots,
            "selected_availability_slot_id": None,
            "selected_start_time": None,
            "appointment_intake_awaiting": APPOINTMENT_INTAKE_AWAITING_SLOT_SELECTION,
            "availability_range_label": label or None,
            "requested_date": None,
            "search_start_date": None,
            "search_end_date": None,
        }

    def _availability_range_no_slots_reply(
        self,
        *,
        context_updates: dict[str, Any],
        label: str,
    ) -> ChatReceptionistReply:
        return ChatReceptionistReply(
            intent=ChatReceptionistIntent.AVAILABILITY_NO_SLOTS,
            content=self._format_availability_range_no_slots(label),
            chat_context_updates={
                **context_updates,
                "offered_slots": [],
                "selected_availability_slot_id": None,
                "selected_start_time": None,
                "appointment_intake_awaiting": APPOINTMENT_INTAKE_AWAITING_DATE_OR_TIME,
                "availability_range_label": None,
                "requested_date": None,
                "search_start_date": None,
                "search_end_date": None,
            },
            availability_checked=True,
            offered_slot_count=0,
        )

    def _select_range_slots(
        self,
        slots: Sequence[Any],
        *,
        key: Any,
    ) -> list[Any]:
        return sorted(slots, key=key)[:_MAX_OFFERED_SLOTS]

    def _range_label_prefix(self, label: str) -> str:
        if label == APPOINTMENT_AVAILABILITY_RANGE_NEXT_WEEK:
            return "Next week"
        if label == APPOINTMENT_AVAILABILITY_RANGE_THIS_WEEK:
            return "This week"
        return "That week"

    def _range_label_phrase(self, label: str) -> str:
        if label == APPOINTMENT_AVAILABILITY_RANGE_NEXT_WEEK:
            return "next week"
        if label == APPOINTMENT_AVAILABILITY_RANGE_THIS_WEEK:
            return "this week"
        return "that week"

    def _format_availability_range_no_slots(self, label: str) -> str:
        return (
            f"I'm not seeing openings for that request {self._range_label_phrase(label)}. "
            "Would you like me to check another week or a different time window?"
        )

    def _join_day_groups(self, parts: Sequence[str]) -> str:
        if not parts:
            return ""
        if len(parts) == 1:
            return parts[0]
        if len(parts) == 2:
            return f"{parts[0]}, and {parts[1]}"
        return ", ".join(parts[:-1]) + f", and {parts[-1]}"

    def _format_doctor_availability_range_slots(
        self,
        slots: Sequence[AvailabilitySlot],
        *,
        doctor_name: str,
        label: str,
    ) -> str:
        clinic_tz = self._clinic_timezone()
        grouped: dict[date, list[datetime]] = {}
        for slot in slots:
            local_start = to_clinic_local_datetime(slot.start_time, clinic_tz)
            grouped.setdefault(local_start.date(), []).append(local_start)

        day_parts: list[str] = []
        for slot_date in sorted(grouped):
            times_text = self._join_names(
                [start.strftime("%H:%M") for start in sorted(grouped[slot_date])],
            )
            day_parts.append(f"{slot_date.strftime('%A')} at {times_text}")

        body = self._join_day_groups(day_parts)
        return (
            f"{self._range_label_prefix(label)}, I found openings with {doctor_name} "
            f"on {body}. Which time works better?"
        )

    def _format_specialty_availability_range_slots(
        self,
        slots: Sequence[DoctorAttributedAvailabilitySlot],
        *,
        specialty_name: str,
        label: str,
    ) -> str:
        clinic_tz = self._clinic_timezone()
        grouped: dict[date, list[datetime]] = {}
        for item in slots:
            local_start = to_clinic_local_datetime(item.slot.start_time, clinic_tz)
            grouped.setdefault(local_start.date(), []).append(local_start)

        day_parts: list[str] = []
        for slot_date in sorted(grouped):
            times_text = self._join_names(
                [start.strftime("%H:%M") for start in sorted(grouped[slot_date])],
            )
            day_parts.append(f"{slot_date.strftime('%A')} at {times_text}")

        body = self._join_day_groups(day_parts)
        return (
            f"{self._range_label_prefix(label)}, I found {specialty_name} openings "
            f"on {body}. Which time works better?"
        )

    def _format_availability_date_label(self, slot_date: date) -> str:
        if self.clinic_time_service is not None:
            clinic_today = self.clinic_time_service.clinic_today()
            if slot_date == clinic_today:
                return "today"
            if slot_date == clinic_today + timedelta(days=1):
                return "tomorrow"
        return slot_date.strftime("%A")

    def _format_earliest_doctor_availability_slots(
        self,
        slots: Sequence[AvailabilitySlot],
        *,
        doctor_name: str,
    ) -> str:
        if not slots:
            return _EARLIEST_NO_AVAILABILITY_MESSAGE

        clinic_tz = self._clinic_timezone()
        grouped: dict[date, list[datetime]] = {}
        for slot in slots:
            local_start = to_clinic_local_datetime(slot.start_time, clinic_tz)
            grouped.setdefault(local_start.date(), []).append(local_start)

        day_parts: list[str] = []
        for slot_date in sorted(grouped):
            date_label = self._format_availability_date_label(slot_date)
            times_text = self._join_names(
                [start.strftime("%H:%M") for start in sorted(grouped[slot_date])],
            )
            day_parts.append(f"{date_label} at {times_text}")

        body = "; ".join(day_parts)
        suffix = ""
        if len(slots) > _MAX_OFFERED_SLOTS:
            suffix = f" There are {len(slots) - _MAX_OFFERED_SLOTS} more openings available."

        return (
            f"I found openings with {doctor_name} {body}. "
            f"Which time works better?{suffix}"
        )

    def _format_earliest_specialty_availability_slots(
        self,
        slots: Sequence[DoctorAttributedAvailabilitySlot],
        *,
        specialty_name: str,
    ) -> str:
        if not slots:
            return _EARLIEST_NO_AVAILABILITY_MESSAGE

        clinic_tz = self._clinic_timezone()
        first = slots[0]
        first_local = to_clinic_local_datetime(first.slot.start_time, clinic_tz)
        slot_date = first_local.date()
        date_label = self._format_availability_date_label(slot_date)
        doctor_name = first.doctor_name
        same_doctor_and_day = all(
            item.doctor_name == doctor_name
            and to_clinic_local_datetime(item.slot.start_time, clinic_tz).date() == slot_date
            for item in slots
        )

        if same_doctor_and_day:
            times = [
                format_clinic_local_time_label(item.slot.start_time, clinic_tz)
                for item in slots
            ]
            times_text = self._join_names(times)
            return (
                f"The earliest {specialty_name.lower()} openings I found are with "
                f"{doctor_name} {date_label} at {times_text}. Which time works better?"
            )

        opening_descriptions = [
            (
                f"{format_clinic_local_time_label(item.slot.start_time, clinic_tz)} "
                f"with {item.doctor_name}"
            )
            for item in slots
        ]
        openings_text = self._join_names(opening_descriptions)
        return (
            f"The earliest {specialty_name.lower()} openings I found are {date_label}: "
            f"{openings_text}. Which time works better?"
        )

    def _availability_target_name(self, merged_context: dict[str, Any]) -> str:
        doctor_name = merged_context.get("selected_doctor_name")
        if isinstance(doctor_name, str) and doctor_name.strip():
            return doctor_name

        specialty_name = merged_context.get("selected_specialty_name")
        if isinstance(specialty_name, str) and specialty_name.strip():
            return specialty_name

        return "your appointment"

    def _format_missing_scheduling_target_prompt(self) -> str:
        return (
            "What kind of appointment are you looking for? "
            "You can mention a specialty or preferred doctor."
        )

    def _format_unknown_specialty_prompt(self) -> str:
        specialties = self.scheduling.list_specialties()
        if not specialties:
            return (
                "I could not find that specialty. "
                "Please tell me which type of appointment you are looking for."
            )

        names = [specialty.name for specialty in specialties]
        return (
            f"I could not find that specialty. We currently support "
            f"{self._join_names(names)}. Which one would you like?"
        )

    def _format_unknown_doctor_prompt(self) -> str:
        return (
            "I could not find that doctor. "
            "Would you like to choose a different doctor or search by specialty?"
        )

    def _mentions_unknown_doctor(self, normalized_message: str) -> bool:
        if not normalized_message:
            return False

        if self._match_doctor_in_message(normalized_message) is not None:
            return False

        return _DR_MENTION_PATTERN.search(normalized_message) is not None

    def _mentions_unknown_specialty(self, normalized_message: str) -> bool:
        if not normalized_message:
            return False

        if self._match_specialty_in_message(normalized_message) is not None:
            return False

        if _DR_MENTION_PATTERN.search(normalized_message) is not None:
            return False

        return _SPECIALTY_MENTION_PATTERN.search(normalized_message) is not None

    def _format_missing_doctor_prompt(self, merged_context: dict[str, Any]) -> str:
        return self._format_missing_scheduling_target_prompt()

    def _validate_scheduling_date(
        self,
        requested_date: date,
    ) -> ChatReceptionistReply | None:
        if self.clinic_time_service is None:
            return None

        resolved_date = self.clinic_time_service.resolve_date(
            DateExpression(
                kind=DateExpressionKind.EXACT_DATE,
                exact_date=requested_date,
            ),
        )
        if resolved_date.status is DateResolutionStatus.RESOLVED:
            return None

        if resolved_date.status is DateResolutionStatus.CLOSED_DAY:
            return ChatReceptionistReply(
                intent=ChatReceptionistIntent.INVALID_DATE,
                content=(
                    "The clinic is closed on that day. "
                    "Please choose a weekday during business hours."
                ),
                chat_context_updates={},
            )

        return ChatReceptionistReply(
            intent=ChatReceptionistIntent.INVALID_DATE,
            content=(
                "That date is in the past or cannot be used for scheduling. "
                "Please provide a future clinic business day."
            ),
            chat_context_updates={},
        )

    def _query_availability(
        self,
        *,
        doctor_id: UUID,
        requested_date: date,
    ) -> Sequence[AvailabilitySlot]:
        if self.clinic_time_service is not None:
            availability_window = SchedulingAvailabilityResolver(
                self.clinic_time_service,
                date_parser=self.date_parser,
            ).resolve_check_availability(
                CheckAvailabilityToolArguments(
                    date_expression=DateExpressionSchema(
                        kind=DateExpressionKind.EXACT_DATE,
                        exact_date=requested_date,
                    ),
                ),
                {},
            )
            if availability_window.is_resolved:
                assert availability_window.start_from is not None
                assert availability_window.end_to is not None
                return self.scheduling.check_availability(
                    doctor_id=doctor_id,
                    start_from=availability_window.start_from,
                    start_to=availability_window.end_to,
                )
            return []

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

    def _query_specialty_availability(
        self,
        *,
        specialty_id: UUID,
        requested_date: date,
    ) -> SpecialtyAvailabilityCheckResult:
        if self.clinic_time_service is not None:
            availability_window = SchedulingAvailabilityResolver(
                self.clinic_time_service,
                date_parser=self.date_parser,
            ).resolve_check_availability(
                CheckAvailabilityToolArguments(
                    date_expression=DateExpressionSchema(
                        kind=DateExpressionKind.EXACT_DATE,
                        exact_date=requested_date,
                    ),
                ),
                {},
            )
            if availability_window.is_resolved:
                assert availability_window.start_from is not None
                assert availability_window.end_to is not None
                return self.scheduling.check_availability_for_specialty(
                    specialty_id=specialty_id,
                    start_from=availability_window.start_from,
                    start_to=availability_window.end_to,
                    limit=_MAX_OFFERED_SLOTS,
                )
            return SpecialtyAvailabilityCheckResult(
                status=AvailabilityCheckStatus.NO_MATCHING_SLOTS,
                available_slots=[],
            )

        start_from = datetime(
            requested_date.year,
            requested_date.month,
            requested_date.day,
            tzinfo=UTC,
        )
        start_to = start_from + timedelta(days=1)

        return self.scheduling.check_availability_for_specialty(
            specialty_id=specialty_id,
            start_from=start_from,
            start_to=start_to,
            limit=_MAX_OFFERED_SLOTS,
        )

    def _serialize_offered_doctors(
        self,
        doctors: Sequence[Doctor],
        *,
        specialty: Specialty | None = None,
    ) -> list[dict[str, Any]]:
        specialty_names_by_id = {
            str(item.id): item.name for item in self.scheduling.list_specialties()
        }
        offered: list[dict[str, Any]] = []
        for index, doctor in enumerate(doctors, start=1):
            specialty_id = (
                str(specialty.id)
                if specialty is not None
                else (
                    str(doctor.specialty_id) if doctor.specialty_id is not None else None
                )
            )
            specialty_name = (
                specialty.name
                if specialty is not None
                else specialty_names_by_id.get(specialty_id or "", None)
            )
            offered.append(
                {
                    "reference": f"doctor-{index}",
                    "doctor_id": str(doctor.id),
                    "doctor_name": doctor.full_name,
                    "specialty_id": specialty_id,
                    "specialty_name": specialty_name,
                },
            )
        return offered

    def _serialize_offered_slots(
        self,
        slots: Sequence[AvailabilitySlot],
        *,
        doctor_names: dict[UUID, str] | None = None,
        specialty_name: str | None = None,
        display_date: str | None = None,
        use_slot_date: bool = False,
    ) -> list[dict[str, Any]]:
        doctor_names = doctor_names or {}
        clinic_tz = self._clinic_timezone()
        return [
            {
                "availability_slot_id": str(slot.id),
                "doctor_id": str(slot.doctor_id),
                "doctor_name": doctor_names.get(slot.doctor_id, ""),
                "specialty_name": specialty_name,
                "start_time": slot.start_time.isoformat(),
                "display_time": format_clinic_local_time_label(slot.start_time, clinic_tz),
                "display_date": (
                    to_clinic_local_datetime(slot.start_time, clinic_tz).date().isoformat()
                    if use_slot_date
                    else display_date
                ),
            }
            for slot in slots
        ]

    def _serialize_attributed_offered_slots(
        self,
        slots: Sequence[DoctorAttributedAvailabilitySlot],
        *,
        specialty_name: str | None = None,
        display_date: str | None = None,
        use_slot_date: bool = False,
    ) -> list[dict[str, Any]]:
        clinic_tz = self._clinic_timezone()
        return [
            {
                "availability_slot_id": str(item.slot.id),
                "doctor_id": str(item.doctor_id),
                "doctor_name": item.doctor_name,
                "specialty_name": specialty_name,
                "start_time": item.slot.start_time.isoformat(),
                "display_time": format_clinic_local_time_label(
                    item.slot.start_time,
                    clinic_tz,
                ),
                "display_date": (
                    to_clinic_local_datetime(item.slot.start_time, clinic_tz)
                    .date()
                    .isoformat()
                    if use_slot_date
                    else display_date
                ),
            }
            for item in slots
        ]

    def _format_doctor_availability_slots(
        self,
        slots: Sequence[AvailabilitySlot],
        *,
        doctor_name: str,
        requested_date: str,
    ) -> str:
        shown_slots = list(slots[:_MAX_OFFERED_SLOTS])
        clinic_tz = self._clinic_timezone()
        times = [
            format_clinic_local_time_label(slot.start_time, clinic_tz) for slot in shown_slots
        ]
        times_text = self._join_names(times)
        suffix = ""

        if len(slots) > _MAX_OFFERED_SLOTS:
            suffix = f" There are {len(slots) - _MAX_OFFERED_SLOTS} more openings available."

        return (
            f"I found openings with {doctor_name} on {requested_date} at {times_text}. "
            f"Which time works better?{suffix}"
        )

    def _format_specialty_availability_slots(
        self,
        slots: Sequence[DoctorAttributedAvailabilitySlot],
        *,
        specialty_name: str,
        requested_date: str,
    ) -> str:
        clinic_tz = self._clinic_timezone()
        opening_descriptions = [
            (
                f"{format_clinic_local_time_label(item.slot.start_time, clinic_tz)} "
                f"with {item.doctor_name}"
            )
            for item in slots
        ]
        openings_text = self._join_names(opening_descriptions)

        return (
            f"I found {specialty_name} openings on {requested_date}: {openings_text}. "
            "Which time works better?"
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

        if parse_result.status == TimePreferenceStatus.PARSED and parse_result.window is not None:
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
        clinic_tz = self._clinic_timezone()
        return [
            slot
            for slot in slots
            if is_time_in_window(
                time_value=format_clinic_local_time_label(slot.start_time, clinic_tz),
                window=time_window,
            )
        ]

    def _filter_attributed_slots_by_time_window(
        self,
        slots: Sequence[DoctorAttributedAvailabilitySlot],
        window: dict[str, Any],
    ) -> list[DoctorAttributedAvailabilitySlot]:
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
        clinic_tz = self._clinic_timezone()
        return [
            item
            for item in slots
            if is_time_in_window(
                time_value=format_clinic_local_time_label(
                    item.slot.start_time,
                    clinic_tz,
                ),
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
            or merged_context.get("selected_specialty_id")
            or merged_context.get("requested_date"),
        )

    def _should_enter_availability_after_intake(
        self,
        *,
        context_updates: dict[str, Any],
        merged_context: dict[str, Any],
    ) -> bool:
        has_provider = bool(
            merged_context.get("selected_doctor_id")
            or merged_context.get("selected_specialty_id"),
        )
        if not has_provider:
            return False

        scheduling_fields = {"requested_date", "requested_time_window", "requested_exact_time"}
        provider_fields = {"selected_doctor_id", "selected_specialty_id"}

        if scheduling_fields & context_updates.keys():
            return True

        if provider_fields & context_updates.keys() and not merged_context.get("requested_date"):
            return True

        return False

    def _should_complete_availability_from_context(
        self,
        *,
        merged_context: dict[str, Any],
        context_updates: dict[str, Any],
    ) -> bool:
        has_target = bool(
            merged_context.get("selected_doctor_id")
            or merged_context.get("selected_specialty_id"),
        )
        has_date = bool(merged_context.get("requested_date"))

        if not has_target or not has_date:
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

        if self._is_single_slot_affirmative_hold_selection(
            message=message,
            merged_context=merged_context,
        ):
            return True

        if offered_slots and "book" in normalized_message:
            return True

        if self.responder._contains_any(normalized_message, _HOLD_KEYWORDS):
            return True

        # A bare clock-like time only signals slot selection / a hold request once
        # availability has been shown (offered slots) or while a hold is being
        # confirmed/selected. Without that context a concrete time is part of a
        # rich scheduling request and must reach availability/intake instead.
        if self._message_has_time_pattern(normalized_message) and (
            offered_slots or self._has_active_hold_context(merged_context)
        ):
            return True

        if offered_slots and self._extract_offered_time(message) is not None:
            return True

        if offered_slots and _BARE_INTEGER_PATTERN.fullmatch(message.strip()):
            return True

        return self._extract_iso_datetime(message) is not None

    def _has_active_hold_context(self, merged_context: dict[str, Any]) -> bool:
        if merged_context.get("hold_id"):
            return True
        return (
            merged_context.get("appointment_intake_awaiting")
            == APPOINTMENT_INTAKE_AWAITING_SLOT_SELECTION
        )

    def _is_single_slot_affirmative_hold_selection(
        self,
        *,
        message: str,
        merged_context: dict[str, Any],
    ) -> bool:
        offered_slots = merged_context.get("offered_slots") or []
        if len(offered_slots) != 1:
            return False
        if (
            merged_context.get("appointment_intake_awaiting")
            != APPOINTMENT_INTAKE_AWAITING_SLOT_SELECTION
        ):
            return False
        return is_simple_affirmative(message)

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
                    "Please check availability first so I can hold one of the available times."
                ),
                chat_context_updates=context_updates,
                hold_created=False,
            )

        selection = self._select_offered_slot(
            message,
            normalized_message,
            offered_slots,
        )

        if selection.ambiguous:
            return ChatReceptionistReply(
                intent=ChatReceptionistIntent.HOLD_SLOT_NOT_FOUND,
                content=(
                    "I found more than one matching time. "
                    "Please choose by option number or day."
                ),
                chat_context_updates=context_updates,
                hold_created=False,
            )

        selected_slot = selection.slot

        if selected_slot is None and self._is_single_slot_affirmative_hold_selection(
            message=message,
            merged_context=merged_context,
        ):
            selected_slot = offered_slots[0]

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
                    "Please choose one of the listed times so I can hold it for you temporarily."
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
                ttl_seconds=self._chat_appointment_hold_ttl_seconds,
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
        except AppointmentHoldStoreUnavailableError:
            return ChatReceptionistReply(
                intent=ChatReceptionistIntent.HOLD_CONFLICT,
                content=(
                    "I could not reserve that time right now. "
                    "Please try again in a moment or choose another available time."
                ),
                chat_context_updates=context_updates,
                hold_created=False,
            )

        hold_expires_at = hold.created_at + timedelta(
            seconds=self._chat_appointment_hold_ttl_seconds,
        )
        display_time = str(
            selected_slot.get(
                "display_time",
                format_clinic_local_time_label(slot.start_time, self._clinic_timezone()),
            ),
        )
        doctor_name = str(
            selected_slot.get("doctor_name")
            or merged_context.get("selected_doctor_name", "the selected doctor"),
        )

        hold_context_updates = {
            **context_updates,
            "selected_availability_slot_id": str(slot.id),
            "selected_doctor_id": str(slot.doctor_id),
            "selected_doctor_name": doctor_name,
            "selected_start_time": slot.start_time.isoformat(),
            "hold_id": str(hold.hold_id),
            "hold_expires_at": hold_expires_at.isoformat(),
            "hold_owner_id": owner_id,
            "appointment_intake_awaiting": None,
        }
        hold_context_updates = self._booking_identity.hold_created_context_updates(
            hold_context_updates,
        )
        hold_content = self._booking_identity.hold_created_message(
            display_time=display_time,
            doctor_name=doctor_name,
        )

        return ChatReceptionistReply(
            intent=ChatReceptionistIntent.HOLD_CREATED,
            content=hold_content,
            chat_context_updates=hold_context_updates,
            hold_created=True,
            hold_id=str(hold.hold_id),
        )

    def _select_offered_slot(
        self,
        message: str,
        normalized_message: str,
        offered_slots: list[dict[str, Any]],
    ) -> _OfferedSlotSelection:
        for keyword, index in _ORDINAL_SLOT_KEYWORDS.items():
            if keyword in normalized_message and index < len(offered_slots):
                return _OfferedSlotSelection(slot=offered_slots[index])

        iso_datetime = self._extract_iso_datetime(message)
        if iso_datetime is not None:
            for offered_slot in offered_slots:
                if offered_slot.get("start_time") == iso_datetime.isoformat():
                    return _OfferedSlotSelection(slot=offered_slot)

        bare_number_selection = self._select_offered_slot_by_bare_number(
            message,
            offered_slots,
        )
        if bare_number_selection is not None:
            return bare_number_selection

        normalized_time = self._extract_offered_time(message)
        if normalized_time is not None:
            return self._match_offered_slots_by_time(normalized_time, offered_slots)

        return _OfferedSlotSelection()

    def _select_offered_slot_by_bare_number(
        self,
        message: str,
        offered_slots: list[dict[str, Any]],
    ) -> _OfferedSlotSelection | None:
        """Resolve a bare integer message against offered slots.

        Returns ``None`` when the message is not a bare integer, so the caller
        can fall back to the regular clock-time interpretation. Slot-aware
        interpretation lives here (rather than in the context-free normalizer)
        because this layer knows the offered options:

            * a bare integer that maps to an offered option index keeps the
              existing option-number selection behavior; otherwise
            * the integer is treated as an ``HH:00`` clock time and matched
              against offered ``display_time`` values (preserving the
              ambiguity-safe behavior when more than one slot matches).
        """
        candidate = message.strip()
        if not _BARE_INTEGER_PATTERN.fullmatch(candidate):
            return None

        number = int(candidate)

        if 1 <= number <= len(offered_slots):
            return _OfferedSlotSelection(slot=offered_slots[number - 1])

        return self._match_offered_slots_by_time(f"{number:02d}:00", offered_slots)

    def _match_offered_slots_by_time(
        self,
        normalized_time: str,
        offered_slots: list[dict[str, Any]],
    ) -> _OfferedSlotSelection:
        matches = [
            offered_slot
            for offered_slot in offered_slots
            if offered_slot.get("display_time") == normalized_time
        ]
        distinct_starts = {match.get("start_time") for match in matches}
        if len(distinct_starts) > 1:
            return _OfferedSlotSelection(ambiguous=True)
        if matches:
            return _OfferedSlotSelection(slot=matches[0])
        return _OfferedSlotSelection()

    def _message_has_slot_selection_attempt(
        self,
        normalized_message: str,
        message: str,
    ) -> bool:
        if any(keyword in normalized_message for keyword in _ORDINAL_SLOT_KEYWORDS):
            return True

        if self._message_has_time_pattern(normalized_message):
            return True

        if self._extract_offered_time(message) is not None:
            return True

        if _BARE_INTEGER_PATTERN.fullmatch(message.strip()):
            return True

        return self._extract_iso_datetime(message) is not None

    def _message_has_time_pattern(self, normalized_message: str) -> bool:
        return _TIME_PATTERN.search(normalized_message) is not None

    def _requested_exact_time(self, merged_context: dict[str, Any]) -> str | None:
        exact_time = merged_context.get("requested_exact_time")
        if isinstance(exact_time, str) and exact_time:
            return exact_time
        return None

    def _filter_slots_by_exact_time(
        self,
        slots: Sequence[AvailabilitySlot],
        exact_time: str,
    ) -> list[AvailabilitySlot]:
        clinic_tz = self._clinic_timezone()
        return [
            slot
            for slot in slots
            if format_clinic_local_time_label(slot.start_time, clinic_tz) == exact_time
        ]

    def _filter_attributed_slots_by_exact_time(
        self,
        slots: Sequence[DoctorAttributedAvailabilitySlot],
        exact_time: str,
    ) -> list[DoctorAttributedAvailabilitySlot]:
        clinic_tz = self._clinic_timezone()
        return [
            item
            for item in slots
            if format_clinic_local_time_label(item.slot.start_time, clinic_tz) == exact_time
        ]

    def _format_weekday_label(self, parsed_date: date) -> str:
        return parsed_date.strftime("%A")

    def _format_exact_time_available_hold_prompt(
        self,
        *,
        provider_name: str,
        parsed_date: date,
        display_time: str,
    ) -> str:
        weekday = self._format_weekday_label(parsed_date)
        return (
            f"Yes, {provider_name} is available on {weekday} at {display_time}. "
            "Would you like me to hold that time?"
        )

    def _format_exact_time_unavailable_with_alternatives(
        self,
        *,
        provider_name: str,
        parsed_date: date,
        requested_exact_time: str,
        alternative_times: Sequence[str],
    ) -> str:
        weekday = self._format_weekday_label(parsed_date)
        if alternative_times:
            alternatives_text = self._join_names(list(alternative_times))
            return (
                f"{requested_exact_time} is not available with {provider_name} on "
                f"{weekday}, but I found these openings that day: {alternatives_text}. "
                "Which time would you like?"
            )
        return (
            f"I don't have {requested_exact_time} available with {provider_name} on "
            f"{weekday}. Would you like me to check another day or time window?"
        )

    def _handle_exact_time_doctor_availability(
        self,
        *,
        slots: Sequence[AvailabilitySlot],
        merged_context: dict[str, Any],
        context_updates: dict[str, Any],
        doctor_name: str,
        requested_date: str,
        parsed_requested_date: date,
    ) -> ChatReceptionistReply | None:
        requested_exact_time = self._requested_exact_time(merged_context)
        if requested_exact_time is None:
            return None

        exact_matches = self._filter_slots_by_exact_time(slots, requested_exact_time)
        clinic_tz = self._clinic_timezone()

        if len(exact_matches) == 1:
            slot = exact_matches[0]
            display_time = format_clinic_local_time_label(slot.start_time, clinic_tz)
            offered_slots = self._serialize_offered_slots(
                [slot],
                doctor_names={slot.doctor_id: doctor_name},
                specialty_name=merged_context.get("selected_specialty_name"),
                display_date=requested_date,
            )
            return ChatReceptionistReply(
                intent=ChatReceptionistIntent.AVAILABILITY_RESULTS,
                content=self._format_exact_time_available_hold_prompt(
                    provider_name=doctor_name,
                    parsed_date=parsed_requested_date,
                    display_time=display_time,
                ),
                chat_context_updates={
                    **context_updates,
                    "offered_slots": offered_slots,
                    "requested_exact_time": requested_exact_time,
                    "appointment_intake_awaiting": APPOINTMENT_INTAKE_AWAITING_SLOT_SELECTION,
                },
                availability_checked=True,
                offered_slot_count=1,
            )

        if not exact_matches:
            alternative_times = [
                format_clinic_local_time_label(slot.start_time, clinic_tz) for slot in slots
            ]
            shown_slots = list(slots[:_MAX_OFFERED_SLOTS])
            offered_slots = self._serialize_offered_slots(
                shown_slots,
                doctor_names={slot.doctor_id: doctor_name for slot in shown_slots},
                specialty_name=merged_context.get("selected_specialty_name"),
                display_date=requested_date,
            ) if shown_slots else []
            return ChatReceptionistReply(
                intent=(
                    ChatReceptionistIntent.AVAILABILITY_NO_SLOTS
                    if not shown_slots
                    else ChatReceptionistIntent.AVAILABILITY_RESULTS
                ),
                content=self._format_exact_time_unavailable_with_alternatives(
                    provider_name=doctor_name,
                    parsed_date=parsed_requested_date,
                    requested_exact_time=requested_exact_time,
                    alternative_times=alternative_times[:_MAX_OFFERED_SLOTS],
                ),
                chat_context_updates={
                    **context_updates,
                    "offered_slots": offered_slots,
                    "requested_exact_time": requested_exact_time,
                    "appointment_intake_awaiting": (
                        APPOINTMENT_INTAKE_AWAITING_SLOT_SELECTION
                        if shown_slots
                        else APPOINTMENT_INTAKE_AWAITING_DATE_OR_TIME
                    ),
                },
                availability_checked=True,
                offered_slot_count=len(offered_slots),
            )

        return None

    def _handle_exact_time_specialty_availability(
        self,
        *,
        attributed_slots: Sequence[DoctorAttributedAvailabilitySlot],
        merged_context: dict[str, Any],
        context_updates: dict[str, Any],
        specialty_name: str,
        requested_date: str,
        parsed_requested_date: date,
    ) -> ChatReceptionistReply | None:
        requested_exact_time = self._requested_exact_time(merged_context)
        if requested_exact_time is None:
            return None

        exact_matches = self._filter_attributed_slots_by_exact_time(
            attributed_slots,
            requested_exact_time,
        )
        clinic_tz = self._clinic_timezone()

        if len(exact_matches) == 1:
            item = exact_matches[0]
            display_time = format_clinic_local_time_label(item.slot.start_time, clinic_tz)
            offered_slots = self._serialize_attributed_offered_slots(
                [item],
                specialty_name=specialty_name,
                display_date=requested_date,
            )
            provider_name = item.doctor_name or specialty_name
            return ChatReceptionistReply(
                intent=ChatReceptionistIntent.AVAILABILITY_RESULTS,
                content=self._format_exact_time_available_hold_prompt(
                    provider_name=provider_name,
                    parsed_date=parsed_requested_date,
                    display_time=display_time,
                ),
                chat_context_updates={
                    **context_updates,
                    "offered_slots": offered_slots,
                    "requested_exact_time": requested_exact_time,
                    "appointment_intake_awaiting": APPOINTMENT_INTAKE_AWAITING_SLOT_SELECTION,
                },
                availability_checked=True,
                offered_slot_count=1,
            )

        if not exact_matches:
            alternative_times = [
                format_clinic_local_time_label(item.slot.start_time, clinic_tz)
                for item in attributed_slots
            ]
            shown_slots = attributed_slots[:_MAX_OFFERED_SLOTS]
            offered_slots = self._serialize_attributed_offered_slots(
                shown_slots,
                specialty_name=specialty_name,
                display_date=requested_date,
            ) if shown_slots else []
            return ChatReceptionistReply(
                intent=(
                    ChatReceptionistIntent.AVAILABILITY_NO_SLOTS
                    if not shown_slots
                    else ChatReceptionistIntent.AVAILABILITY_RESULTS
                ),
                content=self._format_exact_time_unavailable_with_alternatives(
                    provider_name=specialty_name,
                    parsed_date=parsed_requested_date,
                    requested_exact_time=requested_exact_time,
                    alternative_times=alternative_times[:_MAX_OFFERED_SLOTS],
                ),
                chat_context_updates={
                    **context_updates,
                    "offered_slots": offered_slots,
                    "requested_exact_time": requested_exact_time,
                    "appointment_intake_awaiting": (
                        APPOINTMENT_INTAKE_AWAITING_SLOT_SELECTION
                        if shown_slots
                        else APPOINTMENT_INTAKE_AWAITING_DATE_OR_TIME
                    ),
                },
                availability_checked=True,
                offered_slot_count=len(offered_slots),
            )

        return None

    def _extract_offered_time(self, message: str) -> str | None:
        normalized = normalize_appointment_time_expression(message, allow_bare_hour=True)
        return normalized.value if normalized is not None else None

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
            if any(term in normalized_message for term in self._specialty_match_terms(specialty)):
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

    def _format_specialty_doctors_reply(
        self,
        doctors: Sequence[Doctor],
        *,
        specialty_name: str,
    ) -> str:
        if not doctors:
            return (
                f"We do not currently have any doctors listed for {specialty_name}. "
                "Please contact the clinic for assistance."
            )

        names = [doctor.full_name for doctor in doctors]

        if len(names) == 1:
            return (
                f"We have {names[0]} for {specialty_name}. "
                "What day or time works best?"
            )

        return (
            f"We have {self._join_names(names)} for {specialty_name}. "
            "Do you have a preferred doctor, day, or time?"
        )

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
