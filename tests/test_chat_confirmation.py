from __future__ import annotations

import pytest

from app.services.chat_confirmation import (
    ConfirmationDecision,
    ConfirmationType,
    is_confirmation_confirmed,
    normalize_email_address,
    normalize_patient_display_name,
    understand_confirmation,
)


@pytest.mark.parametrize(
    ("message", "confirmation_type"),
    [
        ("Yes", ConfirmationType.EMAIL_CONFIRMATION),
        ("yes.", ConfirmationType.EMAIL_CONFIRMATION),
        ("Correct", ConfirmationType.EMAIL_CONFIRMATION),
        ("Yes", ConfirmationType.FINAL_BOOKING_CONFIRMATION),
        ("yes please", ConfirmationType.FINAL_BOOKING_CONFIRMATION),
        ("sure", ConfirmationType.FINAL_BOOKING_CONFIRMATION),
        ("Looks good", ConfirmationType.FINAL_BOOKING_CONFIRMATION),
        ("That's right", ConfirmationType.POSSIBLE_PATIENT_MATCH_CONFIRMATION),
    ],
)
def test_confirmation_phrases_are_confirmed(
    message: str,
    confirmation_type: ConfirmationType,
) -> None:
    result = understand_confirmation(
        confirmation_type=confirmation_type,
        message=message,
    )
    assert result.decision is ConfirmationDecision.CONFIRMED


@pytest.mark.parametrize(
    ("message", "confirmation_type"),
    [
        ("No", ConfirmationType.EMAIL_CONFIRMATION),
        ("No", ConfirmationType.FINAL_BOOKING_CONFIRMATION),
    ],
)
def test_confirmation_phrases_are_rejected(
    message: str,
    confirmation_type: ConfirmationType,
) -> None:
    result = understand_confirmation(
        confirmation_type=confirmation_type,
        message=message,
    )
    assert result.decision is ConfirmationDecision.REJECTED


def test_change_the_time_is_wants_change() -> None:
    result = understand_confirmation(
        confirmation_type=ConfirmationType.FINAL_BOOKING_CONFIRMATION,
        message="Change the time",
    )
    assert result.decision is ConfirmationDecision.WANTS_CHANGE


def test_maybe_is_unclear() -> None:
    result = understand_confirmation(
        confirmation_type=ConfirmationType.FINAL_BOOKING_CONFIRMATION,
        message="Maybe",
    )
    assert result.decision is ConfirmationDecision.UNCLEAR


def test_yes_does_not_globally_book_outside_final_state() -> None:
    assert not is_confirmation_confirmed(
        confirmation_type=ConfirmationType.FINAL_BOOKING_CONFIRMATION,
        message="I would like to book an appointment",
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Jane Doe.", "Jane Doe"),
        ("Jane Doe,", "Jane Doe"),
        ("  Jane   Doe  ", "Jane Doe"),
    ],
)
def test_normalize_patient_display_name(raw: str, expected: str) -> None:
    assert normalize_patient_display_name(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("jane.doe@example.com.", "jane.doe@example.com"),
        (" jane.doe@example.com ", "jane.doe@example.com"),
    ],
)
def test_normalize_email_address(raw: str, expected: str) -> None:
    assert normalize_email_address(raw) == expected
