from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.db.base  # noqa: F401
from app.db.base_class import Base
from app.domain.appointment_rescheduling_enums import AppointmentRescheduleAttemptStatus
from app.domain.audit.appointment_rescheduling import (
    build_safe_reschedule_audit_metadata,
    sanitize_reschedule_audit_metadata,
)
from app.models.appointment_reschedule_attempt import AppointmentRescheduleAttempt
from app.models.scheduling import Appointment, Doctor, Patient, Specialty
from app.repositories.sqlalchemy.appointments import (
    SQLAlchemyAppointmentRescheduleAttemptRepository,
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

    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(bind=engine)

    with testing_session_local() as session:
        yield session

    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def _seed_appointment(db_session: Session) -> Appointment:
    specialty = Specialty(
        id=uuid4(),
        name="Dermatology",
        description="Skin care",
        is_active=True,
    )
    doctor = Doctor(
        id=uuid4(),
        specialty_id=specialty.id,
        full_name="Dr. Emily Carter",
        email="emily.carter@example-clinic.test",
        phone_number="+1-555-0101",
        is_active=True,
    )
    patient = Patient(
        id=uuid4(),
        full_name="John Miller",
        date_of_birth=date(1985, 4, 12),
        phone_number="+1-555-0201",
        email="john.miller@example.test",
    )
    start_time = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    appointment = Appointment(
        id=uuid4(),
        patient_id=patient.id,
        doctor_id=doctor.id,
        specialty_id=specialty.id,
        start_time=start_time,
        end_time=start_time + timedelta(minutes=30),
    )

    db_session.add_all([specialty, doctor, patient, appointment])
    db_session.commit()
    return appointment


def test_create_attempt_persists_pending_attempt(db_session: Session) -> None:
    appointment = _seed_appointment(db_session)
    repository = SQLAlchemyAppointmentRescheduleAttemptRepository(db_session)

    result = repository.create_attempt(
        idempotency_key="reschedule-repo-1",
        appointment_id=appointment.id,
    )

    assert result.created is True
    assert result.attempt.status == AppointmentRescheduleAttemptStatus.PENDING
    assert result.attempt.appointment_id == appointment.id


def test_create_attempt_returns_existing_on_duplicate_key(db_session: Session) -> None:
    appointment = _seed_appointment(db_session)
    repository = SQLAlchemyAppointmentRescheduleAttemptRepository(db_session)

    first = repository.create_attempt(
        idempotency_key="reschedule-repo-dup",
        appointment_id=appointment.id,
    )
    second = repository.create_attempt(
        idempotency_key="reschedule-repo-dup",
        appointment_id=appointment.id,
    )

    assert first.created is True
    assert second.created is False
    assert second.attempt.id == first.attempt.id


def test_mark_succeeded_updates_attempt(db_session: Session) -> None:
    appointment = _seed_appointment(db_session)
    successor = Appointment(
        id=uuid4(),
        patient_id=appointment.patient_id,
        doctor_id=appointment.doctor_id,
        specialty_id=appointment.specialty_id,
        start_time=datetime(2026, 7, 2, 14, 0, tzinfo=UTC),
        end_time=datetime(2026, 7, 2, 14, 30, tzinfo=UTC),
        rescheduled_from_appointment_id=appointment.id,
    )
    db_session.add(successor)
    db_session.commit()

    repository = SQLAlchemyAppointmentRescheduleAttemptRepository(db_session)
    attempt = repository.create_attempt(
        idempotency_key="reschedule-repo-success",
        appointment_id=appointment.id,
    ).attempt

    updated = repository.mark_succeeded(
        attempt,
        new_appointment_id=successor.id,
    )
    db_session.commit()

    persisted = db_session.get(AppointmentRescheduleAttempt, updated.id)
    assert persisted is not None
    assert persisted.status == AppointmentRescheduleAttemptStatus.SUCCEEDED
    assert persisted.new_appointment_id == successor.id
    assert persisted.error_code is None


def test_mark_failed_or_rejected_persists_error_code(db_session: Session) -> None:
    appointment = _seed_appointment(db_session)
    repository = SQLAlchemyAppointmentRescheduleAttemptRepository(db_session)
    attempt = repository.create_attempt(
        idempotency_key="reschedule-repo-reject",
        appointment_id=appointment.id,
    ).attempt

    updated = repository.mark_failed_or_rejected(
        attempt,
        error_code="slot_unavailable",
    )
    db_session.commit()

    persisted = db_session.get(AppointmentRescheduleAttempt, updated.id)
    assert persisted is not None
    assert persisted.status == AppointmentRescheduleAttemptStatus.REJECTED
    assert persisted.error_code == "slot_unavailable"


def test_unique_constraint_race_returns_existing_attempt(db_session: Session) -> None:
    appointment = _seed_appointment(db_session)
    repository = SQLAlchemyAppointmentRescheduleAttemptRepository(db_session)
    shared_key = "reschedule-repo-race"

    db_session.add(
        AppointmentRescheduleAttempt(
            idempotency_key=shared_key,
            appointment_id=appointment.id,
            status=AppointmentRescheduleAttemptStatus.PENDING,
        ),
    )
    db_session.commit()

    result = repository.create_attempt(
        idempotency_key=shared_key,
        appointment_id=appointment.id,
    )

    assert result.created is False
    assert result.attempt.idempotency_key == shared_key


def test_safe_reschedule_audit_metadata_excludes_sensitive_fields() -> None:
    metadata = build_safe_reschedule_audit_metadata(
        idempotency_key="reschedule-audit-1",
        original_appointment_id=uuid4(),
        new_appointment_id=uuid4(),
        extra={
            "transcript": "secret transcript",
            "email": "patient@example.test",
            "duplicate": True,
        },
    )

    assert metadata["duplicate"] is True
    assert "transcript" not in metadata
    assert "email" not in metadata


def test_sanitize_reschedule_audit_metadata_filters_unknown_keys() -> None:
    metadata = sanitize_reschedule_audit_metadata(
        {
            "idempotency_key": "reschedule-audit-2",
            "api_key": "secret",
            "unexpected_field": "value",
        },
    )

    assert metadata == {"idempotency_key": "reschedule-audit-2"}
