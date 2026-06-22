from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.db.base  # noqa: F401
from app.db.base_class import Base
from app.domain.appointment_rescheduling import (
    AppointmentReschedulingFailureCode,
    AppointmentReschedulingInvalidIdempotencyKeyError,
    AppointmentReschedulingMissingConfirmationError,
    AppointmentReschedulingRequest,
    is_appointment_reschedulable,
    normalize_rescheduling_reason,
    validate_appointment_rescheduling_request,
)
from app.domain.appointments import is_appointment_cancelable
from app.domain.scheduling.enums import AppointmentStatus
from app.models.appointment_reschedule_attempt import AppointmentRescheduleAttempt
from app.models.scheduling import Appointment, Doctor, Patient, Specialty


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


def _build_request(
    *,
    explicit_confirmation: bool = True,
    idempotency_key: str = "reschedule-attempt-1",
) -> AppointmentReschedulingRequest:
    return AppointmentReschedulingRequest(
        appointment_id=uuid4(),
        availability_slot_id=uuid4(),
        explicit_confirmation=explicit_confirmation,
        idempotency_key=idempotency_key,
        rescheduling_reason="Patient requested a new time",
    )


def _seed_appointment(
    db_session: Session,
    *,
    status: AppointmentStatus = AppointmentStatus.SCHEDULED,
    suffix: str = "1",
) -> Appointment:
    specialty = Specialty(
        id=uuid4(),
        name=f"Dermatology {suffix}",
        description="Skin care",
        is_active=True,
    )
    doctor = Doctor(
        id=uuid4(),
        specialty_id=specialty.id,
        full_name=f"Dr. Emily Carter {suffix}",
        email=f"emily.carter.{suffix}@example-clinic.test",
        phone_number=f"+1-555-010{suffix}",
        is_active=True,
    )
    patient = Patient(
        id=uuid4(),
        full_name=f"John Miller {suffix}",
        date_of_birth=date(1985, 4, 12),
        phone_number=f"+1-555-020{suffix}",
        email=f"john.miller.{suffix}@example.test",
    )
    start_time = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    appointment = Appointment(
        id=uuid4(),
        patient_id=patient.id,
        doctor_id=doctor.id,
        specialty_id=specialty.id,
        start_time=start_time,
        end_time=start_time + timedelta(minutes=30),
        status=status,
    )

    db_session.add_all([specialty, doctor, patient, appointment])
    db_session.commit()
    return appointment


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (AppointmentStatus.SCHEDULED, True),
        (AppointmentStatus.RESCHEDULED, True),
        (AppointmentStatus.CANCELLED, False),
        (AppointmentStatus.COMPLETED, False),
    ],
)
def test_appointment_status_reschedulable_support(
    status: AppointmentStatus,
    expected: bool,
) -> None:
    assert is_appointment_reschedulable(status) is expected


def test_validate_request_accepts_valid_request() -> None:
    validate_appointment_rescheduling_request(_build_request())


def test_validate_request_requires_confirmation() -> None:
    with pytest.raises(AppointmentReschedulingMissingConfirmationError) as exc_info:
        validate_appointment_rescheduling_request(
            _build_request(explicit_confirmation=False),
        )

    assert (
        exc_info.value.failure_code
        == AppointmentReschedulingFailureCode.MISSING_CONFIRMATION
    )


def test_validate_request_requires_idempotency_key() -> None:
    with pytest.raises(AppointmentReschedulingInvalidIdempotencyKeyError) as exc_info:
        validate_appointment_rescheduling_request(
            _build_request(idempotency_key="   "),
        )

    assert (
        exc_info.value.failure_code
        == AppointmentReschedulingFailureCode.INVALID_IDEMPOTENCY_KEY
    )


def test_normalize_rescheduling_reason_trims_and_rejects_blank() -> None:
    assert normalize_rescheduling_reason("  Patient moved  ") == "Patient moved"
    assert normalize_rescheduling_reason("   ") is None
    assert normalize_rescheduling_reason(None) is None


def test_active_appointment_statuses_remain_cancelable_regression() -> None:
    assert is_appointment_cancelable(AppointmentStatus.SCHEDULED) is True
    assert is_appointment_cancelable(AppointmentStatus.RESCHEDULED) is True
    assert is_appointment_cancelable(AppointmentStatus.CANCELLED) is False
    assert is_appointment_cancelable(AppointmentStatus.COMPLETED) is False


def test_reschedulable_statuses_match_cancelable_active_statuses_regression() -> None:
    for status in AppointmentStatus:
        assert is_appointment_reschedulable(status) == is_appointment_cancelable(status)


def test_reschedule_attempt_idempotency_key_must_be_unique(
    db_session: Session,
) -> None:
    original = _seed_appointment(db_session, suffix="original")
    replacement = _seed_appointment(db_session, suffix="replacement")
    shared_key = "reschedule-dup-1"

    db_session.add(
        AppointmentRescheduleAttempt(
            idempotency_key=shared_key,
            appointment_id=original.id,
            new_appointment_id=replacement.id,
        ),
    )
    db_session.commit()

    db_session.add(
        AppointmentRescheduleAttempt(
            idempotency_key=shared_key,
            appointment_id=original.id,
            new_appointment_id=replacement.id,
        ),
    )
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_existing_appointment_model_persists_after_reschedule_attempt_table_exists(
    db_session: Session,
) -> None:
    appointment = _seed_appointment(db_session, status=AppointmentStatus.SCHEDULED)

    persisted = db_session.get(Appointment, appointment.id)
    assert persisted is not None
    assert persisted.status == AppointmentStatus.SCHEDULED
    assert persisted.rescheduled_from_appointment_id is None
