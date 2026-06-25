from __future__ import annotations

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from app.services.demo_availability_generation import (
    clinic_local_slot_to_utc,
    has_future_clinic_slot_on_date,
    parse_clinic_business_weekdays,
    rolling_clinic_business_dates,
)
from scripts.seed_demo_data import SEED_SLOT_START_TIMES


def test_parse_clinic_business_weekdays_defaults_to_weekdays_only() -> None:
    weekdays = parse_clinic_business_weekdays("monday,tuesday,wednesday,thursday,friday")

    assert weekdays == frozenset({0, 1, 2, 3, 4})


def test_rolling_business_dates_includes_today_when_future_slots_remain() -> None:
    clinic_tz = ZoneInfo("America/New_York")
    clinic_now = datetime(2026, 6, 24, 8, 0, tzinfo=clinic_tz)
    weekdays = parse_clinic_business_weekdays("monday,tuesday,wednesday,thursday,friday")

    dates = rolling_clinic_business_dates(
        clinic_now=clinic_now,
        business_weekdays=weekdays,
        slot_start_times=SEED_SLOT_START_TIMES,
        business_day_count=7,
    )

    assert dates[0] == date(2026, 6, 24)
    assert date(2026, 6, 27) not in dates
    assert date(2026, 6, 28) not in dates
    assert len(dates) == 7


def test_rolling_business_dates_skips_today_when_all_slots_are_past() -> None:
    clinic_tz = ZoneInfo("America/New_York")
    clinic_now = datetime(2026, 6, 24, 16, 0, tzinfo=clinic_tz)
    weekdays = parse_clinic_business_weekdays("monday,tuesday,wednesday,thursday,friday")

    dates = rolling_clinic_business_dates(
        clinic_now=clinic_now,
        business_weekdays=weekdays,
        slot_start_times=SEED_SLOT_START_TIMES,
        business_day_count=7,
    )

    assert dates[0] == date(2026, 6, 25)
    assert date(2026, 6, 24) not in dates
    assert len(dates) == 7


def test_rolling_business_dates_skips_weekends() -> None:
    clinic_tz = ZoneInfo("America/New_York")
    clinic_now = datetime(2026, 6, 27, 10, 0, tzinfo=clinic_tz)
    weekdays = parse_clinic_business_weekdays("monday,tuesday,wednesday,thursday,friday")

    dates = rolling_clinic_business_dates(
        clinic_now=clinic_now,
        business_weekdays=weekdays,
        slot_start_times=SEED_SLOT_START_TIMES,
        business_day_count=7,
    )

    assert all(day.weekday() < 5 for day in dates)
    assert dates[0] == date(2026, 6, 29)


def test_clinic_local_slot_to_utc_uses_clinic_timezone_not_utc_wall_clock() -> None:
    clinic_tz = ZoneInfo("America/New_York")
    winter_slot = clinic_local_slot_to_utc(
        slot_date=date(2026, 1, 15),
        slot_start=time(10, 0),
        clinic_timezone=clinic_tz,
    )
    summer_slot = clinic_local_slot_to_utc(
        slot_date=date(2026, 6, 24),
        slot_start=time(10, 0),
        clinic_timezone=clinic_tz,
    )

    assert winter_slot == datetime(2026, 1, 15, 15, 0, tzinfo=UTC)
    assert summer_slot == datetime(2026, 6, 24, 14, 0, tzinfo=UTC)


def test_has_future_clinic_slot_on_date_respects_clinic_local_times() -> None:
    clinic_tz = ZoneInfo("America/New_York")
    clinic_now = datetime(2026, 6, 24, 9, 30, tzinfo=clinic_tz)

    assert has_future_clinic_slot_on_date(
        clinic_now=clinic_now,
        slot_date=date(2026, 6, 24),
        slot_start_times=SEED_SLOT_START_TIMES,
    )

    clinic_now_late = datetime(2026, 6, 24, 16, 0, tzinfo=clinic_tz)

    assert not has_future_clinic_slot_on_date(
        clinic_now=clinic_now_late,
        slot_date=date(2026, 6, 24),
        slot_start_times=SEED_SLOT_START_TIMES,
    )
