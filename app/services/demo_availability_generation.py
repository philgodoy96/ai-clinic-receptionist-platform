from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.domain.scheduling.enums import AvailabilitySlotStatus
from app.models.scheduling import AvailabilitySlot, Doctor

DEMO_SLOT_START_TIMES: tuple[time, ...] = (
    time(10, 0),
    time(11, 0),
    time(14, 0),
    time(15, 0),
)
DEMO_SLOT_DURATION_MINUTES = 30
SEED_BUSINESS_DAY_COUNT = 7
_MAX_CALENDAR_SCAN_DAYS = 366

_WEEKDAY_NAME_TO_INDEX = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


@dataclass(frozen=True, slots=True)
class DemoAvailabilityGenerationResult:
    slots_created: int
    slots_existing: int
    horizon_days: int
    start_date: date
    end_date: date


def generate_demo_availability(
    session: Session,
    *,
    settings: Settings | None = None,
    clinic_now: datetime | None = None,
) -> DemoAvailabilityGenerationResult:
    resolved_settings = settings or get_settings()
    clinic_tz = ZoneInfo(resolved_settings.clinic_timezone)
    resolved_clinic_now = clinic_now or datetime.now(clinic_tz)
    if resolved_clinic_now.tzinfo is None:
        resolved_clinic_now = resolved_clinic_now.replace(tzinfo=clinic_tz)
    else:
        resolved_clinic_now = resolved_clinic_now.astimezone(clinic_tz)

    horizon_days = resolved_settings.scheduling_booking_horizon_days
    business_weekdays = parse_clinic_business_weekdays(resolved_settings.clinic_business_days)
    slot_dates = business_dates_through_horizon(
        clinic_now=resolved_clinic_now,
        business_weekdays=business_weekdays,
        slot_start_times=DEMO_SLOT_START_TIMES,
        horizon_days=horizon_days,
    )
    latest_bookable = resolved_clinic_now + timedelta(days=horizon_days)

    doctors = session.scalars(select(Doctor)).all()
    slots_created = 0
    slots_existing = 0

    for doctor in doctors:
        for slot_date in slot_dates:
            for slot_start in DEMO_SLOT_START_TIMES:
                start_time = clinic_local_slot_to_utc(
                    slot_date=slot_date,
                    slot_start=slot_start,
                    clinic_timezone=clinic_tz,
                )
                if start_time <= resolved_clinic_now.astimezone(UTC):
                    continue
                if start_time >= latest_bookable.astimezone(UTC):
                    continue

                end_time = start_time + timedelta(minutes=DEMO_SLOT_DURATION_MINUTES)

                existing = session.scalar(
                    select(AvailabilitySlot).where(
                        AvailabilitySlot.doctor_id == doctor.id,
                        AvailabilitySlot.start_time == start_time,
                    ),
                )

                if existing is None:
                    session.add(
                        AvailabilitySlot(
                            doctor_id=doctor.id,
                            start_time=start_time,
                            end_time=end_time,
                            status=AvailabilitySlotStatus.AVAILABLE,
                        ),
                    )
                    slots_created += 1
                else:
                    slots_existing += 1

    session.flush()

    start_date = resolved_clinic_now.date()
    end_date = latest_bookable.date()

    return DemoAvailabilityGenerationResult(
        slots_created=slots_created,
        slots_existing=slots_existing,
        horizon_days=horizon_days,
        start_date=start_date,
        end_date=end_date,
    )


def parse_clinic_business_weekdays(business_days: str) -> frozenset[int]:
    weekdays: set[int] = set()
    for day_name in business_days.split(","):
        normalized = day_name.strip().lower()
        if not normalized:
            continue
        weekdays.add(_WEEKDAY_NAME_TO_INDEX[normalized])
    return frozenset(weekdays)


def rolling_clinic_business_dates(
    *,
    clinic_now: datetime,
    business_weekdays: frozenset[int],
    slot_start_times: tuple[time, ...],
    business_day_count: int,
) -> list[date]:
    dates: list[date] = []
    cursor = clinic_now.date()

    for _ in range(_MAX_CALENDAR_SCAN_DAYS):
        if cursor.weekday() in business_weekdays:
            if cursor == clinic_now.date():
                if has_future_clinic_slot_on_date(
                    clinic_now=clinic_now,
                    slot_date=cursor,
                    slot_start_times=slot_start_times,
                ):
                    dates.append(cursor)
            else:
                dates.append(cursor)

        if len(dates) >= business_day_count:
            break

        cursor += timedelta(days=1)

    return dates


def business_dates_through_horizon(
    *,
    clinic_now: datetime,
    business_weekdays: frozenset[int],
    slot_start_times: tuple[time, ...],
    horizon_days: int,
) -> list[date]:
    dates: list[date] = []
    horizon_end_date = (clinic_now + timedelta(days=horizon_days)).date()
    cursor = clinic_now.date()

    for _ in range(_MAX_CALENDAR_SCAN_DAYS):
        if cursor > horizon_end_date:
            break

        if cursor.weekday() in business_weekdays:
            if cursor == clinic_now.date():
                if has_future_clinic_slot_on_date(
                    clinic_now=clinic_now,
                    slot_date=cursor,
                    slot_start_times=slot_start_times,
                ):
                    dates.append(cursor)
            else:
                dates.append(cursor)

        cursor += timedelta(days=1)

    return dates


def has_future_clinic_slot_on_date(
    *,
    clinic_now: datetime,
    slot_date: date,
    slot_start_times: tuple[time, ...],
) -> bool:
    clinic_tz = clinic_now.tzinfo
    if clinic_tz is None:
        return False

    for slot_start in slot_start_times:
        slot_start_utc = clinic_local_slot_to_utc(
            slot_date=slot_date,
            slot_start=slot_start,
            clinic_timezone=clinic_tz,
        )
        if slot_start_utc > clinic_now.astimezone(UTC):
            return True

    return False


def clinic_local_slot_to_utc(
    *,
    slot_date: date,
    slot_start: time,
    clinic_timezone: ZoneInfo | tzinfo,
) -> datetime:
    start_local = datetime.combine(slot_date, slot_start, tzinfo=clinic_timezone)
    return start_local.astimezone(UTC)
