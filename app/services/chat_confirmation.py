from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

_CONFIRMED_PHRASES = (
    "yes",
    "yeah",
    "yep",
    "sure",
    "correct",
    "that's correct",
    "that is correct",
    "that's right",
    "that is right",
    "yes please",
    "i confirm",
    "confirm",
    "go ahead",
    "looks good",
    "sounds good",
    "book it",
    "please book it",
    "yes please book it",
)

_REJECTED_PHRASES = (
    "no",
    "nope",
    "no thanks",
    "not correct",
    "that's wrong",
    "that is wrong",
    "incorrect",
    "do not",
    "don't",
)

_WANTS_CHANGE_PHRASES = (
    "change",
    "change it",
    "different time",
    "another time",
    "different email",
    "wrong email",
    "wait",
    "hold on",
    "not that time",
)

_TRAILING_PUNCTUATION = re.compile(r"^[.,!?;:]+|[.,!?;:]+$")


class ConfirmationType(StrEnum):
    EMAIL_CONFIRMATION = "email_confirmation"
    FINAL_BOOKING_CONFIRMATION = "final_booking_confirmation"
    POSSIBLE_PATIENT_MATCH_CONFIRMATION = "possible_patient_match_confirmation"


class ConfirmationDecision(StrEnum):
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    WANTS_CHANGE = "wants_change"
    UNCLEAR = "unclear"


@dataclass(frozen=True, slots=True)
class ConfirmationUnderstanding:
    decision: ConfirmationDecision
    normalized_confirmation_text: str
    reason: str


def understand_confirmation(
    *,
    confirmation_type: ConfirmationType,
    message: str,
) -> ConfirmationUnderstanding:
    del confirmation_type  # state-aware via caller; same phrases apply per type for now
    normalized = _normalize_message(message)

    if not normalized:
        return ConfirmationUnderstanding(
            decision=ConfirmationDecision.UNCLEAR,
            normalized_confirmation_text=normalized,
            reason="empty_message",
        )

    rejected = _match_phrase_list(normalized, _REJECTED_PHRASES)
    if rejected is not None:
        return ConfirmationUnderstanding(
            decision=ConfirmationDecision.REJECTED,
            normalized_confirmation_text=normalized,
            reason=f"matched_rejection:{rejected}",
        )

    wants_change = _match_phrase_list(normalized, _WANTS_CHANGE_PHRASES)
    if wants_change is not None:
        return ConfirmationUnderstanding(
            decision=ConfirmationDecision.WANTS_CHANGE,
            normalized_confirmation_text=normalized,
            reason=f"matched_change_request:{wants_change}",
        )

    confirmed = _match_phrase_list(normalized, _CONFIRMED_PHRASES)
    if confirmed is not None:
        return ConfirmationUnderstanding(
            decision=ConfirmationDecision.CONFIRMED,
            normalized_confirmation_text=normalized,
            reason=f"matched_confirmation:{confirmed}",
        )

    return ConfirmationUnderstanding(
        decision=ConfirmationDecision.UNCLEAR,
        normalized_confirmation_text=normalized,
        reason="no_matching_phrase",
    )


def is_confirmation_confirmed(
    *,
    confirmation_type: ConfirmationType,
    message: str,
) -> bool:
    return (
        understand_confirmation(confirmation_type=confirmation_type, message=message).decision
        is ConfirmationDecision.CONFIRMED
    )


def is_confirmation_rejected(
    *,
    confirmation_type: ConfirmationType,
    message: str,
) -> bool:
    return (
        understand_confirmation(confirmation_type=confirmation_type, message=message).decision
        is ConfirmationDecision.REJECTED
    )


def normalize_patient_display_name(value: str) -> str:
    collapsed = re.sub(r"\s+", " ", value.strip())
    if not collapsed:
        return collapsed

    return _TRAILING_PUNCTUATION.sub("", collapsed).strip()


def normalize_email_address(value: str) -> str:
    collapsed = value.strip()
    if not collapsed:
        return collapsed

    return _TRAILING_PUNCTUATION.sub("", collapsed).strip().lower()


def _normalize_message(message: str) -> str:
    collapsed = re.sub(r"\s+", " ", message.strip().lower())
    if not collapsed:
        return collapsed

    return _TRAILING_PUNCTUATION.sub("", collapsed).strip()


def _match_phrase_list(normalized: str, phrases: tuple[str, ...]) -> str | None:
    ordered = sorted(phrases, key=len, reverse=True)
    for phrase in ordered:
        if normalized == phrase:
            return phrase
        if normalized.startswith(f"{phrase} "):
            return phrase
        if normalized.startswith(f"{phrase},"):
            return phrase
        if normalized.startswith(f"{phrase}."):
            return phrase
        if normalized.startswith(f"{phrase}!"):
            return phrase
    return None
