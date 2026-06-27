from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base_class import Base
from app.domain.scheduling.enums import AppointmentStatus, AvailabilitySlotStatus
from app.models.scheduling import Appointment, AvailabilitySlot, Doctor, Patient, Specialty
from app.repositories.sqlalchemy.scheduling import (
    SQLAlchemyAppointmentRepository,
    SQLAlchemyAvailabilitySlotRepository,
    SQLAlchemyDoctorRepository,
    SQLAlchemyPatientRepository,
    SQLAlchemySpecialtyRepository,
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


def test_specialty_repository_lists_active_specialties(db_session: Session) -> None:
    dermatology = Specialty(name="Dermatology", description="Skin care", is_active=True)
    inactive = Specialty(name="Pediatrics", description="Children care", is_active=False)
    db_session.add_all([dermatology, inactive])
    db_session.commit()

    repository = SQLAlchemySpecialtyRepository(db_session)

    specialties = repository.list_active()

    assert [specialty.name for specialty in specialties] == ["Dermatology"]


def test_doctor_repository_filters_active_doctors_by_specialty(db_session: Session) -> None:
    dermatology = Specialty(name="Dermatology", description="Skin care", is_active=True)
    cardiology = Specialty(name="Cardiology", description="Heart care", is_active=True)
    db_session.add_all([dermatology, cardiology])
    db_session.flush()

    dermatologist = Doctor(
        specialty_id=dermatology.id,
        full_name="Dr. Emily Carter",
        email="emily.carter@example-clinic.test",
        phone_number="+1-555-0101",
        is_active=True,
    )
    cardiologist = Doctor(
        specialty_id=cardiology.id,
        full_name="Dr. Michael Reed",
        email="michael.reed@example-clinic.test",
        phone_number="+1-555-0102",
        is_active=True,
    )
    inactive_doctor = Doctor(
        specialty_id=dermatology.id,
        full_name="Dr. Inactive",
        email="inactive@example-clinic.test",
        phone_number="+1-555-0199",
        is_active=False,
    )
    db_session.add_all([dermatologist, cardiologist, inactive_doctor])
    db_session.commit()

    repository = SQLAlchemyDoctorRepository(db_session)

    doctors = repository.list_active(specialty_id=dermatology.id)

    assert [doctor.full_name for doctor in doctors] == ["Dr. Emily Carter"]


def test_patient_repository_requires_sufficient_identity(db_session: Session) -> None:
    patient = Patient(
        full_name="John Miller",
        date_of_birth=date(1985, 4, 12),
        phone_number="+1-555-0201",
        email="john.miller@example.test",
    )
    db_session.add(patient)
    db_session.commit()

    repository = SQLAlchemyPatientRepository(db_session)

    assert (
        repository.get_by_identity(
            full_name="John Miller",
            date_of_birth=date(1985, 4, 12),
        )
        is None
    )

    found = repository.get_by_identity(
        full_name="John Miller",
        date_of_birth=date(1985, 4, 12),
        phone_number="+1-555-0201",
    )

    assert found is not None
    assert found.email == "john.miller@example.test"

    normalized_match = repository.get_by_identity(
        full_name="John Miller",
        date_of_birth=date(1985, 4, 12),
        phone_number="15550201",
        email="john.miller@example.test",
    )

    assert normalized_match is not None
    assert normalized_match.id == found.id


def test_availability_repository_lists_available_slots(db_session: Session) -> None:
    doctor = create_doctor(db_session)
    start_time = datetime(2026, 7, 1, 14, 0, tzinfo=UTC)
    available_slot = AvailabilitySlot(
        doctor_id=doctor.id,
        start_time=start_time,
        end_time=start_time + timedelta(minutes=30),
        status=AvailabilitySlotStatus.AVAILABLE,
    )
    blocked_slot = AvailabilitySlot(
        doctor_id=doctor.id,
        start_time=start_time + timedelta(hours=1),
        end_time=start_time + timedelta(hours=1, minutes=30),
        status=AvailabilitySlotStatus.BLOCKED,
    )
    db_session.add_all([available_slot, blocked_slot])
    db_session.commit()

    repository = SQLAlchemyAvailabilitySlotRepository(db_session)

    slots = repository.list_available(
        doctor_id=doctor.id,
        start_from=start_time - timedelta(minutes=1),
        start_to=start_time + timedelta(hours=2),
    )

    assert [slot.id for slot in slots] == [available_slot.id]


def test_appointment_repository_finds_scheduled_conflict(db_session: Session) -> None:
    doctor = create_doctor(db_session)
    patient = create_patient(db_session)
    start_time = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    scheduled = Appointment(
        patient_id=patient.id,
        doctor_id=doctor.id,
        specialty_id=doctor.specialty_id,
        start_time=start_time,
        end_time=start_time + timedelta(minutes=30),
        status=AppointmentStatus.SCHEDULED,
        reason="Annual checkup",
    )
    cancelled = Appointment(
        patient_id=patient.id,
        doctor_id=doctor.id,
        specialty_id=doctor.specialty_id,
        start_time=start_time + timedelta(hours=1),
        end_time=start_time + timedelta(hours=1, minutes=30),
        status=AppointmentStatus.CANCELLED,
        reason="Follow-up",
        cancellation_reason="Patient cancelled",
    )
    db_session.add_all([scheduled, cancelled])
    db_session.commit()

    repository = SQLAlchemyAppointmentRepository(db_session)

    conflict = repository.find_scheduled_conflict(
        doctor_id=doctor.id,
        start_time=start_time,
    )
    no_conflict = repository.find_scheduled_conflict(
        doctor_id=doctor.id,
        start_time=start_time + timedelta(hours=1),
    )

    assert conflict is not None
    assert conflict.id == scheduled.id
    assert no_conflict is None


def test_appointment_repository_lists_upcoming_patient_appointments(db_session: Session) -> None:
    doctor = create_doctor(db_session)
    patient = create_patient(db_session)
    start_time = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    scheduled = Appointment(
        patient_id=patient.id,
        doctor_id=doctor.id,
        specialty_id=doctor.specialty_id,
        start_time=start_time,
        end_time=start_time + timedelta(minutes=30),
        status=AppointmentStatus.SCHEDULED,
        reason="Annual checkup",
    )
    cancelled = Appointment(
        patient_id=patient.id,
        doctor_id=doctor.id,
        specialty_id=doctor.specialty_id,
        start_time=start_time + timedelta(hours=1),
        end_time=start_time + timedelta(hours=1, minutes=30),
        status=AppointmentStatus.CANCELLED,
        reason="Follow-up",
        cancellation_reason="Patient cancelled",
    )
    db_session.add_all([scheduled, cancelled])
    db_session.commit()

    repository = SQLAlchemyAppointmentRepository(db_session)

    appointments = repository.list_upcoming_for_patient(
        patient_id=patient.id,
        start_from=start_time - timedelta(minutes=1),
    )

    assert [appointment.id for appointment in appointments] == [scheduled.id]


def test_appointment_repository_lists_cancelable_patient_appointments(db_session: Session) -> None:
    doctor = create_doctor(db_session)
    patient = create_patient(db_session)
    start_time = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    scheduled = Appointment(
        patient_id=patient.id,
        doctor_id=doctor.id,
        specialty_id=doctor.specialty_id,
        start_time=start_time + timedelta(days=7),
        end_time=start_time + timedelta(days=7, minutes=30),
        status=AppointmentStatus.SCHEDULED,
        reason="Annual checkup",
    )
    rescheduled = Appointment(
        patient_id=patient.id,
        doctor_id=doctor.id,
        specialty_id=doctor.specialty_id,
        start_time=start_time + timedelta(days=8),
        end_time=start_time + timedelta(days=8, minutes=30),
        status=AppointmentStatus.RESCHEDULED,
        reason="Follow-up",
    )
    cancelled = Appointment(
        patient_id=patient.id,
        doctor_id=doctor.id,
        specialty_id=doctor.specialty_id,
        start_time=start_time + timedelta(days=9),
        end_time=start_time + timedelta(days=9, minutes=30),
        status=AppointmentStatus.CANCELLED,
        reason="Past visit",
        cancellation_reason="Patient cancelled",
    )
    db_session.add_all([scheduled, rescheduled, cancelled])
    db_session.commit()

    repository = SQLAlchemyAppointmentRepository(db_session)

    appointments = repository.list_cancelable_for_patient(
        patient_id=patient.id,
        start_from=start_time - timedelta(minutes=1),
    )

    assert [appointment.id for appointment in appointments] == [scheduled.id, rescheduled.id]


def test_list_reschedulable_for_patient_includes_scheduled_excludes_rescheduled_and_cancelled(
    db_session: Session,
) -> None:
    doctor = create_doctor(db_session)
    patient = create_patient(db_session)
    start_time = datetime(2026, 7, 1, 14, 0, tzinfo=UTC)
    scheduled = Appointment(
        patient_id=patient.id,
        doctor_id=doctor.id,
        specialty_id=doctor.specialty_id,
        start_time=start_time + timedelta(days=7),
        end_time=start_time + timedelta(days=7, minutes=30),
        status=AppointmentStatus.SCHEDULED,
        reason="Annual checkup",
    )
    rescheduled = Appointment(
        patient_id=patient.id,
        doctor_id=doctor.id,
        specialty_id=doctor.specialty_id,
        start_time=start_time + timedelta(days=8),
        end_time=start_time + timedelta(days=8, minutes=30),
        status=AppointmentStatus.RESCHEDULED,
        reason="Follow-up",
    )
    cancelled = Appointment(
        patient_id=patient.id,
        doctor_id=doctor.id,
        specialty_id=doctor.specialty_id,
        start_time=start_time + timedelta(days=9),
        end_time=start_time + timedelta(days=9, minutes=30),
        status=AppointmentStatus.CANCELLED,
        reason="Past visit",
        cancellation_reason="Patient cancelled",
    )
    db_session.add_all([scheduled, rescheduled, cancelled])
    db_session.commit()

    repository = SQLAlchemyAppointmentRepository(db_session)

    appointments = repository.list_reschedulable_for_patient(
        patient_id=patient.id,
        start_from=start_time - timedelta(minutes=1),
    )

    assert [appointment.id for appointment in appointments] == [scheduled.id]


def create_doctor(session: Session) -> Doctor:
    specialty = Specialty(name="Primary Care", description="General care", is_active=True)
    session.add(specialty)
    session.flush()

    doctor = Doctor(
        specialty_id=specialty.id,
        full_name="Dr. Sarah Mitchell",
        email="sarah.mitchell@example-clinic.test",
        phone_number="+1-555-0103",
        is_active=True,
    )
    session.add(doctor)
    session.flush()

    return doctor


def create_patient(session: Session) -> Patient:
    patient = Patient(
        full_name="Ava Thompson",
        date_of_birth=date(1992, 9, 3),
        phone_number="+1-555-0202",
        email="ava.thompson@example.test",
    )
    session.add(patient)
    session.flush()

    return patient
