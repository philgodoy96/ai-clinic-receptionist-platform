from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from app.domain.scheduling.expressions import (
    DateExpression,
    DateExpressionKind,
    DateResolutionStatus,
    ResolvedDate,
    ResolvedTimeWindow,
    TimeWindowExpression,
    TimeWindowExpressionKind,
    TimeWindowResolutionStatus,
    Weekday,
)
from app.services.clock import Clock, SystemClock

if TYPE_CHECKING:
    from app.core.config import Settings

_HH_MM_PATTERN = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_WEEKDAY_TO_INDEX = {
    Weekday.MONDAY: 0,
    Weekday.TUESDAY: 1,
    Weekday.WEDNESDAY: 2,
    Weekday.THURSDAY: 3,
    Weekday.FRIDAY: 4,
    Weekday.SATURDAY: 5,
    Weekday.SUNDAY: 6,
}
_INDEX_TO_WEEKDAY_NAME = tuple(day.value for day in Weekday)
_STANDARD_TIME_WINDOWS = {
    TimeWindowExpressionKind.MORNING: ("morning", "08:00", "12:00"),
    TimeWindowExpressionKind.AFTERNOON: ("afternoon", "12:00", "17:00"),
    TimeWindowExpressionKind.EVENING: ("evening", "17:00", "20:00"),
}


@dataclass(frozen=True, slots=True)
class ClinicContext:
    clinic_name: str
    clinic_timezone: str
    current_date: str
    current_weekday: str
    business_days: list[str]
    business_hours_start: str
    business_hours_end: str

    def to_tool_result(self) -> dict[str, Any]:
        return {
            "clinic_name": self.clinic_name,
            "clinic_timezone": self.clinic_timezone,
            "current_date": self.current_date,
            "current_weekday": self.current_weekday,
            "business_days": self.business_days,
            "business_hours": {
                "start": self.business_hours_start,
                "end": self.business_hours_end,
            },
        }


class ClinicTimeService:
    def __init__(
        self,
        *,
        clinic_name: str,
        timezone: str,
        business_days: str,
        business_hours_start: str,
        business_hours_end: str,
        clock: Clock | None = None,
    ) -> None:
        self._clinic_name = clinic_name.strip()
        self._timezone_name = timezone.strip()
        self._clock = clock or SystemClock()
        self._timezone = ZoneInfo(self._timezone_name)
        self._business_days = frozenset(
            day.strip().lower() for day in business_days.split(",") if day.strip()
        )
        self._business_hours_start = business_hours_start.strip()
        self._business_hours_end = business_hours_end.strip()
        self._business_hours_start_minutes = _minutes_from_hhmm(self._business_hours_start)
        self._business_hours_end_minutes = _minutes_from_hhmm(self._business_hours_end)

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        clock: Clock | None = None,
    ) -> ClinicTimeService:
        return cls(
            clinic_name=settings.clinic_name,
            timezone=settings.clinic_timezone,
            business_days=settings.clinic_business_days,
            business_hours_start=settings.clinic_business_hours_start,
            business_hours_end=settings.clinic_business_hours_end,
            clock=clock,
        )

    def clinic_now(self) -> datetime:
        return self._clock.now().astimezone(self._timezone)

    def clinic_today(self) -> date:
        return self.clinic_now().date()

    @property
    def timezone(self) -> ZoneInfo:
        return self._timezone

    def is_within_business_hours(self, time_value: str) -> bool:
        return self._is_within_business_hours(time_value)

    def get_current_clinic_context(self) -> ClinicContext:
        today = self.clinic_today()
        return ClinicContext(
            clinic_name=self._clinic_name,
            clinic_timezone=self._timezone_name,
            current_date=today.isoformat(),
            current_weekday=_INDEX_TO_WEEKDAY_NAME[today.weekday()],
            business_days=self._ordered_business_days(),
            business_hours_start=self._business_hours_start,
            business_hours_end=self._business_hours_end,
        )

    def _ordered_business_days(self) -> list[str]:
        return [day for day in _INDEX_TO_WEEKDAY_NAME if day in self._business_days]

    def resolve_date(self, expression: DateExpression) -> ResolvedDate:
        if expression.kind == DateExpressionKind.UNKNOWN:
            return ResolvedDate(
                status=DateResolutionStatus.UNKNOWN,
                reason="unknown_expression",
            )

        raw_date = self._resolve_raw_date(expression)
        if raw_date is None:
            return ResolvedDate(
                status=DateResolutionStatus.UNKNOWN,
                reason="invalid_expression",
            )

        today = self.clinic_today()
        if raw_date < today:
            return ResolvedDate(
                status=DateResolutionStatus.PAST_DATE,
                reason="past_date",
            )

        weekday_name = _INDEX_TO_WEEKDAY_NAME[raw_date.weekday()]
        if weekday_name not in self._business_days:
            return ResolvedDate(
                status=DateResolutionStatus.CLOSED_DAY,
                reason="closed_day",
            )

        return ResolvedDate(
            status=DateResolutionStatus.RESOLVED,
            resolved_date=raw_date,
        )

    def resolve_time_window(self, expression: TimeWindowExpression) -> ResolvedTimeWindow:
        if expression.kind == TimeWindowExpressionKind.UNKNOWN:
            return ResolvedTimeWindow(
                status=TimeWindowResolutionStatus.UNKNOWN,
                reason="unknown_expression",
            )

        if expression.kind == TimeWindowExpressionKind.EXACT_TIME:
            exact_time = (expression.exact_time or "").strip()
            if not exact_time:
                return ResolvedTimeWindow(
                    status=TimeWindowResolutionStatus.UNKNOWN,
                    reason="missing_exact_time",
                )
            if _HH_MM_PATTERN.fullmatch(exact_time) is None:
                return ResolvedTimeWindow(
                    status=TimeWindowResolutionStatus.UNKNOWN,
                    reason="invalid_exact_time",
                )
            if not self._is_within_business_hours(exact_time):
                return ResolvedTimeWindow(
                    status=TimeWindowResolutionStatus.OUTSIDE_BUSINESS_HOURS,
                    reason="outside_business_hours",
                )
            return ResolvedTimeWindow(
                status=TimeWindowResolutionStatus.RESOLVED,
                label="exact_time",
                exact_time=exact_time,
            )

        window = _STANDARD_TIME_WINDOWS.get(expression.kind)
        if window is None:
            return ResolvedTimeWindow(
                status=TimeWindowResolutionStatus.UNKNOWN,
                reason="invalid_expression",
            )

        label, start_time, end_time = window
        return ResolvedTimeWindow(
            status=TimeWindowResolutionStatus.RESOLVED,
            label=label,
            start_time=start_time,
            end_time=end_time,
        )

    def _resolve_raw_date(self, expression: DateExpression) -> date | None:
        today = self.clinic_today()

        if expression.kind == DateExpressionKind.TODAY:
            return today

        if expression.kind == DateExpressionKind.TOMORROW:
            return today + timedelta(days=1)

        if expression.kind == DateExpressionKind.NEXT_WEEK:
            start_of_week = today - timedelta(days=today.weekday())
            return start_of_week + timedelta(days=7)

        if expression.kind == DateExpressionKind.IN_N_DAYS:
            if expression.days_offset is None or expression.days_offset < 0:
                return None
            return today + timedelta(days=expression.days_offset)

        if expression.kind == DateExpressionKind.EXACT_DATE:
            return expression.exact_date

        if expression.kind in {
            DateExpressionKind.THIS_WEEKDAY,
            DateExpressionKind.NEXT_WEEKDAY,
        }:
            if expression.weekday is None:
                return None
            weekday_index = _WEEKDAY_TO_INDEX[expression.weekday]
            start_of_week = today - timedelta(days=today.weekday())
            if expression.kind == DateExpressionKind.THIS_WEEKDAY:
                return start_of_week + timedelta(days=weekday_index)
            return start_of_week + timedelta(days=7 + weekday_index)

        return None

    def _is_within_business_hours(self, time_value: str) -> bool:
        minutes = _minutes_from_hhmm(time_value)
        return self._business_hours_start_minutes <= minutes < self._business_hours_end_minutes


def _minutes_from_hhmm(value: str) -> int:
    hour, minute = map(int, value.split(":"))
    return hour * 60 + minute


def to_clinic_local_datetime(value: datetime, timezone: ZoneInfo) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(timezone)


def format_clinic_local_time_label(value: datetime, timezone: ZoneInfo) -> str:
    return to_clinic_local_datetime(value, timezone).strftime("%H:%M")


def format_clinic_local_slot_summary(value: datetime, timezone: ZoneInfo) -> str:
    localized = to_clinic_local_datetime(value, timezone)
    return f"{localized.strftime('%A')} at {localized.strftime('%H:%M')}"


def format_clinic_local_appointment_datetime(value: datetime, timezone: ZoneInfo) -> str:
    localized = to_clinic_local_datetime(value, timezone)
    date_part = f"{localized.strftime('%A')}, {localized.strftime('%B')} {localized.day}"
    return f"{date_part} at {localized.strftime('%H:%M')}"
