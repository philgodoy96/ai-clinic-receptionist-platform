from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

from app.services.chat_appointment_cancellation import (
    APPOINTMENT_MANAGEMENT_AWAITING_APPOINTMENT_SELECTION,
    APPOINTMENT_MANAGEMENT_AWAITING_CANCELLATION_CONFIRMATION,
    APPOINTMENT_MANAGEMENT_AWAITING_PATIENT_IDENTITY,
    APPOINTMENT_MANAGEMENT_MODE_CANCEL,
)
from app.services.chat_appointment_lookup import (
    APPOINTMENT_MANAGEMENT_MODE_LOOKUP,
    is_appointment_lookup_message,
)
from app.services.chat_appointment_rescheduling import (
    APPOINTMENT_MANAGEMENT_AWAITING_NEW_SLOT_SELECTION,
    APPOINTMENT_MANAGEMENT_AWAITING_NEW_TIME_PREFERENCE,
    APPOINTMENT_MANAGEMENT_AWAITING_RESCHEDULE_CONFIRMATION,
    APPOINTMENT_MANAGEMENT_MODE_RESCHEDULE,
)
from app.services.chat_booking_identity import ParsedPatientFields
from app.services.chat_confirmation import (
    ConfirmationDecision,
    ConfirmationType,
    understand_confirmation,
)
from app.services.post_completion_turn_classification import _NEW_SCHEDULING_SIGNALS

PENDING_INTENT_SWITCH_KEY = "pending_intent_switch"

_OPTION_NUMBER_PATTERN = re.compile(r"^option\s+\d+$", re.IGNORECASE)
_BARE_INTEGER_PATTERN = re.compile(r"^\d{1,2}$")
_TIME_PATTERN = re.compile(r"\b(\d{1,2}):(\d{2})\b")
_EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE_PATTERN = re.compile(r"(?:\+?\d[\d\s\-().]{6,}\d|\b\d{10,14}\b)")

_CANCEL_KEYWORDS = ("cancel", "cancellation")
_RESCHEDULE_KEYWORDS = (
    "reschedule",
    "move appointment",
    "move my appointment",
    "move an appointment",
)

_UNCERTAINTY_MARKERS = (
    "maybe",
    "might",
    "perhaps",
    "i don't know",
    "i dont know",
    "not sure",
    "i'm not sure",
    "im not sure",
)

_CLEAR_OVERRIDE_MARKERS = (
    "actually",
    "instead",
    "on second thought",
    "never mind",
    "nevermind",
)

_PENDING_SWITCH_CONFIRM_PHRASES = (
    "yes",
    "yeah",
    "yep",
    "correct",
    "that's right",
    "that is right",
    "thats right",
    "sure",
    "yes please",
    "go ahead",
    "please do",
    "do it",
)

_PENDING_SWITCH_REJECT_PHRASES = (
    "no",
    "nope",
    "no thanks",
    "no thank you",
    "keep going",
    "continue",
    "keep rescheduling",
    "continue rescheduling",
    "stay",
)

_FLOW_ACTION_LABELS = {
    APPOINTMENT_MANAGEMENT_MODE_CANCEL: "cancelling",
    APPOINTMENT_MANAGEMENT_MODE_RESCHEDULE: "rescheduling",
    APPOINTMENT_MANAGEMENT_MODE_LOOKUP: "looking up your appointments",
    "booking": "booking",
}

_TARGET_START_LABELS = {
    "booking": "booking a new appointment",
    "cancel": "cancelling your appointment",
    "reschedule": "rescheduling your appointment",
    "lookup": "listing your appointments",
}

_SWITCH_ACK_PREFIX = {
    "booking": "Sure — let's schedule a new appointment. ",
    "cancel": "Okay — I'll help you cancel instead. ",
    "reschedule": "Okay — I'll help you reschedule instead. ",
    "lookup": "Sure — I can list your appointments. ",
}


class IntentSwitchTarget(StrEnum):
    BOOKING = "booking"
    CANCEL = "cancel"
    RESCHEDULE = "reschedule"
    LOOKUP = "lookup"


IntentOverrideKind = Literal["clear", "uncertain", "none"]


@dataclass(frozen=True, slots=True)
class IntentOverrideDetection:
    kind: IntentOverrideKind
    target: IntentSwitchTarget | None = None
    source_message: str | None = None


@dataclass(frozen=True, slots=True)
class PendingIntentSwitchReply:
    decision: Literal["confirmed", "rejected", "unclear"]
    normalized_message: str


def is_in_switchable_active_flow(
    chat_context: dict[str, Any],
    *,
    booking_identity_active: bool,
) -> bool:
    mode = chat_context.get("appointment_management_mode")
    awaiting = chat_context.get("appointment_management_awaiting")
    if mode in {
        APPOINTMENT_MANAGEMENT_MODE_CANCEL,
        APPOINTMENT_MANAGEMENT_MODE_RESCHEDULE,
        APPOINTMENT_MANAGEMENT_MODE_LOOKUP,
    } and awaiting not in {None, "completed"}:
        return True
    if booking_identity_active and chat_context.get("hold_id"):
        return True
    return False


def current_flow_name(chat_context: dict[str, Any], *, booking_identity_active: bool) -> str:
    mode = chat_context.get("appointment_management_mode")
    if isinstance(mode, str) and mode:
        return mode
    if booking_identity_active:
        return "booking"
    return "unknown"


def current_flow_target(
    chat_context: dict[str, Any],
    *,
    booking_identity_active: bool,
) -> IntentSwitchTarget | None:
    mode = chat_context.get("appointment_management_mode")
    if mode == APPOINTMENT_MANAGEMENT_MODE_CANCEL:
        return IntentSwitchTarget.CANCEL
    if mode == APPOINTMENT_MANAGEMENT_MODE_RESCHEDULE:
        return IntentSwitchTarget.RESCHEDULE
    if mode == APPOINTMENT_MANAGEMENT_MODE_LOOKUP:
        return IntentSwitchTarget.LOOKUP
    if booking_identity_active:
        return IntentSwitchTarget.BOOKING
    return None


def detect_intent_override(
    message: str,
    chat_context: dict[str, Any],
    *,
    parse_patient_fields: Callable[[str], ParsedPatientFields],
    booking_identity_active: bool,
) -> IntentOverrideDetection:
    if chat_context.get(PENDING_INTENT_SWITCH_KEY):
        return IntentOverrideDetection(kind="none")

    if not is_in_switchable_active_flow(
        chat_context,
        booking_identity_active=booking_identity_active,
    ):
        return IntentOverrideDetection(kind="none")

    if is_expected_in_flow_input(
        message,
        chat_context,
        parse_patient_fields=parse_patient_fields,
    ):
        return IntentOverrideDetection(kind="none")

    normalized = _normalize_message(message)
    target = _detect_switch_target(normalized)
    if target is None:
        return IntentOverrideDetection(kind="none")

    current = current_flow_target(
        chat_context,
        booking_identity_active=booking_identity_active,
    )
    if target == current:
        return IntentOverrideDetection(kind="none")

    if _message_has_uncertainty(normalized):
        return IntentOverrideDetection(
            kind="uncertain",
            target=target,
            source_message=message,
        )

    if _message_has_clear_override(normalized) or _is_high_confidence_switch(normalized, target):
        return IntentOverrideDetection(
            kind="clear",
            target=target,
            source_message=message,
        )

    return IntentOverrideDetection(kind="none")


def is_expected_in_flow_input(
    message: str,
    chat_context: dict[str, Any],
    *,
    parse_patient_fields: Callable[[str], ParsedPatientFields],
) -> bool:
    awaiting = chat_context.get("appointment_management_awaiting")
    if not isinstance(awaiting, str):
        return False

    normalized = _normalize_message(message)
    stripped = message.strip()

    if awaiting == APPOINTMENT_MANAGEMENT_AWAITING_PATIENT_IDENTITY:
        parsed = parse_patient_fields(message)
        if any((parsed.full_name, parsed.date_of_birth, parsed.email, parsed.phone)):
            return True
        if _EMAIL_PATTERN.search(message) or _PHONE_PATTERN.search(message):
            return True
        return False

    if awaiting == APPOINTMENT_MANAGEMENT_AWAITING_APPOINTMENT_SELECTION:
        if _OPTION_NUMBER_PATTERN.match(stripped):
            return True
        if _BARE_INTEGER_PATTERN.fullmatch(stripped):
            offered = chat_context.get("offered_appointments")
            if isinstance(offered, list) and offered:
                number = int(stripped)
                if 1 <= number <= len(offered):
                    return True
        return False

    if awaiting == APPOINTMENT_MANAGEMENT_AWAITING_CANCELLATION_CONFIRMATION:
        decision = understand_confirmation(
            confirmation_type=ConfirmationType.CANCELLATION_CONFIRMATION,
            message=message,
        ).decision
        return decision is not ConfirmationDecision.UNCLEAR

    if awaiting == APPOINTMENT_MANAGEMENT_AWAITING_RESCHEDULE_CONFIRMATION:
        decision = understand_confirmation(
            confirmation_type=ConfirmationType.RESCHEDULE_CONFIRMATION,
            message=message,
        ).decision
        return decision is not ConfirmationDecision.UNCLEAR

    if awaiting in {
        APPOINTMENT_MANAGEMENT_AWAITING_NEW_TIME_PREFERENCE,
        APPOINTMENT_MANAGEMENT_AWAITING_NEW_SLOT_SELECTION,
    }:
        if _OPTION_NUMBER_PATTERN.match(stripped):
            return True
        if _BARE_INTEGER_PATTERN.fullmatch(stripped):
            offered_slots = chat_context.get("offered_slots")
            if isinstance(offered_slots, list) and offered_slots:
                number = int(stripped)
                if 1 <= number <= len(offered_slots):
                    return True
        if _TIME_PATTERN.search(message):
            return True
        if any(
            token in normalized
            for token in (
                "monday",
                "tuesday",
                "wednesday",
                "thursday",
                "friday",
                "saturday",
                "sunday",
                "tomorrow",
                "morning",
                "afternoon",
                "evening",
            )
        ):
            return True
        return False

    booking_step = chat_context.get("booking_identity_step")
    if isinstance(booking_step, str) and booking_step:
        parsed = parse_patient_fields(message)
        if any((parsed.full_name, parsed.date_of_birth, parsed.email, parsed.phone)):
            return True
        decision = understand_confirmation(
            confirmation_type=ConfirmationType.EMAIL_CONFIRMATION,
            message=message,
        ).decision
        if decision is not ConfirmationDecision.UNCLEAR:
            return True
        if normalized in {"yes", "no", "yeah", "yep", "nope"}:
            return True

    return False


def build_pending_intent_switch_context(
    *,
    from_flow: str,
    target: IntentSwitchTarget,
    original_message: str,
    previous_awaiting: str | None,
) -> dict[str, Any]:
    return {
        "from_flow": from_flow,
        "to_intent": target.value,
        "original_message": original_message,
        "previous_awaiting": previous_awaiting,
        "clarification_retries": 0,
    }


def build_intent_switch_confirmation_message(
    *,
    from_flow: str,
    target: IntentSwitchTarget,
) -> str:
    from_label = _FLOW_ACTION_LABELS.get(from_flow, from_flow)
    to_label = _TARGET_START_LABELS[target.value]
    return f"Do you want me to stop {from_label} and start {to_label} instead?"


def build_intent_switch_acknowledgment(target: IntentSwitchTarget) -> str:
    return _SWITCH_ACK_PREFIX[target.value]


def classify_pending_intent_switch_reply(message: str) -> PendingIntentSwitchReply:
    normalized = _normalize_message(message)
    if not normalized:
        return PendingIntentSwitchReply(decision="unclear", normalized_message=normalized)

    for phrase in _PENDING_SWITCH_REJECT_PHRASES:
        if normalized == phrase or normalized.startswith(f"{phrase} "):
            return PendingIntentSwitchReply(decision="rejected", normalized_message=normalized)

    for phrase in _PENDING_SWITCH_CONFIRM_PHRASES:
        if normalized == phrase or normalized.startswith(f"{phrase} "):
            return PendingIntentSwitchReply(decision="confirmed", normalized_message=normalized)

    return PendingIntentSwitchReply(decision="unclear", normalized_message=normalized)


def _normalize_message(message: str) -> str:
    return re.sub(r"\s+", " ", message.strip().lower())


def _message_has_uncertainty(normalized: str) -> bool:
    return any(marker in normalized for marker in _UNCERTAINTY_MARKERS)


def _message_has_clear_override(normalized: str) -> bool:
    return any(marker in normalized for marker in _CLEAR_OVERRIDE_MARKERS)


def _is_high_confidence_switch(normalized: str, target: IntentSwitchTarget) -> bool:
    if target is IntentSwitchTarget.BOOKING:
        return any(signal in normalized for signal in _NEW_SCHEDULING_SIGNALS) or (
            ("book" in normalized or "schedule" in normalized)
            and "appointment" in normalized
        )
    if target is IntentSwitchTarget.CANCEL:
        return any(keyword in normalized for keyword in _CANCEL_KEYWORDS)
    if target is IntentSwitchTarget.RESCHEDULE:
        return any(keyword in normalized for keyword in _RESCHEDULE_KEYWORDS)
    if target is IntentSwitchTarget.LOOKUP:
        return is_appointment_lookup_message(normalized)
    return False


def _detect_switch_target(normalized: str) -> IntentSwitchTarget | None:
    if is_appointment_lookup_message(normalized):
        return IntentSwitchTarget.LOOKUP
    if any(keyword in normalized for keyword in _RESCHEDULE_KEYWORDS):
        return IntentSwitchTarget.RESCHEDULE
    if any(keyword in normalized for keyword in _CANCEL_KEYWORDS):
        return IntentSwitchTarget.CANCEL
    if any(signal in normalized for signal in _NEW_SCHEDULING_SIGNALS):
        return IntentSwitchTarget.BOOKING
    if ("book" in normalized or "schedule" in normalized) and (
        "appointment" in normalized or "another" in normalized or "new" in normalized
    ):
        return IntentSwitchTarget.BOOKING
    return None
