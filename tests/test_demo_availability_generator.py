from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base_class import Base
from app.domain.scheduling.enums import AppointmentStatus, AvailabilitySlotStatus
from app.models.scheduling import Appointment, AvailabilitySlot, Doctor, Patient, Specialty
from app.services.demo_availability_generation import (
    DEMO_SLOT_START_TIMES,
    business_dates_through_horizon,
    generate_demo_availability,
    parse_clinic_business_weekdays,
)


@pytest.fixture()
def db_session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session_local = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    Base.metadata.create_all(bind=engine)

    with testing_session_local() as session:
        yield session

    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def _test_settings(*, horizon_days: int = 14) -> Settings:
    return Settings(
        SCHEDULING_BOOKING_HORIZON_DAYS=horizon_days,
        CLINIC_TIMEZONE="America/New_York",
        CLINIC_BUSINESS_DAYS="monday,tuesday,wednesday,thursday,friday",
        CLINIC_BUSINESS_HOURS_START="09:00",
        CLINIC_BUSINESS_HOURS_END="17:00",
    )


def _seed_doctor(db_session: Session) -> Doctor:
    specialty = Specialty(name="Dermatology", description="Skin care", is_active=True)
    db_session.add(specialty)
    db_session.flush()
    doctor = Doctor(
        specialty_id=specialty.id,
        full_name="Dr. Emily Carter",
        email="emily.carter@example-clinic.test",
        phone_number="+1-555-0101",
        is_active=True,
    )
    db_session.add(doctor)
    db_session.flush()
    return doctor


def test_generator_creates_future_slots_through_booking_horizon(
    db_session: Session,
) -> None:
    doctor = _seed_doctor(db_session)
    settings = _test_settings(horizon_days=14)
    clinic_tz = ZoneInfo(settings.clinic_timezone)
    clinic_now = datetime(2026, 6, 25, 8, 0, tzinfo=clinic_tz)

    result = generate_demo_availability(
        db_session,
        settings=settings,
        clinic_now=clinic_now,
    )

    assert result.horizon_days == 14
    assert result.start_date == date(2026, 6, 25)
    assert result.end_date == date(2026, 7, 9)
    assert result.slots_created > 0

    latest_bookable = clinic_now + timedelta(days=14)
    future_slots = db_session.scalars(
        select(AvailabilitySlot).where(
            AvailabilitySlot.doctor_id == doctor.id,
            AvailabilitySlot.start_time >= clinic_now.astimezone(UTC),
            AvailabilitySlot.start_time < latest_bookable.astimezone(UTC),
        ),
    ).all()
    assert future_slots

    expected_dates = business_dates_through_horizon(
        clinic_now=clinic_now,
        business_weekdays=parse_clinic_business_weekdays(settings.clinic_business_days),
        slot_start_times=DEMO_SLOT_START_TIMES,
        horizon_days=14,
    )
    assert expected_dates[-1] <= result.end_date


def test_generator_is_idempotent(db_session: Session) -> None:
    _seed_doctor(db_session)
    settings = _test_settings()
    clinic_now = datetime(2026, 6, 25, 8, 0, tzinfo=ZoneInfo(settings.clinic_timezone))

    first = generate_demo_availability(db_session, settings=settings, clinic_now=clinic_now)
    second = generate_demo_availability(db_session, settings=settings, clinic_now=clinic_now)

    assert first.slots_created > 0
    assert second.slots_created == 0
    assert second.slots_existing == first.slots_created

    slot_count = db_session.scalar(select(func.count()).select_from(AvailabilitySlot))
    assert slot_count == first.slots_created


def test_generator_does_not_overwrite_booked_slots(db_session: Session) -> None:
    doctor = _seed_doctor(db_session)
    patient = Patient(
        full_name="John Miller",
        date_of_birth=date(1985, 4, 12),
        phone_number="+1-555-0201",
        email="john.miller@example.test",
    )
    db_session.add(patient)
    db_session.flush()
    settings = _test_settings()
    clinic_tz = ZoneInfo(settings.clinic_timezone)
    clinic_now = datetime(2026, 6, 25, 8, 0, tzinfo=clinic_tz)
    booked_start = datetime(2026, 6, 25, 14, 0, tzinfo=UTC)
    booked_slot = AvailabilitySlot(
        doctor_id=doctor.id,
        start_time=booked_start,
        end_time=booked_start + timedelta(minutes=30),
        status=AvailabilitySlotStatus.BOOKED,
    )
    db_session.add(booked_slot)
    db_session.flush()

    appointment = Appointment(
        patient_id=patient.id,
        doctor_id=doctor.id,
        specialty_id=doctor.specialty_id,
        availability_slot_id=booked_slot.id,
        start_time=booked_start,
        end_time=booked_start + timedelta(minutes=30),
        status=AppointmentStatus.SCHEDULED,
        reason="Existing visit",
    )
    db_session.add(appointment)
    db_session.flush()

    generate_demo_availability(db_session, settings=settings, clinic_now=clinic_now)

    refreshed_slot = db_session.get(AvailabilitySlot, booked_slot.id)
    refreshed_appointment = db_session.get(Appointment, appointment.id)
    assert refreshed_slot is not None
    assert refreshed_slot.status == AvailabilitySlotStatus.BOOKED
    assert refreshed_appointment is not None
    assert refreshed_appointment.status == AppointmentStatus.SCHEDULED


def test_generator_respects_business_days(db_session: Session) -> None:
    doctor = _seed_doctor(db_session)
    settings = _test_settings()
    clinic_now = datetime(2026, 6, 27, 10, 0, tzinfo=ZoneInfo(settings.clinic_timezone))

    generate_demo_availability(db_session, settings=settings, clinic_now=clinic_now)

    slots = db_session.scalars(
        select(AvailabilitySlot).where(AvailabilitySlot.doctor_id == doctor.id),
    ).all()
    clinic_tz = ZoneInfo(settings.clinic_timezone)
    for slot in slots:
        local_start = slot.start_time.astimezone(clinic_tz)
        assert local_start.weekday() < 5


def test_generator_respects_business_hours(db_session: Session) -> None:
    doctor = _seed_doctor(db_session)
    settings = _test_settings()
    clinic_now = datetime(2026, 6, 25, 8, 0, tzinfo=ZoneInfo(settings.clinic_timezone))

    generate_demo_availability(db_session, settings=settings, clinic_now=clinic_now)

    slots = db_session.scalars(
        select(AvailabilitySlot).where(AvailabilitySlot.doctor_id == doctor.id),
    ).all()
    clinic_tz = ZoneInfo(settings.clinic_timezone)
    allowed_start_times = set(DEMO_SLOT_START_TIMES)
    for slot in slots:
        local_start = slot.start_time
        if local_start.tzinfo is None:
            local_start = local_start.replace(tzinfo=UTC)
        assert local_start.astimezone(clinic_tz).time() in allowed_start_times
