from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from app.services.post_completion_turn_classification import (
    PostCompletionTurnDecision,
    classify_post_completion_turn,
)


class PostRescheduleTurnDecision(StrEnum):
    END_CONVERSATION = "end_conversation"
    NEEDS_MORE_HELP = "needs_more_help"
    NEW_SCHEDULING_REQUEST = "new_scheduling_request"
    CANCEL_REQUEST = "cancel_request"
    RESCHEDULE_REQUEST = "reschedule_request"
    APPOINTMENT_LOOKUP_REQUEST = "appointment_lookup_request"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class PostRescheduleTurnUnderstanding:
    decision: PostRescheduleTurnDecision
    normalized_message: str
    reason: str


class PostRescheduleTurnClassifier(Protocol):
    def classify(
        self,
        *,
        message: str,
        chat_context: dict[str, Any],
    ) -> PostRescheduleTurnUnderstanding:
        raise NotImplementedError


class DeterministicPostRescheduleTurnClassifier:
    """Local/test-double classifier for the post-reschedule follow-up frame."""

    def classify(
        self,
        *,
        message: str,
        chat_context: dict[str, Any],
    ) -> PostRescheduleTurnUnderstanding:
        del chat_context  # reserved for future LLM/state-aware classification
        return classify_post_reschedule_turn(message=message)


def classify_post_reschedule_turn(
    *,
    message: str,
    chat_context: dict[str, Any] | None = None,
) -> PostRescheduleTurnUnderstanding:
    del chat_context  # reserved for future state-aware classification
    understanding = classify_post_completion_turn(message=message)
    return PostRescheduleTurnUnderstanding(
        decision=_map_completion_decision(understanding.decision),
        normalized_message=understanding.normalized_message,
        reason=understanding.reason,
    )


def _map_completion_decision(
    decision: PostCompletionTurnDecision,
) -> PostRescheduleTurnDecision:
    return PostRescheduleTurnDecision(decision.value)
