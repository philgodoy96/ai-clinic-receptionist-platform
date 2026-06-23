from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.domain.scheduling.expressions import (
    DateExpression,
    DateExpressionKind,
    DateResolutionStatus,
    TimeWindowExpression,
    TimeWindowExpressionKind,
    TimeWindowResolutionStatus,
    Weekday,
)
from app.services.clinic_time import ClinicTimeService
from app.services.clock import FixedClock

REFERENCE_NOW_UTC = datetime(2026, 7, 1, 14, 0, tzinfo=UTC)


@pytest.fixture()
def clinic_time_service() -> ClinicTimeService:
    return ClinicTimeService(
        clinic_name="Demo Clinic",
        timezone="America/New_York",
        business_days="monday,tuesday,wednesday,thursday,friday",
        business_hours_start="09:00",
        business_hours_end="17:00",
        clock=FixedClock(current_time=REFERENCE_NOW_UTC),
    )


def test_tomorrow_across_fixed_clock(clinic_time_service: ClinicTimeService) -> None:
    result = clinic_time_service.resolve_date(
        DateExpression(kind=DateExpressionKind.TOMORROW),
    )

    assert result.status == DateResolutionStatus.RESOLVED
    assert result.resolved_date == date(2026, 7, 2)


def test_next_tuesday(clinic_time_service: ClinicTimeService) -> None:
    result = clinic_time_service.resolve_date(
        DateExpression(
            kind=DateExpressionKind.NEXT_WEEKDAY,
            weekday=Weekday.TUESDAY,
        ),
    )

    assert result.status == DateResolutionStatus.RESOLVED
    assert result.resolved_date == date(2026, 7, 7)


def test_this_tuesday(clinic_time_service: ClinicTimeService) -> None:
    result = clinic_time_service.resolve_date(
        DateExpression(
            kind=DateExpressionKind.THIS_WEEKDAY,
            weekday=Weekday.TUESDAY,
        ),
    )

    assert result.status == DateResolutionStatus.PAST_DATE
    assert result.reason == "past_date"


def test_exact_date(clinic_time_service: ClinicTimeService) -> None:
    result = clinic_time_service.resolve_date(
        DateExpression(
            kind=DateExpressionKind.EXACT_DATE,
            exact_date=date(2026, 7, 10),
        ),
    )

    assert result.status == DateResolutionStatus.RESOLVED
    assert result.resolved_date == date(2026, 7, 10)


def test_in_n_days(clinic_time_service: ClinicTimeService) -> None:
    result = clinic_time_service.resolve_date(
        DateExpression(
            kind=DateExpressionKind.IN_N_DAYS,
            days_offset=2,
        ),
    )

    assert result.status == DateResolutionStatus.RESOLVED
    assert result.resolved_date == date(2026, 7, 3)


def test_past_date_rejected(clinic_time_service: ClinicTimeService) -> None:
    result = clinic_time_service.resolve_date(
        DateExpression(
            kind=DateExpressionKind.EXACT_DATE,
            exact_date=date(2026, 6, 1),
        ),
    )

    assert result.status == DateResolutionStatus.PAST_DATE
    assert result.reason == "past_date"


def test_sunday_closed(clinic_time_service: ClinicTimeService) -> None:
    result = clinic_time_service.resolve_date(
        DateExpression(
            kind=DateExpressionKind.EXACT_DATE,
            exact_date=date(2026, 7, 5),
        ),
    )

    assert result.status == DateResolutionStatus.CLOSED_DAY
    assert result.reason == "closed_day"


def test_exact_time_outside_hours_rejected(clinic_time_service: ClinicTimeService) -> None:
    result = clinic_time_service.resolve_time_window(
        TimeWindowExpression(
            kind=TimeWindowExpressionKind.EXACT_TIME,
            exact_time="08:00",
        ),
    )

    assert result.status == TimeWindowResolutionStatus.OUTSIDE_BUSINESS_HOURS
    assert result.reason == "outside_business_hours"


def test_timezone_conversion_from_utc_to_america_new_york() -> None:
    service = ClinicTimeService(
        clinic_name="Demo Clinic",
        timezone="America/New_York",
        business_days="monday,tuesday,wednesday,thursday,friday",
        business_hours_start="09:00",
        business_hours_end="17:00",
        clock=FixedClock(current_time=datetime(2026, 7, 2, 3, 0, tzinfo=UTC)),
    )

    assert service.clinic_today() == date(2026, 7, 1)
    clinic_now = service.clinic_now()
    assert clinic_now.date() == date(2026, 7, 1)
    assert clinic_now.hour == 23
    assert clinic_now.minute == 0
