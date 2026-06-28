from __future__ import annotations

import pytest

from app.services.post_reschedule_turn import (
    PostRescheduleTurnDecision,
    classify_post_reschedule_turn,
)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("no", PostRescheduleTurnDecision.END_CONVERSATION),
        ("no thanks", PostRescheduleTurnDecision.END_CONVERSATION),
        ("that's all", PostRescheduleTurnDecision.END_CONVERSATION),
        ("nothing else", PostRescheduleTurnDecision.END_CONVERSATION),
        ("I'm good", PostRescheduleTurnDecision.END_CONVERSATION),
        ("yes", PostRescheduleTurnDecision.NEEDS_MORE_HELP),
        ("yes please", PostRescheduleTurnDecision.NEEDS_MORE_HELP),
        ("I need help", PostRescheduleTurnDecision.NEEDS_MORE_HELP),
        (
            "I want to schedule an appointment",
            PostRescheduleTurnDecision.NEW_SCHEDULING_REQUEST,
        ),
        (
            "I want to cancel an appointment",
            PostRescheduleTurnDecision.CANCEL_REQUEST,
        ),
        (
            "I need to reschedule another appointment",
            PostRescheduleTurnDecision.RESCHEDULE_REQUEST,
        ),
        ("Can you tell me a joke?", PostRescheduleTurnDecision.UNKNOWN),
    ],
)
def test_classify_post_reschedule_turn(
    message: str,
    expected: PostRescheduleTurnDecision,
) -> None:
    understanding = classify_post_reschedule_turn(message=message)

    assert understanding.decision is expected
