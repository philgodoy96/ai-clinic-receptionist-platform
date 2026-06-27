from __future__ import annotations

import pytest

from app.services.post_cancellation_turn import (
    PostCancellationTurnDecision,
    classify_post_cancellation_turn,
)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("no", PostCancellationTurnDecision.END_CONVERSATION),
        ("nah", PostCancellationTurnDecision.END_CONVERSATION),
        ("no thanks", PostCancellationTurnDecision.END_CONVERSATION),
        ("that's all", PostCancellationTurnDecision.END_CONVERSATION),
        ("nothing else", PostCancellationTurnDecision.END_CONVERSATION),
        ("I'm good", PostCancellationTurnDecision.END_CONVERSATION),
        ("yes", PostCancellationTurnDecision.NEEDS_MORE_HELP),
        ("yes please", PostCancellationTurnDecision.NEEDS_MORE_HELP),
        ("I need help", PostCancellationTurnDecision.NEEDS_MORE_HELP),
        ("actually yes", PostCancellationTurnDecision.NEEDS_MORE_HELP),
        (
            "I want to schedule an appointment",
            PostCancellationTurnDecision.NEW_SCHEDULING_REQUEST,
        ),
        (
            "I need another appointment",
            PostCancellationTurnDecision.NEW_SCHEDULING_REQUEST,
        ),
        (
            "I want to cancel another appointment",
            PostCancellationTurnDecision.CANCEL_REQUEST,
        ),
        ("I need to reschedule", PostCancellationTurnDecision.RESCHEDULE_REQUEST),
        ("Can you tell me a joke?", PostCancellationTurnDecision.UNKNOWN),
    ],
)
def test_classify_post_cancellation_turn(
    message: str,
    expected: PostCancellationTurnDecision,
) -> None:
    understanding = classify_post_cancellation_turn(message=message)

    assert understanding.decision is expected


def test_classify_post_cancellation_turn_prioritizes_actionable_request_over_closing() -> (
    None
):
    understanding = classify_post_cancellation_turn(
        message="no, I want to schedule another appointment",
    )

    assert understanding.decision is PostCancellationTurnDecision.NEW_SCHEDULING_REQUEST
