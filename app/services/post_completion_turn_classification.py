from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

_TRAILING_PUNCTUATION = re.compile(r"^[.,!?;:]+|[.,!?;:]+$")

_CANCEL_SIGNALS = (
    "cancel",
    "cancellation",
)

_RESCHEDULE_SIGNALS = (
    "reschedule",
    "move appointment",
    "move my appointment",
)

_NEW_SCHEDULING_SIGNALS = (
    "another appointment",
    "another one",
    "book another",
    "schedule another",
    "need another",
    "want another",
    "one more appointment",
    "extra appointment",
    "want to schedule an appointment",
    "schedule an appointment",
    "book an appointment",
    "need to schedule an appointment",
    "want to book an appointment",
    "i want to schedule an appointment",
    "i need to schedule an appointment",
    "i want to book an appointment",
)

_END_CONVERSATION_PHRASES = (
    "no",
    "nope",
    "nah",
    "no thanks",
    "no thank you",
    "nothing else",
    "nothing further",
    "that's all",
    "that is all",
    "that's it",
    "that is it",
    "that's everything",
    "i'm good",
    "i am good",
    "im good",
    "all good",
    "all set",
    "we're good",
    "we are good",
    "i'm all set",
    "im all set",
    "i'm done",
    "im done",
    "that will be all",
    "thanks that's all",
)

_NEEDS_MORE_HELP_PHRASES = (
    "yes",
    "yeah",
    "yep",
    "yes please",
    "sure",
    "i need help",
    "actually yes",
)

_SCHEDULING_KEYWORDS = (
    "appointment",
    "schedule",
    "book",
)


class PostCompletionTurnDecision(StrEnum):
    END_CONVERSATION = "end_conversation"
    NEEDS_MORE_HELP = "needs_more_help"
    NEW_SCHEDULING_REQUEST = "new_scheduling_request"
    CANCEL_REQUEST = "cancel_request"
    RESCHEDULE_REQUEST = "reschedule_request"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class PostCompletionTurnUnderstanding:
    decision: PostCompletionTurnDecision
    normalized_message: str
    reason: str


def classify_post_completion_turn(*, message: str) -> PostCompletionTurnUnderstanding:
    """Classify a user turn after a completed booking or cancellation action."""
    normalized = _normalize_message(message)

    if not normalized:
        return PostCompletionTurnUnderstanding(
            decision=PostCompletionTurnDecision.UNKNOWN,
            normalized_message=normalized,
            reason="empty_message",
        )

    cancel_signal = _match_signal(normalized, _CANCEL_SIGNALS)
    if cancel_signal is not None:
        return PostCompletionTurnUnderstanding(
            decision=PostCompletionTurnDecision.CANCEL_REQUEST,
            normalized_message=normalized,
            reason=f"matched_cancel_signal:{cancel_signal}",
        )

    reschedule_signal = _match_signal(normalized, _RESCHEDULE_SIGNALS)
    if reschedule_signal is not None:
        return PostCompletionTurnUnderstanding(
            decision=PostCompletionTurnDecision.RESCHEDULE_REQUEST,
            normalized_message=normalized,
            reason=f"matched_reschedule_signal:{reschedule_signal}",
        )

    new_scheduling_signal = _match_new_scheduling_intent(normalized)
    if new_scheduling_signal is not None:
        return PostCompletionTurnUnderstanding(
            decision=PostCompletionTurnDecision.NEW_SCHEDULING_REQUEST,
            normalized_message=normalized,
            reason=f"matched_new_scheduling:{new_scheduling_signal}",
        )

    end_phrase = _match_phrase_list(normalized, _END_CONVERSATION_PHRASES)
    if end_phrase is not None:
        return PostCompletionTurnUnderstanding(
            decision=PostCompletionTurnDecision.END_CONVERSATION,
            normalized_message=normalized,
            reason=f"matched_end_conversation:{end_phrase}",
        )

    needs_help_phrase = _match_phrase_list(normalized, _NEEDS_MORE_HELP_PHRASES)
    if needs_help_phrase is not None:
        return PostCompletionTurnUnderstanding(
            decision=PostCompletionTurnDecision.NEEDS_MORE_HELP,
            normalized_message=normalized,
            reason=f"matched_needs_more_help:{needs_help_phrase}",
        )

    return PostCompletionTurnUnderstanding(
        decision=PostCompletionTurnDecision.UNKNOWN,
        normalized_message=normalized,
        reason="no_matching_intent",
    )


def _normalize_message(message: str) -> str:
    collapsed = re.sub(r"\s+", " ", message.strip().lower().replace("\u2019", "'"))
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


def _match_signal(normalized: str, signals: tuple[str, ...]) -> str | None:
    for signal in signals:
        if signal in normalized:
            return signal
    return None


def _match_new_scheduling_intent(normalized: str) -> str | None:
    explicit = _match_phrase_list(normalized, _NEW_SCHEDULING_SIGNALS)
    if explicit is not None:
        return explicit

    if not _match_signal(normalized, _SCHEDULING_KEYWORDS):
        return None

    if "another" in normalized or "one more" in normalized or "extra" in normalized:
        return "scheduling_keyword_with_follow_up"

    return None
