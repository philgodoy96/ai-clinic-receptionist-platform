from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from app.services.post_completion_turn_classification import (
    PostCompletionTurnDecision,
    classify_post_completion_turn,
)


class PostBookingTurnDecision(StrEnum):
    END_CONVERSATION = "end_conversation"
    NEEDS_MORE_HELP = "needs_more_help"
    NEW_SCHEDULING_REQUEST = "new_scheduling_request"
    CANCEL_REQUEST = "cancel_request"
    RESCHEDULE_REQUEST = "reschedule_request"
    APPOINTMENT_LOOKUP_REQUEST = "appointment_lookup_request"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class PostBookingTurnUnderstanding:
    decision: PostBookingTurnDecision
    normalized_message: str
    reason: str


class PostBookingTurnClassifier(Protocol):
    def classify(
        self,
        *,
        message: str,
        chat_context: dict[str, Any],
    ) -> PostBookingTurnUnderstanding:
        raise NotImplementedError


class DeterministicPostBookingTurnClassifier:
    """Local/test-double classifier for the post-booking task frame."""

    def classify(
        self,
        *,
        message: str,
        chat_context: dict[str, Any],
    ) -> PostBookingTurnUnderstanding:
        del chat_context  # reserved for future LLM/state-aware classification
        return classify_post_booking_turn(message=message)


def classify_post_booking_turn(*, message: str) -> PostBookingTurnUnderstanding:
    """Classify a user turn within the post-booking follow-up frame."""
    understanding = classify_post_completion_turn(message=message)
    return PostBookingTurnUnderstanding(
        decision=_map_completion_decision(understanding.decision),
        normalized_message=understanding.normalized_message,
        reason=understanding.reason,
    )


def _map_completion_decision(
    decision: PostCompletionTurnDecision,
) -> PostBookingTurnDecision:
    return PostBookingTurnDecision(decision.value)
