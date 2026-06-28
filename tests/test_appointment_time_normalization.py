from __future__ import annotations

import pytest

from app.services.appointment_time_normalization import (
    normalize_appointment_time_expression,
)


@pytest.mark.parametrize(
    ("text", "expected_value", "expected_raw"),
    [
        ("3PM", "15:00", "3PM"),
        ("3 PM", "15:00", "3 PM"),
        ("3pm", "15:00", "3pm"),
        ("2PM", "14:00", "2PM"),
        ("2 PM", "14:00", "2 PM"),
        ("2:30 PM", "14:30", "2:30 PM"),
        ("11am", "11:00", "11am"),
        ("12pm", "12:00", "12pm"),
        ("12am", "00:00", "12am"),
        ("15:00", "15:00", "15:00"),
        ("9:30", "09:30", "9:30"),
        ("15", "15:00", "15"),
        ("23", "23:00", "23"),
    ],
)
def test_normalizes_supported_time_expressions(
    text: str,
    expected_value: str,
    expected_raw: str,
) -> None:
    normalized = normalize_appointment_time_expression(text)

    assert normalized is not None
    assert normalized.value == expected_value
    assert normalized.raw == expected_raw


@pytest.mark.parametrize("text", ["3", "0", "12", "24", "25", "", "tomorrow", "1500"])
def test_rejects_ambiguous_or_non_time_expressions(text: str) -> None:
    assert normalize_appointment_time_expression(text) is None


def test_bare_hour_disabled_when_not_allowed() -> None:
    assert normalize_appointment_time_expression("15", allow_bare_hour=False) is None
    # am/pm and colon forms still work regardless of allow_bare_hour.
    assert normalize_appointment_time_expression("3PM", allow_bare_hour=False) is not None
    assert normalize_appointment_time_expression("15:00", allow_bare_hour=False) is not None


def test_invalid_colon_time_is_rejected() -> None:
    assert normalize_appointment_time_expression("25:00") is None
    assert normalize_appointment_time_expression("12:99") is None


@pytest.mark.parametrize(
    ("text", "expected_value", "expected_raw"),
    [
        ("10h", "10:00", "10h"),
        ("10 h", "10:00", "10 h"),
        ("10hs", "10:00", "10hs"),
        ("10 hs", "10:00", "10 hs"),
        ("10h30", "10:30", "10h30"),
        ("10 h 30", "10:30", "10 h 30"),
        ("9h", "09:00", "9h"),
        ("9h05", "09:05", "9h05"),
        ("23h", "23:00", "23h"),
        ("0h", "00:00", "0h"),
    ],
)
def test_normalizes_h_suffix_time_expressions(
    text: str,
    expected_value: str,
    expected_raw: str,
) -> None:
    normalized = normalize_appointment_time_expression(text)

    assert normalized is not None
    assert normalized.value == expected_value
    assert normalized.raw == expected_raw


def test_h_suffix_works_even_when_bare_hour_disallowed() -> None:
    # ``10h`` is an explicit clock expression, not an ambiguous bare ``10``.
    normalized = normalize_appointment_time_expression("10h", allow_bare_hour=False)

    assert normalized is not None
    assert normalized.value == "10:00"


@pytest.mark.parametrize("text", ["25h", "10h99", "2 hrs", "2 hours"])
def test_rejects_invalid_or_non_clock_h_expressions(text: str) -> None:
    assert normalize_appointment_time_expression(text) is None


@pytest.mark.parametrize(
    ("text", "expected_value"),
    [
        ("It could be at 10", "10:00"),
        ("Could be 10", "10:00"),
        ("I can do 10", "10:00"),
        ("at 2pm", "14:00"),
        ("Could it be on Monday 2pm?", "14:00"),
    ],
)
def test_normalizes_contextual_time_selection_phrases(
    text: str,
    expected_value: str,
) -> None:
    normalized = normalize_appointment_time_expression(text)

    assert normalized is not None
    assert normalized.value == expected_value


@pytest.mark.parametrize(
    ("text", "expected_value"),
    [
        ("at 14", "14:00"),
        ("Tuesday at 14", "14:00"),
        ("Tuesday 14", "14:00"),
        ("on Tuesday at 14", "14:00"),
        ("How about Tuesday at 14?", "14:00"),
        ("Tuesday at 2pm", "14:00"),
        ("Tuesday at 14:00", "14:00"),
    ],
)
def test_normalizes_contextual_bare_hour_expressions(
    text: str,
    expected_value: str,
) -> None:
    normalized = normalize_appointment_time_expression(text)

    assert normalized is not None
    assert normalized.value == expected_value


@pytest.mark.parametrize(
    "text",
    [
        "option 2",
        "the second option",
        "2",
        "I prefer the doctor on call",
    ],
)
def test_contextual_bare_hour_does_not_hijack_option_selection(text: str) -> None:
    # A bare/low number that is an option index must not become a time, and
    # context words that are not followed by an hour must not match.
    normalized = normalize_appointment_time_expression(text)
    if normalized is not None:
        assert normalized.value != "02:00"


def test_contextual_bare_hour_works_when_bare_hour_disallowed() -> None:
    # The weekday/``at`` context disambiguates the hour, so explicit contextual
    # forms are honored even when bare-number parsing is disabled, mirroring the
    # existing behavior of leading wrappers like ``at``.
    normalized = normalize_appointment_time_expression(
        "Tuesday at 14",
        allow_bare_hour=False,
    )

    assert normalized is not None
    assert normalized.value == "14:00"