from __future__ import annotations

import pytest

from app.services.dob_ambiguity import (
    AMBIGUOUS_NUMERIC_DOB_REASON,
    detect_ambiguous_numeric_dob,
    parse_dob_ambiguity_confirmation,
    parse_month_day_dob_clarification,
    parse_unambiguous_numeric_dob,
    try_resolve_pending_dob_ambiguity,
)


@pytest.mark.parametrize(
    ("text", "us_iso", "international_iso"),
    [
        ("09/08/1980", "1980-09-08", "1980-08-09"),
        ("9/8/1980", "1980-09-08", "1980-08-09"),
        ("08/09/1980", "1980-08-09", "1980-09-08"),
        ("03/04/1985", "1985-03-04", "1985-04-03"),
        ("Jane Doe, 09/08/1980", "1980-09-08", "1980-08-09"),
    ],
)
def test_detects_ambiguous_numeric_dob(
    text: str,
    us_iso: str,
    international_iso: str,
) -> None:
    issue = detect_ambiguous_numeric_dob(text)

    assert issue is not None
    assert issue.field == "date_of_birth"
    assert issue.reason == AMBIGUOUS_NUMERIC_DOB_REASON
    assert issue.candidates == [us_iso, international_iso]
    assert issue.clarification_question is not None


def test_clarification_proposes_us_style_interpretation_with_iso_fallback() -> None:
    issue = detect_ambiguous_numeric_dob("09/08/1980")

    assert issue is not None
    assert (
        issue.clarification_question
        == "Just to confirm, did you mean September 8, 1980? If not, please send the "
        "date of birth using YYYY-MM-DD, for example 1980-08-09."
    )


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("yes", True),
        ("Yes that's correct", True),
        ("correct", True),
        ("no", False),
        ("not that one", False),
        ("September 8, 1980", None),
    ],
)
def test_parse_dob_ambiguity_confirmation(message: str, expected: bool | None) -> None:
    assert parse_dob_ambiguity_confirmation(message) is expected


def test_try_resolve_pending_dob_ambiguity_accepts_yes() -> None:
    context = {
        "pending_dob_ambiguity": {
            "proposed_iso": "1980-09-08",
            "alternative_iso": "1980-08-09",
        },
    }

    confirmed, rejection = try_resolve_pending_dob_ambiguity("yes", context)

    assert confirmed == "1980-09-08"
    assert rejection is None


def test_try_resolve_pending_dob_ambiguity_rejects_no_with_format_reprompt() -> None:
    context = {
        "pending_dob_ambiguity": {
            "proposed_iso": "1980-09-08",
            "alternative_iso": "1980-08-09",
        },
    }

    confirmed, rejection = try_resolve_pending_dob_ambiguity("no", context)

    assert confirmed is None
    assert rejection is not None
    assert rejection.clarification_question == (
        "Please send the date of birth using YYYY-MM-DD, for example 1980-08-09."
    )


def test_iso_date_is_not_ambiguous() -> None:
    assert detect_ambiguous_numeric_dob("1980-09-08") is None


def test_month_name_date_is_not_ambiguous() -> None:
    assert detect_ambiguous_numeric_dob("September 8, 1980") is None
    assert detect_ambiguous_numeric_dob("Sep 8 1980") is None


@pytest.mark.parametrize("text", ["13/08/1980", "08/13/1980", "31/12/1990", "12/31/1990"])
def test_numeric_date_with_component_over_twelve_is_not_ambiguous(text: str) -> None:
    assert detect_ambiguous_numeric_dob(text) is None


def test_identical_interpretations_are_not_ambiguous() -> None:
    # 05/05/1980 reads the same as MM/DD or DD/MM, so there is nothing to clarify.
    assert detect_ambiguous_numeric_dob("05/05/1980") is None


def test_empty_or_missing_text_is_not_ambiguous() -> None:
    assert detect_ambiguous_numeric_dob(None) is None
    assert detect_ambiguous_numeric_dob("") is None
    assert detect_ambiguous_numeric_dob("no date here") is None


@pytest.mark.parametrize(
    ("message", "expected_iso"),
    [
        ("I meant Sept 8th", "1980-09-08"),
        ("Sept 8", "1980-09-08"),
        ("September 8", "1980-09-08"),
        ("Sep 8th", "1980-09-08"),
        ("I meant August 9th", "1980-08-09"),
    ],
)
def test_parse_month_day_dob_clarification_reuses_pending_year(
    message: str,
    expected_iso: str,
) -> None:
    assert parse_month_day_dob_clarification(message, year=1980) == expected_iso


def test_parse_month_day_dob_clarification_ignores_explicit_year() -> None:
    assert parse_month_day_dob_clarification("September 8, 1981", year=1980) is None


def test_try_resolve_pending_dob_ambiguity_accepts_month_day_without_year() -> None:
    context = {
        "pending_dob_ambiguity": {
            "proposed_iso": "1980-09-08",
            "alternative_iso": "1980-08-09",
        },
    }

    confirmed, rejection = try_resolve_pending_dob_ambiguity("I meant Sept 8th", context)

    assert confirmed == "1980-09-08"
    assert rejection is None


def test_try_resolve_pending_dob_ambiguity_accepts_alternate_month_day_without_year() -> None:
    context = {
        "pending_dob_ambiguity": {
            "proposed_iso": "1980-09-08",
            "alternative_iso": "1980-08-09",
        },
    }

    confirmed, rejection = try_resolve_pending_dob_ambiguity("I meant August 9th", context)

    assert confirmed == "1980-08-09"
    assert rejection is None


def test_month_day_without_year_requires_pending_ambiguity_context() -> None:
    confirmed, rejection = try_resolve_pending_dob_ambiguity("I meant Sept 8th", {})

    assert confirmed is None
    assert rejection is None


@pytest.mark.parametrize(
    ("text", "expected_iso"),
    [
        ("19/09/1996", "1996-09-19"),
        ("Felipe Marques, 19/09/1996", "1996-09-19"),
        ("9/8/1980", None),
        ("13/08/1980", "1980-08-13"),
        ("08/13/1980", "1980-08-13"),
        ("05/05/1980", "1980-05-05"),
        ("32/09/1996", None),
        ("19/19/1996", None),
    ],
)
def test_parse_unambiguous_numeric_dob(text: str, expected_iso: str | None) -> None:
    assert parse_unambiguous_numeric_dob(text) == expected_iso


@pytest.mark.parametrize(
    "text",
    [
        "09/08/1980",
        "Felipe Marques, 09/08/1980",
        "03/04/1985",
    ],
)
def test_parse_unambiguous_numeric_dob_returns_none_for_ambiguous_dates(
    text: str,
) -> None:
    assert parse_unambiguous_numeric_dob(text) is None
    assert detect_ambiguous_numeric_dob(text) is not None
