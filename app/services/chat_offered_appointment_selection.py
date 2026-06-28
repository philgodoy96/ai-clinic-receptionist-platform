"""Shared offered-appointment selection matching for chat cancellation/reschedule."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo

from app.services.appointment_time_normalization import (
    normalize_appointment_time_expression,
    strip_contextual_time_selection_phrases,
)
from app.services.clinic_time import format_clinic_local_time_label, to_clinic_local_datetime
from app.services.date_parsing import DateParseStatus, NaturalLanguageDateParser
from app.services.date_parsing import FixedClock as DateParsingFixedClock

_ORDINAL_APPOINTMENT_KEYWORDS: dict[str, int] = {
    "the first one": 0,
    "first one": 0,
    "the first": 0,
    "the second one": 1,
    "second one": 1,
    "the second": 1,
    "the third one": 2,
    "third one": 2,
    "the third": 2,
}
_ORDINAL_WORDS = frozenset({"first", "second", "third"})
_OPTION_NUMBER_PATTERN = re.compile(r"^(?:number\s+)?(\d+)$")
_SPECIALTY_SELECTION_PATTERN = re.compile(
    r"\bthe\s+([a-z][a-z\s-]*?)\s+one\b",
    re.IGNORECASE,
)
_OFFERED_SUMMARY_PATTERN = re.compile(
    r"^(?P<specialty>.+?) with (?P<doctor>.+?) on "
    r"(?P<weekday>\w+)(?:,\s+\w+\s+\d+)? at (?P<time>\d{2}:\d{2})$",
)
_RELATIVE_DATE_PATTERN = re.compile(r"\b(today|tomorrow)\b", re.IGNORECASE)
_WEEKDAY_NAMES = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)


class OfferedAppointmentSelectionStatus(StrEnum):
    UNIQUE = "unique"
    ZERO = "zero"
    AMBIGUOUS = "ambiguous"
    NEAR_MATCH = "near_match"


@dataclass(frozen=True, slots=True)
class OfferedAppointmentView:
    appointment_id: str
    summary: str
    specialty_name: str
    doctor_name: str
    weekday: str
    time_label: str
    appointment_date: date | None = None
    doctor_id: str | None = None
    specialty_id: str | None = None
    start_time: str | None = None


@dataclass(frozen=True, slots=True)
class AppointmentSelectionConstraints:
    option_index: int | None = None
    ordinal_index: int | None = None
    specialty_query: str | None = None
    doctor_mentioned: bool = False
    calendar_date: date | None = None
    weekday: str | None = None
    time_label: str | None = None

    def attribute_count(self) -> int:
        count = 0
        if self.specialty_query is not None:
            count += 1
        if self.doctor_mentioned:
            count += 1
        if self.calendar_date is not None or self.weekday is not None:
            count += 1
        if self.time_label is not None:
            count += 1
        return count

    def uses_index_selection_only(self) -> bool:
        return (
            self.option_index is not None or self.ordinal_index is not None
        ) and self.attribute_count() == 0


@dataclass(frozen=True, slots=True)
class OfferedAppointmentSelectionResult:
    status: OfferedAppointmentSelectionStatus
    selected_appointment: dict[str, str] | None = None
    matching_appointments: tuple[OfferedAppointmentView, ...] = ()
    signals_detected: bool = False
    constraints_description: str | None = None


def prepare_appointment_selection_message(
    message: str,
    *,
    action_keywords: Sequence[str],
) -> str:
    """Strip cancel/reschedule keywords so selection can run on the same turn."""
    normalized = message.lower()
    for keyword in sorted(action_keywords, key=len, reverse=True):
        normalized = normalized.replace(keyword, " ")
    normalized = re.sub(r"\s+", " ", normalized).strip(" .,!?")
    return normalized or message


def build_offered_appointment_entry(
    *,
    appointment_id: str,
    summary: str,
    specialty_name: str,
    doctor_name: str,
    start_time: datetime,
    doctor_id: str | None = None,
    specialty_id: str | None = None,
) -> dict[str, str]:
    entry: dict[str, str] = {
        "appointment_id": appointment_id,
        "summary": summary,
        "specialty_name": specialty_name,
        "doctor_name": doctor_name,
        "start_time": start_time.isoformat(),
    }
    if doctor_id is not None:
        entry["doctor_id"] = doctor_id
    if specialty_id is not None:
        entry["specialty_id"] = specialty_id
    return entry


def load_offered_appointment_views(
    chat_context: dict[str, Any],
    *,
    clinic_timezone: ZoneInfo,
) -> list[OfferedAppointmentView]:
    raw_offered = chat_context.get("offered_appointments")
    if not isinstance(raw_offered, list):
        return []

    offered: list[OfferedAppointmentView] = []
    for item in raw_offered:
        if not isinstance(item, dict):
            continue
        view = _offered_appointment_view_from_item(item, clinic_timezone=clinic_timezone)
        if view is not None:
            offered.append(view)
    return offered


def resolve_offered_appointment_selection(
    *,
    message: str,
    offered: Sequence[OfferedAppointmentView],
    clinic_today: date | None = None,
) -> OfferedAppointmentSelectionResult:
    normalized_message = message.lower().strip()
    constraints = extract_appointment_selection_constraints(
        message=message,
        normalized_message=normalized_message,
        offered=offered,
        clinic_today=clinic_today,
    )
    signals_detected = _constraints_have_signals(constraints)

    if not signals_detected:
        return OfferedAppointmentSelectionResult(
            status=OfferedAppointmentSelectionStatus.ZERO,
            signals_detected=False,
        )

    if constraints.uses_index_selection_only():
        return _resolve_index_selection(
            constraints=constraints,
            offered=offered,
        )

    constraints_description = format_selection_constraints_description(
        constraints,
        offered=offered,
        normalized_message=normalized_message,
    )
    exact_matches = _find_exact_attribute_matches(
        offered=offered,
        constraints=constraints,
        normalized_message=normalized_message,
    )

    if len(exact_matches) == 1:
        selected = exact_matches[0]
        return OfferedAppointmentSelectionResult(
            status=OfferedAppointmentSelectionStatus.UNIQUE,
            selected_appointment={
                "appointment_id": selected.appointment_id,
                "summary": selected.summary,
            },
            signals_detected=True,
            constraints_description=constraints_description,
        )

    if len(exact_matches) > 1:
        return OfferedAppointmentSelectionResult(
            status=OfferedAppointmentSelectionStatus.AMBIGUOUS,
            matching_appointments=exact_matches,
            signals_detected=True,
            constraints_description=constraints_description,
        )

    near_matches = _find_near_attribute_matches(
        offered=offered,
        constraints=constraints,
        normalized_message=normalized_message,
    )
    if near_matches:
        return OfferedAppointmentSelectionResult(
            status=OfferedAppointmentSelectionStatus.NEAR_MATCH,
            matching_appointments=near_matches,
            signals_detected=True,
            constraints_description=constraints_description,
        )

    return OfferedAppointmentSelectionResult(
        status=OfferedAppointmentSelectionStatus.ZERO,
        signals_detected=True,
        constraints_description=constraints_description,
    )


def extract_appointment_selection_constraints(
    *,
    message: str,
    normalized_message: str,
    offered: Sequence[OfferedAppointmentView],
    clinic_today: date | None,
) -> AppointmentSelectionConstraints:
    calendar_date = extract_relative_calendar_date(
        message,
        clinic_today=clinic_today,
    )
    return AppointmentSelectionConstraints(
        option_index=extract_option_number_index(
            normalized_message,
            option_count=len(offered),
        ),
        ordinal_index=extract_ordinal_index(
            normalized_message,
            option_count=len(offered),
        ),
        specialty_query=extract_specialty_selection_query(
            normalized_message,
            offered=offered,
        ),
        doctor_mentioned=any_doctor_mentioned(normalized_message, offered),
        calendar_date=calendar_date,
        weekday=(
            None
            if calendar_date is not None
            else extract_weekday_constraint(normalized_message, offered)
        ),
        time_label=extract_normalized_selection_time(
            message,
            has_calendar_date_signal=calendar_date is not None,
        ),
    )


def format_selection_constraints_description(
    constraints: AppointmentSelectionConstraints,
    *,
    offered: Sequence[OfferedAppointmentView],
    normalized_message: str,
) -> str | None:
    parts: list[str] = []

    if constraints.calendar_date is not None:
        parts.append(constraints.calendar_date.strftime("%A"))
    elif constraints.weekday is not None:
        parts.append(constraints.weekday.title())

    if constraints.time_label is not None:
        parts.append(f"at {constraints.time_label}")

    if constraints.doctor_mentioned:
        for item in offered:
            if doctor_name_in_message(normalized_message, item.doctor_name):
                parts.append(f"with {item.doctor_name}")
                break

    if constraints.specialty_query is not None:
        parts.append(constraints.specialty_query.title())

    if not parts:
        return None
    return " ".join(parts)


def format_appointment_selection_option_label(
    appointment: OfferedAppointmentView,
) -> str:
    return (
        f"{appointment.weekday} at {appointment.time_label} "
        f"with {appointment.doctor_name}"
    )


def format_ambiguous_appointment_selection_message(
    *,
    matching: Sequence[OfferedAppointmentView],
    action_verb: str,
) -> str:
    options = "\n".join(
        f"{index}. {item.summary}."
        for index, item in enumerate(matching, start=1)
    )
    return (
        f"I found more than one matching appointment:\n\n"
        f"{options}\n\n"
        f"Which one would you like to {action_verb}?"
    )


def format_near_match_appointment_selection_message(
    *,
    constraints_description: str,
    matching: Sequence[OfferedAppointmentView],
) -> str:
    options = "\n".join(
        f"{index}. {format_appointment_selection_option_label(item)}"
        for index, item in enumerate(matching, start=1)
    )
    return (
        f"I couldn't find an appointment matching {constraints_description}. "
        f"I did find these similar appointments:\n\n"
        f"{options}\n\n"
        f"Which one did you mean?"
    )


def format_no_useful_match_appointment_selection_message(
    *,
    constraints_description: str | None,
    offered: Sequence[OfferedAppointmentView],
) -> str:
    examples = "option 1"
    if offered:
        first = offered[0]
        examples = f"option 1 or {first.weekday} at {first.time_label}"

    if constraints_description:
        prefix = (
            f"I couldn't find an appointment matching {constraints_description}. "
        )
    else:
        prefix = "I couldn't find an appointment matching that description. "

    return (
        f"{prefix}"
        f"Please choose one of the listed appointments, for example: {examples}."
    )


def extract_relative_calendar_date(
    message: str,
    *,
    clinic_today: date | None,
) -> date | None:
    if clinic_today is None or not _RELATIVE_DATE_PATTERN.search(message):
        return None

    parser = NaturalLanguageDateParser(clock=DateParsingFixedClock(clinic_today))
    result = parser.parse(message)
    if result.status is not DateParseStatus.PARSED or result.normalized_date is None:
        return None

    return date.fromisoformat(result.normalized_date)


def extract_weekday_constraint(
    normalized_message: str,
    offered: Sequence[OfferedAppointmentView],
) -> str | None:
    for item in offered:
        weekday = item.weekday.lower()
        if weekday in normalized_message:
            return weekday

    for weekday in _WEEKDAY_NAMES:
        if re.search(rf"\b{weekday}\b", normalized_message):
            return weekday

    return None


def extract_normalized_selection_time(
    message: str,
    *,
    has_calendar_date_signal: bool,
) -> str | None:
    for candidate in _time_selection_candidates(message):
        normalized = normalize_appointment_time_expression(
            candidate,
            allow_bare_hour=False,
        )
        if normalized is not None:
            return normalized.value

        stripped = strip_contextual_time_selection_phrases(candidate)
        if stripped and stripped != candidate:
            normalized = normalize_appointment_time_expression(
                stripped,
                allow_bare_hour=True,
            )
            if normalized is not None:
                return normalized.value

        if has_calendar_date_signal:
            remainder = _RELATIVE_DATE_PATTERN.sub(" ", candidate)
            remainder = re.sub(r"\s+", " ", remainder).strip(" .,!?")
            if remainder:
                normalized = normalize_appointment_time_expression(
                    remainder,
                    allow_bare_hour=True,
                )
                if normalized is not None:
                    return normalized.value

    return None


def extract_option_number_index(
    normalized_message: str,
    *,
    option_count: int,
) -> int | None:
    match = _OPTION_NUMBER_PATTERN.match(normalized_message.strip())
    if match is None:
        return None
    option_number = int(match.group(1))
    if option_number < 1 or option_number > option_count:
        return None
    return option_number - 1


def extract_ordinal_index(
    normalized_message: str,
    *,
    option_count: int,
) -> int | None:
    for keyword, index in sorted(
        _ORDINAL_APPOINTMENT_KEYWORDS.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        if keyword in normalized_message and index < option_count:
            return index
    return None


def extract_specialty_selection_query(
    normalized_message: str,
    *,
    offered: Sequence[OfferedAppointmentView],
) -> str | None:
    match = _SPECIALTY_SELECTION_PATTERN.search(normalized_message)
    if match is not None:
        specialty_query = match.group(1).strip().lower()
        if (
            specialty_query not in _ORDINAL_WORDS
            and specialty_query not in _WEEKDAY_NAMES
        ):
            return specialty_query

    for item in offered:
        specialty = item.specialty_name.lower()
        if re.search(rf"\b{re.escape(specialty)}\b", normalized_message):
            return specialty

    return None


def any_doctor_mentioned(
    normalized_message: str,
    offered: Sequence[OfferedAppointmentView],
) -> bool:
    return any(
        doctor_name_in_message(normalized_message, item.doctor_name)
        for item in offered
    )


def doctor_name_in_message(normalized_message: str, doctor_name: str) -> bool:
    normalized_name = doctor_name.replace(".", "").lower()
    if normalized_name in normalized_message:
        return True

    name_terms = [
        term
        for term in normalized_name.split()
        if term not in {"dr", "doctor"}
    ]
    if not name_terms:
        return False

    return any(term in normalized_message for term in name_terms if len(term) >= 3)


def parse_offered_appointment_summary(summary: str) -> dict[str, str] | None:
    match = _OFFERED_SUMMARY_PATTERN.match(summary)
    if match is None:
        return None
    return {
        "specialty_name": match.group("specialty"),
        "doctor_name": match.group("doctor"),
        "weekday": match.group("weekday"),
        "time_label": match.group("time"),
    }


@dataclass(frozen=True, slots=True)
class OfferedAppointmentSelectionResolution:
    result: OfferedAppointmentSelectionResult
    offered_all: tuple[OfferedAppointmentView, ...]
    offered_candidates: tuple[OfferedAppointmentView, ...]
    pending_refinement_active: bool


def constraints_have_signals(constraints: AppointmentSelectionConstraints) -> bool:
    return _constraints_have_signals(constraints)


OfferedAppointmentFilter = Callable[
    [Sequence[OfferedAppointmentView], dict[str, Any]],
    tuple[tuple[OfferedAppointmentView, ...], bool],
]


def resolve_offered_appointment_selection_from_context(
    *,
    message: str,
    chat_context: dict[str, Any],
    clinic_timezone: ZoneInfo,
    clinic_today: date | None,
    offered_filter: OfferedAppointmentFilter | None = None,
) -> OfferedAppointmentSelectionResolution:
    offered_all = tuple(
        load_offered_appointment_views(
            chat_context,
            clinic_timezone=clinic_timezone,
        ),
    )
    if offered_filter is not None:
        offered_candidates, pending_active = offered_filter(offered_all, chat_context)
    else:
        offered_candidates = offered_all
        pending_active = False

    result = resolve_offered_appointment_selection(
        message=message,
        offered=offered_candidates,
        clinic_today=clinic_today,
    )
    return OfferedAppointmentSelectionResolution(
        result=result,
        offered_all=offered_all,
        offered_candidates=offered_candidates,
        pending_refinement_active=pending_active,
    )


def _constraints_have_signals(constraints: AppointmentSelectionConstraints) -> bool:
    return (
        constraints.option_index is not None
        or constraints.ordinal_index is not None
        or constraints.attribute_count() > 0
    )


def _resolve_index_selection(
    *,
    constraints: AppointmentSelectionConstraints,
    offered: Sequence[OfferedAppointmentView],
) -> OfferedAppointmentSelectionResult:
    index = constraints.option_index
    if index is None:
        index = constraints.ordinal_index
    assert index is not None

    if 0 <= index < len(offered):
        selected = offered[index]
        return OfferedAppointmentSelectionResult(
            status=OfferedAppointmentSelectionStatus.UNIQUE,
            selected_appointment={
                "appointment_id": selected.appointment_id,
                "summary": selected.summary,
            },
            signals_detected=True,
        )

    return OfferedAppointmentSelectionResult(
        status=OfferedAppointmentSelectionStatus.ZERO,
        signals_detected=True,
    )


def _appointment_match_flags(
    appointment: OfferedAppointmentView,
    constraints: AppointmentSelectionConstraints,
    normalized_message: str,
) -> dict[str, bool]:
    flags: dict[str, bool] = {}

    if constraints.specialty_query is not None:
        flags["specialty"] = (
            constraints.specialty_query in appointment.specialty_name.lower()
        )

    if constraints.doctor_mentioned:
        flags["doctor"] = doctor_name_in_message(
            normalized_message,
            appointment.doctor_name,
        )

    if constraints.calendar_date is not None:
        flags["date"] = appointment.appointment_date == constraints.calendar_date
    elif constraints.weekday is not None:
        flags["weekday"] = appointment.weekday.lower() == constraints.weekday

    if constraints.time_label is not None:
        flags["time"] = appointment.time_label == constraints.time_label

    return flags


def _appointment_matches_all_constraints(flags: dict[str, bool]) -> bool:
    return bool(flags) and all(flags.values())


def _is_near_partial_match(*, matched_count: int, total_constraints: int) -> bool:
    if matched_count <= 0 or matched_count >= total_constraints:
        return False
    if total_constraints >= 3:
        return matched_count >= total_constraints - 1
    if total_constraints == 2:
        return matched_count >= 1
    return False


def _find_exact_attribute_matches(
    *,
    offered: Sequence[OfferedAppointmentView],
    constraints: AppointmentSelectionConstraints,
    normalized_message: str,
) -> tuple[OfferedAppointmentView, ...]:
    matches: list[OfferedAppointmentView] = []
    for appointment in offered:
        flags = _appointment_match_flags(
            appointment,
            constraints,
            normalized_message,
        )
        if _appointment_matches_all_constraints(flags):
            matches.append(appointment)
    return tuple(matches)


def _find_near_attribute_matches(
    *,
    offered: Sequence[OfferedAppointmentView],
    constraints: AppointmentSelectionConstraints,
    normalized_message: str,
) -> tuple[OfferedAppointmentView, ...]:
    total_constraints = constraints.attribute_count()
    if total_constraints < 2:
        return ()

    scored: list[tuple[int, int, OfferedAppointmentView]] = []
    for index, appointment in enumerate(offered):
        flags = _appointment_match_flags(
            appointment,
            constraints,
            normalized_message,
        )
        if not flags:
            continue
        matched_count = sum(1 for matched in flags.values() if matched)
        if _is_near_partial_match(
            matched_count=matched_count,
            total_constraints=len(flags),
        ):
            scored.append((matched_count, index, appointment))

    scored.sort(key=lambda item: (-item[0], item[1]))
    return tuple(appointment for _, _, appointment in scored)


def _offered_appointment_view_from_item(
    item: dict[str, Any],
    *,
    clinic_timezone: ZoneInfo,
) -> OfferedAppointmentView | None:
    appointment_id = item.get("appointment_id")
    summary = item.get("summary")
    if not isinstance(appointment_id, str) or not isinstance(summary, str):
        return None

    structured = _structured_fields_from_item(item, clinic_timezone=clinic_timezone)
    if structured is not None:
        return OfferedAppointmentView(
            appointment_id=appointment_id,
            summary=summary,
            **structured,
        )

    parsed = parse_offered_appointment_summary(summary)
    if parsed is None:
        return None

    return OfferedAppointmentView(
        appointment_id=appointment_id,
        summary=summary,
        specialty_name=parsed["specialty_name"],
        doctor_name=parsed["doctor_name"],
        weekday=parsed["weekday"],
        time_label=parsed["time_label"],
    )


def _structured_fields_from_item(
    item: dict[str, Any],
    *,
    clinic_timezone: ZoneInfo,
) -> dict[str, Any] | None:
    specialty_name = item.get("specialty_name")
    doctor_name = item.get("doctor_name")
    start_time_raw = item.get("start_time")
    if (
        not isinstance(specialty_name, str)
        or not isinstance(doctor_name, str)
        or not isinstance(start_time_raw, str)
    ):
        return None

    try:
        start_time = datetime.fromisoformat(start_time_raw)
    except ValueError:
        return None

    localized_start = to_clinic_local_datetime(start_time, clinic_timezone)
    return {
        "specialty_name": specialty_name,
        "doctor_name": doctor_name,
        "weekday": localized_start.strftime("%A"),
        "time_label": format_clinic_local_time_label(start_time, clinic_timezone),
        "appointment_date": localized_start.date(),
        "doctor_id": _optional_str(item.get("doctor_id")),
        "specialty_id": _optional_str(item.get("specialty_id")),
        "start_time": start_time_raw,
    }


def _optional_str(value: object) -> str | None:
    if isinstance(value, str):
        return value
    return None


def _time_selection_candidates(message: str) -> list[str]:
    candidates = [message]
    stripped = strip_contextual_time_selection_phrases(message)
    if stripped and stripped not in candidates:
        candidates.append(stripped)
    return candidates
