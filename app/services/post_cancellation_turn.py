from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from app.services.post_completion_turn_classification import (
    PostCompletionTurnDecision,
    classify_post_completion_turn,
)


class PostCancellationTurnDecision(StrEnum):
    END_CONVERSATION = "end_conversation"
    NEEDS_MORE_HELP = "needs_more_help"
    NEW_SCHEDULING_REQUEST = "new_scheduling_request"
    CANCEL_REQUEST = "cancel_request"
    RESCHEDULE_REQUEST = "reschedule_request"
    APPOINTMENT_LOOKUP_REQUEST = "appointment_lookup_request"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class PostCancellationTurnUnderstanding:
    decision: PostCancellationTurnDecision
    normalized_message: str
    reason: str


class PostCancellationTurnClassifier(Protocol):
    def classify(
        self,
        *,
        message: str,
        chat_context: dict[str, Any],
    ) -> PostCancellationTurnUnderstanding:
        raise NotImplementedError


class DeterministicPostCancellationTurnClassifier:
    """Local/test-double classifier for the post-cancellation follow-up frame."""

    def classify(
        self,
        *,
        message: str,
        chat_context: dict[str, Any],
    ) -> PostCancellationTurnUnderstanding:
        del chat_context  # reserved for future LLM/state-aware classification
        return classify_post_cancellation_turn(message=message)


def classify_post_cancellation_turn(
    *,
    message: str,
    chat_context: dict[str, Any] | None = None,
) -> PostCancellationTurnUnderstanding:
    del chat_context  # reserved for future state-aware classification
    understanding = classify_post_completion_turn(message=message)
    return PostCancellationTurnUnderstanding(
        decision=_map_completion_decision(understanding.decision),
        normalized_message=understanding.normalized_message,
        reason=understanding.reason,
    )


def _map_completion_decision(
    decision: PostCompletionTurnDecision,
) -> PostCancellationTurnDecision:
    return PostCancellationTurnDecision(decision.value)
