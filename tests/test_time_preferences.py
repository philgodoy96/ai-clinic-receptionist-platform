from __future__ import annotations

import pytest

from app.services.time_preferences import TimePreferenceParser, TimePreferenceStatus


@pytest.fixture()
def parser() -> TimePreferenceParser:
    return TimePreferenceParser()


def test_tomorrow_morning_parses_morning_window(parser: TimePreferenceParser) -> None:
    result = parser.parse("tomorrow morning")

    assert result.status == TimePreferenceStatus.PARSED
    assert result.window is not None
    assert result.window.label == "morning"
    assert result.window.start_time == "08:00"
    assert result.window.end_time == "12:00"


def test_monday_afternoon_parses_afternoon_window(parser: TimePreferenceParser) -> None:
    result = parser.parse("Monday afternoon")

    assert result.status == TimePreferenceStatus.PARSED
    assert result.window is not None
    assert result.window.label == "afternoon"
    assert result.window.start_time == "12:00"
    assert result.window.end_time == "17:00"


def test_in_the_evening_parses_evening_window(parser: TimePreferenceParser) -> None:
    result = parser.parse("in the evening")

    assert result.status == TimePreferenceStatus.PARSED
    assert result.window is not None
    assert result.window.label == "evening"
    assert result.window.start_time == "17:00"
    assert result.window.end_time == "20:00"


def test_morning_or_afternoon_is_ambiguous(parser: TimePreferenceParser) -> None:
    result = parser.parse("morning or afternoon")

    assert result.status == TimePreferenceStatus.AMBIGUOUS
    assert result.window is None
    assert result.reason == "multiple_time_preferences"


def test_after_lunch_is_unsupported(parser: TimePreferenceParser) -> None:
    result = parser.parse("after lunch")

    assert result.status == TimePreferenceStatus.UNSUPPORTED
    assert result.window is None
    assert result.reason == "unsupported_time_preference"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "Dr. Emily Carter on 2026-07-02",
    ],
)
def test_text_without_time_preference_is_not_found(
    parser: TimePreferenceParser,
    text: str,
) -> None:
    result = parser.parse(text)

    assert result.status == TimePreferenceStatus.NOT_FOUND
    assert result.window is None
