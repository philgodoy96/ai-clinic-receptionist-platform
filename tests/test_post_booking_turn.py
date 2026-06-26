from __future__ import annotations

import pytest

from app.services.post_booking_turn import (
    PostBookingTurnDecision,
    classify_post_booking_turn,
)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("no", PostBookingTurnDecision.END_CONVERSATION),
        ("nah", PostBookingTurnDecision.END_CONVERSATION),
        ("that's all", PostBookingTurnDecision.END_CONVERSATION),
        ("I'm good", PostBookingTurnDecision.END_CONVERSATION),
        ("era isso", PostBookingTurnDecision.END_CONVERSATION),
        ("yes", PostBookingTurnDecision.NEEDS_MORE_HELP),
        ("yes please", PostBookingTurnDecision.NEEDS_MORE_HELP),
        ("I need help", PostBookingTurnDecision.NEEDS_MORE_HELP),
        ("actually yes", PostBookingTurnDecision.NEEDS_MORE_HELP),
        (
            "I need another appointment",
            PostBookingTurnDecision.NEW_SCHEDULING_REQUEST,
        ),
        (
            "I want to schedule another appointment",
            PostBookingTurnDecision.NEW_SCHEDULING_REQUEST,
        ),
        ("Can I book another one?", PostBookingTurnDecision.NEW_SCHEDULING_REQUEST),
        ("I want to cancel", PostBookingTurnDecision.CANCEL_REQUEST),
        (
            "I need to cancel an appointment",
            PostBookingTurnDecision.CANCEL_REQUEST,
        ),
        ("I need to reschedule", PostBookingTurnDecision.RESCHEDULE_REQUEST),
        (
            "Can I move my appointment?",
            PostBookingTurnDecision.RESCHEDULE_REQUEST,
        ),
        ("Can you tell me a joke?", PostBookingTurnDecision.UNKNOWN),
    ],
)
def test_classify_post_booking_turn(message: str, expected: PostBookingTurnDecision) -> None:
    understanding = classify_post_booking_turn(message=message)

    assert understanding.decision is expected


def test_classify_post_booking_turn_prioritizes_actionable_request_over_closing() -> None:
    understanding = classify_post_booking_turn(
        message="no, I want to schedule another appointment",
    )

    assert understanding.decision is PostBookingTurnDecision.NEW_SCHEDULING_REQUEST
