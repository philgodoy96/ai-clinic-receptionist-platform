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
_BARE_NUMBER_PATTERN = re.compile(r"\d{1,2}")

# Bare numbers below this are ambiguous (an option index such as ``3`` could
# mean "option 3" or "3 o'clock"), so we only treat unambiguous 24-hour hours
# (13-23) as a bare-hour time expression.
_MIN_UNAMBIGUOUS_BARE_HOUR = 13
_MAX_BARE_HOUR = 23


@dataclass(frozen=True, slots=True)
class NormalizedAppointmentTime:
    """A user time expression normalized to internal ``HH:MM`` format."""

    value: str
    raw: str


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

    Bare single/low numbers (``3``) are intentionally not treated as times to
    preserve option/reference selection semantics.
    """
    if not text:
        return None

    candidate = text.strip()
    if not candidate:
        return None

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
            if _MIN_UNAMBIGUOUS_BARE_HOUR <= hour <= _MAX_BARE_HOUR:
                return NormalizedAppointmentTime(
                    value=f"{hour:02d}:00",
                    raw=bare.group(0),
                )

    return None
