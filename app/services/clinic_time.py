from __future__ import annotations

import re
from datetime import date, datetime, timedelta
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


class ClinicTimeService:
    def __init__(
        self,
        *,
        timezone: str,
        business_days: str,
        business_hours_start: str,
        business_hours_end: str,
        clock: Clock | None = None,
    ) -> None:
        self._clock = clock or SystemClock()
        self._timezone = ZoneInfo(timezone)
        self._business_days = frozenset(
            day.strip().lower() for day in business_days.split(",") if day.strip()
        )
        self._business_hours_start = business_hours_start.strip()
        self._business_hours_end = business_hours_end.strip()
        self._business_hours_start_minutes = _minutes_from_hhmm(self._business_hours_start)
        self._business_hours_end_minutes = _minutes_from_hhmm(self._business_hours_end)

    def clinic_now(self) -> datetime:
        return self._clock.now().astimezone(self._timezone)

    def clinic_today(self) -> date:
        return self.clinic_now().date()

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
