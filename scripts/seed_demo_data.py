from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta, tzinfo
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.session import SessionLocal
from app.domain.scheduling.enums import AvailabilitySlotStatus
from app.models.scheduling import AvailabilitySlot, Doctor, Patient, Specialty

SEED_SLOT_START_TIMES: tuple[time, ...] = (
    time(10, 0),
    time(11, 0),
    time(14, 0),
    time(15, 0),
)
SEED_BUSINESS_DAY_COUNT = 7
_MAX_SEED_CALENDAR_SCAN_DAYS = 21

_WEEKDAY_NAME_TO_INDEX = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def main() -> None:
    with SessionLocal() as session:
        seed_specialties(session)
        session.flush()

        seed_doctors(session)
        session.flush()

        seed_patients(session)
        session.flush()

        seed_availability_slots(session)
        session.commit()

    print("Demo scheduling data seeded successfully.")


def seed_specialties(session: Session) -> None:
    specialties = [
        (
            "Dermatology",
            "Skin, hair, and nail care for common dermatology concerns.",
        ),
        (
            "Cardiology",
            "Heart health consultations and cardiovascular follow-up visits.",
        ),
        (
            "Primary Care",
            "General health visits, preventive care, and routine follow-ups.",
        ),
    ]

    for name, description in specialties:
        existing = session.scalar(select(Specialty).where(Specialty.name == name))

        if existing is None:
            session.add(Specialty(name=name, description=description))


def seed_doctors(session: Session) -> None:
    doctors = [
        {
            "full_name": "Dr. Emily Carter",
            "email": "emily.carter@example-clinic.test",
            "phone_number": "+1-555-0101",
            "specialty": "Dermatology",
        },
        {
            "full_name": "Dr. Michael Reed",
            "email": "michael.reed@example-clinic.test",
            "phone_number": "+1-555-0102",
            "specialty": "Cardiology",
        },
        {
            "full_name": "Dr. Sarah Mitchell",
            "email": "sarah.mitchell@example-clinic.test",
            "phone_number": "+1-555-0103",
            "specialty": "Primary Care",
        },
    ]

    for doctor_data in doctors:
        existing = session.scalar(
            select(Doctor).where(Doctor.email == doctor_data["email"]),
        )

        if existing is not None:
            continue

        specialty = session.scalar(
            select(Specialty).where(Specialty.name == doctor_data["specialty"]),
        )

        if specialty is None:
            raise RuntimeError(f"Missing specialty: {doctor_data['specialty']}")

        session.add(
            Doctor(
                full_name=doctor_data["full_name"],
                email=doctor_data["email"],
                phone_number=doctor_data["phone_number"],
                specialty_id=specialty.id,
            ),
        )


def seed_patients(session: Session) -> None:
    patients = [
        {
            "full_name": "John Miller",
            "date_of_birth": date(1985, 4, 12),
            "phone_number": "+1-555-0201",
            "email": "john.miller@example.test",
        },
        {
            "full_name": "Ava Thompson",
            "date_of_birth": date(1992, 9, 3),
            "phone_number": "+1-555-0202",
            "email": "ava.thompson@example.test",
        },
    ]

    for patient_data in patients:
        existing = session.scalar(
            select(Patient).where(Patient.email == patient_data["email"]),
        )

        if existing is None:
            session.add(Patient(**patient_data))


def seed_availability_slots(
    session: Session,
    *,
    settings: Settings | None = None,
    clinic_now: datetime | None = None,
) -> None:
    resolved_settings = settings or get_settings()
    clinic_tz = ZoneInfo(resolved_settings.clinic_timezone)
    resolved_clinic_now = clinic_now or datetime.now(clinic_tz)
    if resolved_clinic_now.tzinfo is None:
        resolved_clinic_now = resolved_clinic_now.replace(tzinfo=clinic_tz)
    else:
        resolved_clinic_now = resolved_clinic_now.astimezone(clinic_tz)

    business_weekdays = parse_clinic_business_weekdays(resolved_settings.clinic_business_days)
    slot_dates = rolling_clinic_business_dates(
        clinic_now=resolved_clinic_now,
        business_weekdays=business_weekdays,
        slot_start_times=SEED_SLOT_START_TIMES,
        business_day_count=SEED_BUSINESS_DAY_COUNT,
    )

    doctors = session.scalars(select(Doctor)).all()

    for doctor in doctors:
        for slot_date in slot_dates:
            for slot_start in SEED_SLOT_START_TIMES:
                start_time = clinic_local_slot_to_utc(
                    slot_date=slot_date,
                    slot_start=slot_start,
                    clinic_timezone=clinic_tz,
                )
                if start_time <= datetime.now(UTC):
                    continue

                end_time = start_time + timedelta(minutes=30)

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

    for _ in range(_MAX_SEED_CALENDAR_SCAN_DAYS):
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


if __name__ == "__main__":
    main()
