from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum
from typing import Any, Protocol

_ISO_DATE_PATTERN = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_IN_DAYS_PATTERN = re.compile(r"\bin\s+(\d+)\s+days?\b", re.IGNORECASE)
_THIS_WEEKDAY_PATTERN = re.compile(
    r"\bthis\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    re.IGNORECASE,
)
_NEXT_WEEKDAY_PATTERN = re.compile(
    r"\bnext\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    re.IGNORECASE,
)
_TODAY_PATTERN = re.compile(r"\btoday\b", re.IGNORECASE)
_TOMORROW_PATTERN = re.compile(r"\btomorrow\b", re.IGNORECASE)

_WEEKDAY_TO_INDEX = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

_UNSUPPORTED_PHRASES = (
    "next week",
    "this week",
    "next month",
    "later",
    "sometime",
    "soon",
    "morning",
    "afternoon",
    "evening",
)


class Clock(Protocol):
    def today(self) -> date: ...


class SystemClock:
    def today(self) -> date:
        return date.today()


@dataclass(frozen=True, slots=True)
class FixedClock:
    current_date: date

    def today(self) -> date:
        return self.current_date


class DateParseStatus(StrEnum):
    PARSED = "parsed"
    NOT_FOUND = "not_found"
    AMBIGUOUS = "ambiguous"
    INVALID = "invalid"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class DateParseResult:
    status: DateParseStatus
    normalized_date: str | None = None
    source_text: str | None = None
    reason: str | None = None

    def to_metadata(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "normalized_date": self.normalized_date,
            "source_text": self.source_text,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class _IsoDateMatch:
    source_text: str
    parsed_date: date | None


@dataclass(frozen=True, slots=True)
class _NaturalLanguageMatch:
    source_text: str
    parsed_date: date


class NaturalLanguageDateParser:
    def __init__(self, clock: Clock | None = None) -> None:
        self._clock = clock or SystemClock()

    def parse(self, text: str) -> DateParseResult:
        normalized_text = text.strip()
        if not normalized_text:
            return DateParseResult(
                status=DateParseStatus.NOT_FOUND,
                reason="empty_input",
            )

        lowered_text = normalized_text.lower()
        for phrase in _UNSUPPORTED_PHRASES:
            if phrase in lowered_text:
                return DateParseResult(
                    status=DateParseStatus.UNSUPPORTED,
                    source_text=phrase,
                    reason="unsupported_expression",
                )

        iso_matches = self._find_iso_dates(normalized_text)
        natural_language_matches = self._find_natural_language_dates(normalized_text)

        valid_iso_matches = [match for match in iso_matches if match.parsed_date is not None]
        invalid_iso_matches = [match for match in iso_matches if match.parsed_date is None]

        if invalid_iso_matches and not valid_iso_matches and not natural_language_matches:
            return DateParseResult(
                status=DateParseStatus.INVALID,
                source_text=invalid_iso_matches[0].source_text,
                reason="invalid_iso_date",
            )

        expression_count = len(valid_iso_matches) + len(natural_language_matches)
        if expression_count == 0:
            return DateParseResult(
                status=DateParseStatus.NOT_FOUND,
                reason="no_date_expression_found",
            )

        if len(valid_iso_matches) > 1:
            return DateParseResult(
                status=DateParseStatus.AMBIGUOUS,
                source_text=(
                    f"{valid_iso_matches[0].source_text}, {valid_iso_matches[1].source_text}"
                ),
                reason="multiple_iso_dates",
            )

        if len(natural_language_matches) > 1:
            return DateParseResult(
                status=DateParseStatus.AMBIGUOUS,
                source_text=(
                    f"{natural_language_matches[0].source_text}, "
                    f"{natural_language_matches[1].source_text}"
                ),
                reason="multiple_weekday_expressions",
            )

        if valid_iso_matches and natural_language_matches:
            return DateParseResult(
                status=DateParseStatus.AMBIGUOUS,
                source_text=(
                    f"{valid_iso_matches[0].source_text}, {natural_language_matches[0].source_text}"
                ),
                reason="multiple_date_expressions",
            )

        if valid_iso_matches:
            match = valid_iso_matches[0]
            assert match.parsed_date is not None
            return DateParseResult(
                status=DateParseStatus.PARSED,
                normalized_date=match.parsed_date.isoformat(),
                source_text=match.source_text,
            )

        nl_match = natural_language_matches[0]
        return DateParseResult(
            status=DateParseStatus.PARSED,
            normalized_date=nl_match.parsed_date.isoformat(),
            source_text=nl_match.source_text,
        )

    def _find_iso_dates(self, text: str) -> list[_IsoDateMatch]:
        matches: list[_IsoDateMatch] = []
        for match in _ISO_DATE_PATTERN.finditer(text):
            source_text = match.group(1)
            try:
                parsed_date = date.fromisoformat(source_text)
            except ValueError:
                matches.append(_IsoDateMatch(source_text=source_text, parsed_date=None))
                continue

            matches.append(_IsoDateMatch(source_text=source_text, parsed_date=parsed_date))

        return matches

    def _find_natural_language_dates(self, text: str) -> list[_NaturalLanguageMatch]:
        matches: list[_NaturalLanguageMatch] = []
        today = self._clock.today()

        for match in _TODAY_PATTERN.finditer(text):
            matches.append(
                _NaturalLanguageMatch(
                    source_text=match.group(0),
                    parsed_date=today,
                ),
            )

        for match in _TOMORROW_PATTERN.finditer(text):
            matches.append(
                _NaturalLanguageMatch(
                    source_text=match.group(0),
                    parsed_date=today + timedelta(days=1),
                ),
            )

        for match in _IN_DAYS_PATTERN.finditer(text):
            days = int(match.group(1))
            matches.append(
                _NaturalLanguageMatch(
                    source_text=match.group(0),
                    parsed_date=today + timedelta(days=days),
                ),
            )

        for match in _THIS_WEEKDAY_PATTERN.finditer(text):
            weekday_name = match.group(1).lower()
            matches.append(
                _NaturalLanguageMatch(
                    source_text=match.group(0),
                    parsed_date=self._this_weekday(today, _WEEKDAY_TO_INDEX[weekday_name]),
                ),
            )

        for match in _NEXT_WEEKDAY_PATTERN.finditer(text):
            weekday_name = match.group(1).lower()
            matches.append(
                _NaturalLanguageMatch(
                    source_text=match.group(0),
                    parsed_date=self._next_weekday(today, _WEEKDAY_TO_INDEX[weekday_name]),
                ),
            )

        return matches

    def _this_weekday(self, today: date, weekday_index: int) -> date:
        start_of_week = today - timedelta(days=today.weekday())
        return start_of_week + timedelta(days=weekday_index)

    def _next_weekday(self, today: date, weekday_index: int) -> date:
        start_of_week = today - timedelta(days=today.weekday())
        start_of_next_week = start_of_week + timedelta(days=7)
        return start_of_next_week + timedelta(days=weekday_index)
