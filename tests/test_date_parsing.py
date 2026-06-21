from __future__ import annotations

from datetime import date

import pytest

from app.services.date_parsing import (
    DateParseResult,
    DateParseStatus,
    FixedClock,
    NaturalLanguageDateParser,
)

REFERENCE_DATE = date(2026, 7, 1)


@pytest.fixture()
def parser() -> NaturalLanguageDateParser:
    return NaturalLanguageDateParser(clock=FixedClock(current_date=REFERENCE_DATE))


def test_parse_today(parser: NaturalLanguageDateParser) -> None:
    result = parser.parse("Can I come in today?")

    assert result.status == DateParseStatus.PARSED
    assert result.normalized_date == "2026-07-01"
    assert result.source_text == "today"


def test_parse_tomorrow(parser: NaturalLanguageDateParser) -> None:
    result = parser.parse("tomorrow works for me")

    assert result.status == DateParseStatus.PARSED
    assert result.normalized_date == "2026-07-02"
    assert result.source_text == "tomorrow"


def test_parse_in_3_days(parser: NaturalLanguageDateParser) -> None:
    result = parser.parse("in 3 days")

    assert result.status == DateParseStatus.PARSED
    assert result.normalized_date == "2026-07-04"


def test_parse_next_monday_from_wednesday_reference_date(
    parser: NaturalLanguageDateParser,
) -> None:
    result = parser.parse("next monday")

    assert result.status == DateParseStatus.PARSED
    assert result.normalized_date == "2026-07-06"
    assert result.source_text is not None
    assert result.source_text.lower().startswith("next ")


def test_parse_this_friday_from_wednesday_reference_date(
    parser: NaturalLanguageDateParser,
) -> None:
    result = parser.parse("this friday")

    assert result.status == DateParseStatus.PARSED
    assert result.normalized_date == "2026-07-03"
    assert result.source_text is not None
    assert result.source_text.lower().startswith("this ")


def test_parse_iso_date(parser: NaturalLanguageDateParser) -> None:
    result = parser.parse("Please book me for 2026-07-02")

    assert result.status == DateParseStatus.PARSED
    assert result.normalized_date == "2026-07-02"
    assert result.source_text == "2026-07-02"
    assert result.reason is None


def test_parse_invalid_iso_date(parser: NaturalLanguageDateParser) -> None:
    result = parser.parse("2026-99-99")

    assert result.status == DateParseStatus.INVALID
    assert result.reason == "invalid_iso_date"
    assert result.source_text == "2026-99-99"
    assert result.normalized_date is None


def test_parse_next_week_is_unsupported(parser: NaturalLanguageDateParser) -> None:
    result = parser.parse("next week")

    assert result.status == DateParseStatus.UNSUPPORTED
    assert result.reason == "unsupported_expression"
    assert result.normalized_date is None


def test_parse_not_found_when_no_date_phrase(parser: NaturalLanguageDateParser) -> None:
    result = parser.parse("hello there")

    assert result.status == DateParseStatus.NOT_FOUND
    assert result.reason == "no_date_expression_found"
    assert result.normalized_date is None


def test_parse_multiple_iso_dates_is_ambiguous(parser: NaturalLanguageDateParser) -> None:
    result = parser.parse("2026-07-02 or 2026-07-03")

    assert result.status == DateParseStatus.AMBIGUOUS
    assert result.reason == "multiple_iso_dates"
    assert result.normalized_date is None


@pytest.mark.parametrize(
    "text",
    [
        "this week",
        "next month",
        "maybe later",
        "sometime next month",
        "soon please",
        "tomorrow morning",
        "this afternoon",
        "evening works",
    ],
)
def test_parse_other_unsupported_expressions(
    parser: NaturalLanguageDateParser,
    text: str,
) -> None:
    result = parser.parse(text)

    assert result.status == DateParseStatus.UNSUPPORTED
    assert result.reason == "unsupported_expression"
    assert result.normalized_date is None


def test_parse_multiple_weekday_expressions_is_ambiguous(
    parser: NaturalLanguageDateParser,
) -> None:
    result = parser.parse("today or tomorrow")

    assert result.status == DateParseStatus.AMBIGUOUS
    assert result.reason == "multiple_weekday_expressions"
    assert result.normalized_date is None


def test_parse_iso_and_natural_language_is_ambiguous(
    parser: NaturalLanguageDateParser,
) -> None:
    result = parser.parse("2026-07-02 or tomorrow")

    assert result.status == DateParseStatus.AMBIGUOUS
    assert result.reason == "multiple_date_expressions"
    assert result.normalized_date is None


def test_parse_empty_input(parser: NaturalLanguageDateParser) -> None:
    result = parser.parse("   ")

    assert result.status == DateParseStatus.NOT_FOUND
    assert result.reason == "empty_input"


def test_date_parse_result_to_metadata() -> None:
    result = DateParseResult(
        status=DateParseStatus.PARSED,
        normalized_date="2026-07-01",
        source_text="today",
    )

    assert result.to_metadata() == {
        "status": "parsed",
        "normalized_date": "2026-07-01",
        "source_text": "today",
        "reason": None,
    }
