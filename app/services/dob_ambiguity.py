"""Backend detection of ambiguous numeric dates of birth.

A numeric date such as ``09/08/1980`` is ambiguous when both the first and the
second component are valid months (``1``-``12``): it could mean September 8, 1980
in US-style ``MM/DD/YYYY`` or August 9, 1980 in international ``DD/MM/YYYY``.

Per the platform's architectural principle -- "The LLM understands. The backend
validates and decides." -- this module is the single backend authority that
decides whether a written date of birth is ambiguous and owns the clarification
wording. It must never silently choose one interpretation when both the day and
the month are ``<= 12``.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from app.domain.chat_turn_understanding import FieldIssue

AMBIGUOUS_NUMERIC_DOB_REASON = "ambiguous_numeric_date_format"
DOB_AMBIGUITY_REJECTED_REASON = "dob_ambiguity_rejected"
PENDING_DOB_AMBIGUITY_KEY = "pending_dob_ambiguity"

_DOB_CONFIRMATION_YES_PHRASES = (
    "yes",
    "yeah",
    "yep",
    "correct",
    "that's correct",
    "thats correct",
    "that is correct",
    "right",
    "affirmative",
)

_DOB_CONFIRMATION_NO_PHRASES = (
    "no",
    "nope",
    "not that one",
    "that's not right",
    "thats not right",
    "that is not right",
    "incorrect",
    "wrong",
)

# Matches numeric dates written as ``D/M/YYYY`` or ``M/D/YYYY`` (1-2 digit
# day/month components, 4 digit year). ISO ``YYYY-MM-DD`` dates never match here
# and are therefore always treated as unambiguous.
_NUMERIC_DOB_PATTERN = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")

_MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def format_iso_date_label(iso_value: str) -> str:
    """Render an ISO date (``YYYY-MM-DD``) as e.g. ``September 8, 1980``."""
    parsed = date.fromisoformat(iso_value)
    return f"{_MONTH_NAMES[parsed.month - 1]} {parsed.day}, {parsed.year}"


def build_dob_clarification_question(us_iso: str, international_iso: str) -> str:
    us_label = format_iso_date_label(us_iso)
    return (
        f"Just to confirm, did you mean {us_label}? If not, please send the "
        f"date of birth using YYYY-MM-DD, for example {international_iso}."
    )


def build_dob_format_reprompt(example_iso: str) -> str:
    return (
        "Please send the date of birth using YYYY-MM-DD, "
        f"for example {example_iso}."
    )


def pending_dob_ambiguity_context_updates(dob_issue: FieldIssue) -> dict[str, Any]:
    if not dob_issue.candidates or len(dob_issue.candidates) < 2:
        return {}
    return {
        PENDING_DOB_AMBIGUITY_KEY: {
            "proposed_iso": dob_issue.candidates[0],
            "alternative_iso": dob_issue.candidates[1],
        },
    }


def clear_pending_dob_ambiguity_updates() -> dict[str, None]:
    return {PENDING_DOB_AMBIGUITY_KEY: None}


def merge_dob_ambiguity_context_updates(
    updates: dict[str, Any],
    *,
    dob_issue: FieldIssue | None,
    date_of_birth: str | None,
) -> None:
    if dob_issue is not None:
        if dob_issue.reason == AMBIGUOUS_NUMERIC_DOB_REASON:
            updates.update(pending_dob_ambiguity_context_updates(dob_issue))
        elif dob_issue.reason == DOB_AMBIGUITY_REJECTED_REASON:
            updates.update(clear_pending_dob_ambiguity_updates())
    elif date_of_birth:
        updates.update(clear_pending_dob_ambiguity_updates())


def parse_dob_ambiguity_confirmation(message: str) -> bool | None:
    normalized = message.lower().strip().rstrip(".!")
    if any(
        normalized == phrase or normalized.startswith(f"{phrase} ")
        for phrase in _DOB_CONFIRMATION_NO_PHRASES
    ):
        return False
    if any(
        normalized == phrase
        or normalized.startswith(f"{phrase} ")
        or phrase in normalized
        for phrase in _DOB_CONFIRMATION_YES_PHRASES
    ):
        return True
    return None


def try_resolve_pending_dob_ambiguity(
    message: str,
    chat_context: dict[str, Any],
) -> tuple[str | None, FieldIssue | None]:
    """Handle yes/no replies to a pending ambiguous DOB clarification.

    Returns ``(confirmed_iso, rejection_issue)``. Both are ``None`` when the
    message is not a simple confirmation or rejection of the proposed date.
    """
    pending = chat_context.get(PENDING_DOB_AMBIGUITY_KEY)
    if not isinstance(pending, dict):
        return None, None

    proposed = pending.get("proposed_iso")
    alternative = pending.get("alternative_iso")
    if not isinstance(proposed, str) or not isinstance(alternative, str):
        return None, None

    confirmation = parse_dob_ambiguity_confirmation(message)
    if confirmation is True:
        return proposed, None
    if confirmation is False:
        return None, FieldIssue(
            field="date_of_birth",
            source_text=message.strip(),
            reason=DOB_AMBIGUITY_REJECTED_REASON,
            clarification_question=build_dob_format_reprompt(alternative),
        )
    return None, None


def detect_ambiguous_numeric_dob(text: str | None) -> FieldIssue | None:
    """Return a ``FieldIssue`` when ``text`` contains an ambiguous numeric DOB.

    The date is ambiguous only when the first and second numeric components are
    both in ``1``-``12`` and the two interpretations differ. ISO dates,
    month-name dates, and numeric dates with a component ``> 12`` (which pins the
    day) are unambiguous and return ``None``.
    """
    if not text:
        return None

    match = _NUMERIC_DOB_PATTERN.search(text)
    if match is None:
        return None

    first = int(match.group(1))
    second = int(match.group(2))
    year = int(match.group(3))

    if not (1 <= first <= 12 and 1 <= second <= 12):
        return None

    us_iso = f"{year}-{first:02d}-{second:02d}"  # MM/DD/YYYY
    international_iso = f"{year}-{second:02d}-{first:02d}"  # DD/MM/YYYY
    if us_iso == international_iso:
        # e.g. 05/05/1980 -- both readings resolve to the same calendar date.
        return None

    return FieldIssue(
        field="date_of_birth",
        source_text=match.group(0),
        reason=AMBIGUOUS_NUMERIC_DOB_REASON,
        candidates=[us_iso, international_iso],
        clarification_question=build_dob_clarification_question(us_iso, international_iso),
    )
