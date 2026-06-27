from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.domain.appointments import (
    AppointmentCancellationMissingConfirmationError,
    AppointmentCancellationRequest,
    AppointmentNotCancelableError,
    AppointmentNotFoundError,
)
from app.domain.audit.enums import AuditEventOutcome, AuditEventType
from app.domain.scheduling.enums import AppointmentStatus, AvailabilitySlotStatus
from app.models.appointment_cancellation_attempt import AppointmentCancellationAttempt
from app.models.scheduling import Appointment, Doctor, Patient, Specialty
from app.services.appointment_cancellation import AppointmentCancellationService
from app.services.audit_logs import AuditLogService
from tests.test_appointment_booking_api import FakeAuditLogService
from tests.test_appointment_booking_service import (
    FakeAppointmentRepository,
    FakeAvailabilitySlotRepository,
)


@dataclass
class CancellationContext:
    service: AppointmentCancellationService
    appointment_repository: FakeAppointmentRepository
    attempt_repository: FakeAppointmentCancellationAttemptRepository
    audit_logs: FakeAuditLogService
    appointment: Appointment
    availability_slot_repository: FakeAvailabilitySlotRepository | None = None


class FakeAppointmentCancellationAttemptRepository:
    def __init__(self) -> None:
        self.attempts: list[AppointmentCancellationAttempt] = []
        self._idempotency_keys: set[str] = set()

    def add(self, attempt: AppointmentCancellationAttempt) -> AppointmentCancellationAttempt:
        if attempt.idempotency_key in self._idempotency_keys:
            raise IntegrityError("duplicate idempotency key", {}, Exception())

        if attempt.id is None:
            attempt.id = uuid4()

        self._idempotency_keys.add(attempt.idempotency_key)
        self.attempts.append(attempt)
        return attempt

    def get_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> AppointmentCancellationAttempt | None:
        for attempt in self.attempts:
            if attempt.idempotency_key == idempotency_key:
                return attempt
        return None


def _build_request(
    appointment_id: UUID,
    *,
    explicit_confirmation: bool = True,
    idempotency_key: str = "cancel-attempt-1",
    cancellation_reason: str | None = "Patient requested cancellation",
) -> AppointmentCancellationRequest:
    return AppointmentCancellationRequest(
        appointment_id=appointment_id,
        explicit_confirmation=explicit_confirmation,
        idempotency_key=idempotency_key,
        cancellation_reason=cancellation_reason,
    )


def create_cancellation_context(
    *,
    status: AppointmentStatus = AppointmentStatus.SCHEDULED,
    with_availability_slot: bool = False,
) -> CancellationContext:
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
    availability_slot_repository: FakeAvailabilitySlotRepository | None = None
    availability_slot_id = None
    if with_availability_slot:
        from app.models.scheduling import AvailabilitySlot

        slot = AvailabilitySlot(
            id=uuid4(),
            doctor_id=doctor.id,
            start_time=start_time,
            end_time=start_time + timedelta(minutes=30),
            status=AvailabilitySlotStatus.BOOKED,
        )
        availability_slot_repository = FakeAvailabilitySlotRepository([slot])
        availability_slot_id = slot.id

    appointment = Appointment(
        id=uuid4(),
        patient_id=patient.id,
        doctor_id=doctor.id,
        specialty_id=specialty.id,
        availability_slot_id=availability_slot_id,
        start_time=start_time,
        end_time=start_time + timedelta(minutes=30),
        status=status,
    )

    appointment_repository = FakeAppointmentRepository([appointment])
    attempt_repository = FakeAppointmentCancellationAttemptRepository()
    audit_logs = FakeAuditLogService()
    service = AppointmentCancellationService(
        appointments=appointment_repository,
        cancellation_attempts=attempt_repository,
        audit_logs=cast(AuditLogService, audit_logs),
        availability_slots=availability_slot_repository,
    )

    return CancellationContext(
        service=service,
        appointment_repository=appointment_repository,
        attempt_repository=attempt_repository,
        audit_logs=audit_logs,
        appointment=appointment,
        availability_slot_repository=availability_slot_repository,
    )


def test_scheduled_appointment_can_be_cancelled() -> None:
    context = create_cancellation_context()

    result = context.service.cancel_appointment(
        _build_request(context.appointment.id),
    )

    assert result.duplicate is False
    assert result.already_cancelled is False
    assert result.appointment_id == context.appointment.id
    assert context.appointment.status == AppointmentStatus.CANCELLED
    assert context.appointment.cancelled_at is not None
    assert context.appointment.cancellation_reason == "Patient requested cancellation"
    assert len(context.appointment_repository.appointments) == 1


def test_already_cancelled_appointment_returns_safe_idempotent_result() -> None:
    context = create_cancellation_context(status=AppointmentStatus.CANCELLED)
    context.appointment.cancelled_at = datetime(2026, 6, 20, 9, 0, tzinfo=UTC)

    result = context.service.cancel_appointment(
        _build_request(context.appointment.id),
    )

    assert result.already_cancelled is True
    assert result.duplicate is False
    assert len(context.audit_logs.records) == 0
    assert len(context.attempt_repository.attempts) == 1


def test_rescheduled_appointment_is_not_cancelable() -> None:
    context = create_cancellation_context(status=AppointmentStatus.RESCHEDULED)

    with pytest.raises(AppointmentNotCancelableError):
        context.service.cancel_appointment(
            _build_request(context.appointment.id),
        )

    assert context.appointment.status == AppointmentStatus.RESCHEDULED
    assert context.appointment.cancelled_at is None
    assert len(context.audit_logs.records) == 0


def test_completed_appointment_is_rejected() -> None:
    context = create_cancellation_context(status=AppointmentStatus.COMPLETED)

    with pytest.raises(AppointmentNotCancelableError):
        context.service.cancel_appointment(
            _build_request(context.appointment.id),
        )

    assert context.appointment.status == AppointmentStatus.COMPLETED
    assert len(context.audit_logs.records) == 0


def test_missing_confirmation_is_rejected() -> None:
    context = create_cancellation_context()

    with pytest.raises(AppointmentCancellationMissingConfirmationError):
        context.service.cancel_appointment(
            _build_request(
                context.appointment.id,
                explicit_confirmation=False,
            ),
        )

    assert context.appointment.status == AppointmentStatus.SCHEDULED
    assert len(context.audit_logs.records) == 0


def test_missing_appointment_is_rejected() -> None:
    context = create_cancellation_context()

    with pytest.raises(AppointmentNotFoundError):
        context.service.cancel_appointment(
            _build_request(uuid4()),
        )


def test_cancellation_does_not_delete_row() -> None:
    context = create_cancellation_context()
    appointment_id = context.appointment.id

    context.service.cancel_appointment(_build_request(appointment_id))

    stored = context.appointment_repository.get_by_id(appointment_id)
    assert stored is not None
    assert stored.status == AppointmentStatus.CANCELLED
    assert len(context.appointment_repository.appointments) == 1


def test_audit_log_written_on_successful_cancellation() -> None:
    context = create_cancellation_context()

    context.service.cancel_appointment(_build_request(context.appointment.id))

    assert len(context.audit_logs.records) == 1
    audit_record = context.audit_logs.records[0]
    assert audit_record.event_type == AuditEventType.APPOINTMENT_CANCELLATION_CONFIRMED
    assert audit_record.outcome == AuditEventOutcome.SUCCESS
    assert audit_record.appointment_id == context.appointment.id
    assert audit_record.patient_id == context.appointment.patient_id
    assert audit_record.event_metadata["idempotency_key"] == "cancel-attempt-1"
    assert audit_record.event_metadata["duplicate"] is False


def test_duplicate_idempotency_key_does_not_duplicate_side_effects() -> None:
    context = create_cancellation_context()
    request = _build_request(context.appointment.id, idempotency_key="cancel-dup-1")

    first = context.service.cancel_appointment(request)
    second = context.service.cancel_appointment(request)

    assert first.duplicate is False
    assert second.duplicate is True
    assert second.already_cancelled is True
    assert len(context.audit_logs.records) == 1
    assert len(context.attempt_repository.attempts) == 1
    assert len(context.appointment_repository.appointments) == 1


def test_cancellation_releases_linked_availability_slot() -> None:
    context = create_cancellation_context(with_availability_slot=True)
    assert context.availability_slot_repository is not None
    slot = context.availability_slot_repository.slots[0]

    context.service.cancel_appointment(_build_request(context.appointment.id))

    assert context.appointment.status == AppointmentStatus.CANCELLED
    assert slot.status == AvailabilitySlotStatus.AVAILABLE
