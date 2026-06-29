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

_MONTH_NAME_TO_INDEX = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sept": 9,
    "sep": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}
# Longest names first so abbreviations do not shadow full names (e.g. ``sept``).
_MONTH_NAMES_ALTERNATION = "|".join(
    sorted(_MONTH_NAME_TO_INDEX, key=len, reverse=True),
)
# ``July 6`` / ``July 6th`` / ``July 06`` / ``July 6, 2026`` / ``July 6 2026``.
_MONTH_DAY_PATTERN = re.compile(
    rf"\b(?P<month>{_MONTH_NAMES_ALTERNATION})\s+"
    r"(?P<day>\d{1,2})(?:st|nd|rd|th)?"
    r"(?:,?\s+(?P<year>\d{4}))?\b",
    re.IGNORECASE,
)
# ``6 July`` / ``6th of July`` / ``6 July 2026``.
_DAY_MONTH_PATTERN = re.compile(
    r"\b(?P<day>\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?"
    rf"(?P<month>{_MONTH_NAMES_ALTERNATION})"
    r"(?:,?\s+(?P<year>\d{4}))?\b",
    re.IGNORECASE,
)

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

        matches.extend(self._find_month_name_dates(text, today))

        return matches

    def _find_month_name_dates(self, text: str, today: date) -> list[_NaturalLanguageMatch]:
        matches: list[_NaturalLanguageMatch] = []
        consumed_spans: list[tuple[int, int]] = []

        # ``6 July`` is matched first so ``July 06`` is not spuriously extracted
        # from a trailing year (e.g. the ``July 20`` inside ``6 July 2026``).
        for pattern in (_DAY_MONTH_PATTERN, _MONTH_DAY_PATTERN):
            for match in pattern.finditer(text):
                start, end = match.span()
                overlaps_existing = any(
                    start < span_end and span_start < end
                    for span_start, span_end in consumed_spans
                )
                if overlaps_existing:
                    continue

                month_index = _MONTH_NAME_TO_INDEX[match.group("month").lower()]
                day = int(match.group("day"))
                year_text = match.group("year")
                year = int(year_text) if year_text else None

                resolved_date = self._resolve_calendar_date(month_index, day, year, today)
                if resolved_date is None:
                    continue

                consumed_spans.append((start, end))
                matches.append(
                    _NaturalLanguageMatch(
                        source_text=match.group(0),
                        parsed_date=resolved_date,
                    ),
                )

        return matches

    def _resolve_calendar_date(
        self,
        month: int,
        day: int,
        year: int | None,
        today: date,
    ) -> date | None:
        if year is not None:
            return self._safe_date(year, month, day)

        # No explicit year: pick the next upcoming occurrence of month/day.
        this_year = self._safe_date(today.year, month, day)
        if this_year is not None and this_year >= today:
            return this_year

        next_year = self._safe_date(today.year + 1, month, day)
        if next_year is not None:
            return next_year

        return this_year

    @staticmethod
    def _safe_date(year: int, month: int, day: int) -> date | None:
        try:
            return date(year, month, day)
        except ValueError:
            return None

    def _this_weekday(self, today: date, weekday_index: int) -> date:
        start_of_week = today - timedelta(days=today.weekday())
        return start_of_week + timedelta(days=weekday_index)

    def _next_weekday(self, today: date, weekday_index: int) -> date:
        days_ahead = (weekday_index - today.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        return today + timedelta(days=days_ahead)
