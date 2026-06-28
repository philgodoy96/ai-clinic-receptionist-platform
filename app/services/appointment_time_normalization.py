"""Narrow time-expression normalization for appointment intake and slot selection.

This module intentionally only understands the clock-time expressions a user
sends when selecting an offered appointment slot (for example ``3PM`` or
``15``). It normalizes them into the internal ``HH:MM`` 24-hour format the
backend uses to compare against offered slots. It is not a general purpose
date/time parser and must not be used outside appointment intake / slot
selection.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ``3pm`` / ``3 PM`` / ``2:30 pm`` / ``11 a.m.`` style expressions.
_AMPM_TIME_PATTERN = re.compile(
    r"\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?(?![a-z])",
    re.IGNORECASE,
)
# ``15:00`` / ``9:30`` style 24-hour (or 12-hour) clock expressions.
_COLON_TIME_PATTERN = re.compile(r"\b(\d{1,2}):(\d{2})\b")
# ``10h`` / ``10 h`` / ``10hs`` / ``10h30`` / ``10 h 30`` style clock
# expressions. The ``h`` (optionally ``hs``) makes the intent explicit, so this
# is treated as a confident time even when bare hours are disallowed.
_H_SUFFIX_TIME_PATTERN = re.compile(r"\b(\d{1,2})\s*hs?\s*(\d{2})?\b", re.IGNORECASE)
# A bare number that stands alone as the whole expression, e.g. ``15``.
_BARE_NUMBER_PATTERN = re.compile(r"^\d{1,2}$")
# A bare hour that appears mid-phrase but is disambiguated by an explicit time
# context word immediately before it, e.g. ``at 14`` / ``Tuesday at 14`` /
# ``Tuesday 14`` / ``on Tuesday at 14``. A leading ``at``/``on`` or weekday name
# signals that the trailing number is a clock hour rather than an option index,
# so it is safe to treat it as a time even though it is not the whole message.
# The negative lookahead avoids re-capturing the hour of a richer clock form
# (``at 14:30`` / ``at 14h``), which the colon/``h`` patterns already handle.
_CONTEXTUAL_BARE_HOUR_PATTERN = re.compile(
    r"\b(?:at|on|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"mon|tue|tues|wed|weds|thu|thur|thurs|fri|sat|sun)\b"
    r"(?:\s+(?:at|on))*"
    r"\s+(\d{1,2})\b(?!\s*[:h])",
    re.IGNORECASE,
)
# Leading conversational wrappers before a clock time, e.g. ``It could be at 10``.
_CONTEXTUAL_TIME_PREFIX_PATTERN = re.compile(
    r"^(?:"
    r"(?:it\s+)?could\s+(?:it\s+be\s+)?(?:be\s+)?(?:at\s+)?"
    r"|i\s+can\s+do\s+"
    r"|i\s+could\s+do\s+"
    r"|(?:how\s+about|maybe|perhaps)\s+(?:at\s+)?"
    r"|at\s+"
    r")+",
    re.IGNORECASE,
)
_TRAILING_PUNCTUATION_PATTERN = re.compile(r"[?.!]+$")

# Bare numbers below this are ambiguous (an option index such as ``3`` could
# mean "option 3" or "3 o'clock"), so we only treat unambiguous 24-hour hours
# (13-23) as a bare-hour time expression on the original message text.
_MIN_UNAMBIGUOUS_BARE_HOUR = 13
_MAX_BARE_HOUR = 23
_MIN_CONTEXTUAL_BARE_HOUR = 1


@dataclass(frozen=True, slots=True)
class NormalizedAppointmentTime:
    """A user time expression normalized to internal ``HH:MM`` format."""

    value: str
    raw: str


def strip_contextual_time_selection_phrases(text: str) -> str:
    """Remove conversational wrappers that precede a clock-time selection."""
    candidate = text.strip()
    if not candidate:
        return candidate

    candidate = _TRAILING_PUNCTUATION_PATTERN.sub("", candidate).strip()
    stripped = _CONTEXTUAL_TIME_PREFIX_PATTERN.sub("", candidate).strip()
    return stripped


def normalize_appointment_time_expression(
    text: str,
    *,
    allow_bare_hour: bool = True,
) -> NormalizedAppointmentTime | None:
    """Normalize a user clock-time expression to ``HH:MM`` (24-hour).

    Returns ``None`` when the text does not contain a confident clock time.

    Supported forms:
        * ``3PM`` / ``3 PM`` / ``2:30 PM`` -> ``15:00`` / ``15:00`` / ``14:30``
        * ``15:00`` -> ``15:00``
        * ``10h`` / ``10 h`` / ``10hs`` / ``10h30`` / ``10 h 30`` -> ``10:00``
          / ``10:00`` / ``10:00`` / ``10:30`` / ``10:30`` (the explicit ``h``
          marker is honored regardless of ``allow_bare_hour``)
        * ``15`` -> ``15:00`` (only when ``allow_bare_hour`` is True and the
          whole expression is an unambiguous 24-hour hour, 13-23)
        * ``It could be at 10`` / ``Could be 10`` / ``at 2pm`` -> ``10:00`` /
          ``10:00`` / ``14:00`` after stripping contextual wrappers
        * ``Tuesday at 14`` / ``Tuesday 14`` / ``on Tuesday at 14`` / ``at 14``
          -> ``14:00`` when an ``at``/``on`` or weekday context word precedes the
          bare hour mid-phrase

    Bare single/low numbers (``3``) are intentionally not treated as times on the
    original message to preserve option/reference selection semantics. After
    stripping contextual wrappers, bare hours 1-23 are accepted because the
    surrounding phrase disambiguates them from option numbers. A bare hour that
    appears mid-phrase is only accepted when an explicit time context word
    (``at``/``on`` or a weekday) immediately precedes it, so ``option 2`` stays an
    option index while ``Tuesday at 14`` becomes ``14:00``.
    """
    if not text:
        return None

    candidate = text.strip()
    if not candidate:
        return None

    normalized = _normalize_clock_time_candidate(
        candidate,
        allow_bare_hour=allow_bare_hour,
        min_bare_hour=_MIN_UNAMBIGUOUS_BARE_HOUR,
    )
    if normalized is not None:
        return normalized

    stripped = strip_contextual_time_selection_phrases(candidate)
    if stripped and stripped != candidate:
        normalized = _normalize_clock_time_candidate(
            stripped,
            allow_bare_hour=True,
            min_bare_hour=_MIN_CONTEXTUAL_BARE_HOUR,
        )
        if normalized is not None:
            return normalized

    return _normalize_contextual_bare_hour(candidate)


def _normalize_contextual_bare_hour(
    candidate: str,
) -> NormalizedAppointmentTime | None:
    """Normalize a mid-phrase bare hour preceded by a time context word.

    Recognizes forms like ``at 14`` / ``Tuesday at 14`` / ``Tuesday 14`` /
    ``on Tuesday at 14`` where an ``at``/``on`` or weekday context word makes the
    trailing number an unambiguous clock hour rather than an option index. Only
    hours 1-23 are accepted, matching the contextual range used after stripping
    leading conversational wrappers.
    """
    match = _CONTEXTUAL_BARE_HOUR_PATTERN.search(candidate)
    if match is None:
        return None

    hour = int(match.group(1))
    if _MIN_CONTEXTUAL_BARE_HOUR <= hour <= _MAX_BARE_HOUR:
        return NormalizedAppointmentTime(
            value=f"{hour:02d}:00",
            raw=match.group(1),
        )

    return None


def _normalize_clock_time_candidate(
    candidate: str,
    *,
    allow_bare_hour: bool,
    min_bare_hour: int,
) -> NormalizedAppointmentTime | None:
    ampm = _AMPM_TIME_PATTERN.search(candidate)
    if ampm is not None:
        hour = int(ampm.group(1))
        minute = int(ampm.group(2)) if ampm.group(2) is not None else 0
        meridiem = ampm.group(3).lower()
        if 1 <= hour <= 12 and minute <= 59:
            if meridiem == "p" and hour != 12:
                hour += 12
            elif meridiem == "a" and hour == 12:
                hour = 0
            return NormalizedAppointmentTime(
                value=f"{hour:02d}:{minute:02d}",
                raw=ampm.group(0),
            )

    colon = _COLON_TIME_PATTERN.search(candidate)
    if colon is not None:
        hour = int(colon.group(1))
        minute = int(colon.group(2))
        if hour <= 23 and minute <= 59:
            return NormalizedAppointmentTime(
                value=f"{hour:02d}:{minute:02d}",
                raw=colon.group(0),
            )

    h_suffix = _H_SUFFIX_TIME_PATTERN.search(candidate)
    if h_suffix is not None:
        hour = int(h_suffix.group(1))
        minute = int(h_suffix.group(2)) if h_suffix.group(2) is not None else 0
        if hour <= 23 and minute <= 59:
            return NormalizedAppointmentTime(
                value=f"{hour:02d}:{minute:02d}",
                raw=h_suffix.group(0),
            )

    if allow_bare_hour:
        bare = _BARE_NUMBER_PATTERN.fullmatch(candidate)
        if bare is not None:
            hour = int(bare.group(0))
            if min_bare_hour <= hour <= _MAX_BARE_HOUR:
                return NormalizedAppointmentTime(
                    value=f"{hour:02d}:00",
                    raw=bare.group(0),
                )

    return None
