from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class Weekday(StrEnum):
    MONDAY = "monday"
    TUESDAY = "tuesday"
    WEDNESDAY = "wednesday"
    THURSDAY = "thursday"
    FRIDAY = "friday"
    SATURDAY = "saturday"
    SUNDAY = "sunday"


class DateExpressionKind(StrEnum):
    TODAY = "today"
    TOMORROW = "tomorrow"
    THIS_WEEKDAY = "this_weekday"
    NEXT_WEEKDAY = "next_weekday"
    NEXT_WEEK = "next_week"
    IN_N_DAYS = "in_n_days"
    EXACT_DATE = "exact_date"
    UNKNOWN = "unknown"


class TimeWindowExpressionKind(StrEnum):
    MORNING = "morning"
    AFTERNOON = "afternoon"
    EVENING = "evening"
    EXACT_TIME = "exact_time"
    UNKNOWN = "unknown"


class DateResolutionStatus(StrEnum):
    RESOLVED = "resolved"
    PAST_DATE = "past_date"
    CLOSED_DAY = "closed_day"
    UNKNOWN = "unknown"


class TimeWindowResolutionStatus(StrEnum):
    RESOLVED = "resolved"
    OUTSIDE_BUSINESS_HOURS = "outside_business_hours"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class DateExpression:
    kind: DateExpressionKind
    weekday: Weekday | None = None
    days_offset: int | None = None
    exact_date: date | None = None


@dataclass(frozen=True, slots=True)
class TimeWindowExpression:
    kind: TimeWindowExpressionKind
    exact_time: str | None = None


@dataclass(frozen=True, slots=True)
class ResolvedDate:
    status: DateResolutionStatus
    resolved_date: date | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ResolvedTimeWindow:
    status: TimeWindowResolutionStatus
    label: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    exact_time: str | None = None
    reason: str | None = None
