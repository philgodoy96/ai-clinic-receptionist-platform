from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.domain.appointment_rescheduling import (
    AppointmentReschedulingHoldExpiredError,
    AppointmentReschedulingMissingConfirmationError,
    AppointmentReschedulingMissingTargetError,
    AppointmentReschedulingNotFoundError,
    AppointmentReschedulingNotReschedulableError,
    AppointmentReschedulingRequest,
    AppointmentReschedulingSlotUnavailableError,
)
from app.domain.audit.enums import AuditEventOutcome, AuditEventType
from app.domain.conversations.enums import ConversationChannel
from app.domain.jobs.enums import EmailJobType
from app.domain.scheduling.enums import AppointmentStatus, AvailabilitySlotStatus
from app.domain.voice_conversation import read_voice_context
from app.models.appointment_reschedule_attempt import AppointmentRescheduleAttempt
from app.models.conversations import Conversation
from app.models.scheduling import Appointment, AvailabilitySlot, Doctor, Patient, Specialty
from app.services.appointment_holds import AppointmentHoldService
from app.services.appointment_rescheduling import AppointmentReschedulingService
from app.services.audit_logs import AuditLogService
from app.services.conversations import ConversationService
from app.services.email_jobs import EmailJobService
from tests.test_appointment_booking_api import FakeAuditLogService
from tests.test_appointment_booking_service import (
    FakeAppointmentHoldRepository,
    FakeAppointmentRepository,
    FakeAvailabilitySlotRepository,
    FakeDoctorRepository,
)
from tests.test_conversations import FakeConversationRepository
from tests.test_email_jobs import FakeEmailJobRepository


@dataclass
class ReschedulingContext:
    service: AppointmentReschedulingService
    original_appointment: Appointment
    original_slot: AvailabilitySlot
    new_slot: AvailabilitySlot
    patient: Patient
    doctor: Doctor
    appointment_repository: FakeAppointmentRepository
    slot_repository: FakeAvailabilitySlotRepository
    hold_repository: FakeAppointmentHoldRepository
    hold_service: AppointmentHoldService
    attempt_repository: FakeAppointmentRescheduleAttemptRepository
    audit_logs: FakeAuditLogService
    email_jobs: EmailJobService
    email_repository: FakeEmailJobRepository
    conversation: Conversation
    conversation_service: ConversationService


class FakeAppointmentRescheduleAttemptRepository:
    def __init__(self) -> None:
        self.attempts: list[AppointmentRescheduleAttempt] = []
        self._idempotency_keys: set[str] = set()

    def add(self, attempt: AppointmentRescheduleAttempt) -> AppointmentRescheduleAttempt:
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
    ) -> AppointmentRescheduleAttempt | None:
        for attempt in self.attempts:
            if attempt.idempotency_key == idempotency_key:
                return attempt
        return None


def _build_request(
    context: ReschedulingContext,
    *,
    hold_id: UUID | None = None,
    new_slot_id: UUID | None = None,
    explicit_confirmation: bool = True,
    idempotency_key: str = "reschedule-attempt-1",
    owner_id: str | None = "call-123",
) -> AppointmentReschedulingRequest:
    return AppointmentReschedulingRequest(
        appointment_id=context.original_appointment.id,
        hold_id=hold_id,
        new_slot_id=new_slot_id or context.new_slot.id,
        explicit_confirmation=explicit_confirmation,
        idempotency_key=idempotency_key,
        owner_id=owner_id,
        rescheduling_reason="Patient requested a new time",
        conversation_id=str(context.conversation.id),
    )


def create_rescheduling_context(
    *,
    original_status: AppointmentStatus = AppointmentStatus.SCHEDULED,
    new_slot_status: AvailabilitySlotStatus = AvailabilitySlotStatus.AVAILABLE,
) -> ReschedulingContext:
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
    original_start = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    original_slot = AvailabilitySlot(
        id=uuid4(),
        doctor_id=doctor.id,
        start_time=original_start,
        end_time=original_start + timedelta(minutes=30),
        status=AvailabilitySlotStatus.BOOKED,
    )
    new_start = datetime(2026, 7, 2, 14, 0, tzinfo=UTC)
    new_slot = AvailabilitySlot(
        id=uuid4(),
        doctor_id=doctor.id,
        start_time=new_start,
        end_time=new_start + timedelta(minutes=30),
        status=new_slot_status,
    )
    original_appointment = Appointment(
        id=uuid4(),
        patient_id=patient.id,
        doctor_id=doctor.id,
        specialty_id=specialty.id,
        availability_slot_id=original_slot.id,
        start_time=original_slot.start_time,
        end_time=original_slot.end_time,
        status=original_status,
        reason="Initial visit",
    )

    appointment_repository = FakeAppointmentRepository([original_appointment])
    slot_repository = FakeAvailabilitySlotRepository([original_slot, new_slot])
    doctor_repository = FakeDoctorRepository([doctor])
    hold_repository = FakeAppointmentHoldRepository()
    hold_service = AppointmentHoldService(repository=hold_repository, ttl_seconds=300)
    attempt_repository = FakeAppointmentRescheduleAttemptRepository()
    audit_logs = FakeAuditLogService()
    email_repository = FakeEmailJobRepository()
    email_jobs = EmailJobService(repository=email_repository)
    conversation_repository = FakeConversationRepository()
    conversation = conversation_repository.add(
        Conversation(
            channel=ConversationChannel.VOICE,
            conversation_metadata={
                "voice_context": {
                    "appointment_id": str(original_appointment.id),
                    "hold_id": "pending-hold",
                    "availability_slot_id": str(new_slot.id),
                    "start_time": new_slot.start_time.isoformat(),
                    "end_time": new_slot.end_time.isoformat(),
                },
                "source": "retell_voice",
            },
        ),
    )
    conversation_service = ConversationService(repository=conversation_repository)
    service = AppointmentReschedulingService(
        appointments=appointment_repository,
        availability_slots=slot_repository,
        doctors=doctor_repository,
        hold_service=hold_service,
        reschedule_attempts=attempt_repository,
        audit_logs=cast(AuditLogService, audit_logs),
        conversations=conversation_service,
        email_jobs=email_jobs,
    )

    return ReschedulingContext(
        service=service,
        original_appointment=original_appointment,
        original_slot=original_slot,
        new_slot=new_slot,
        patient=patient,
        doctor=doctor,
        appointment_repository=appointment_repository,
        slot_repository=slot_repository,
        hold_repository=hold_repository,
        hold_service=hold_service,
        attempt_repository=attempt_repository,
        audit_logs=audit_logs,
        email_jobs=email_jobs,
        email_repository=email_repository,
        conversation=conversation,
        conversation_service=conversation_service,
    )


def test_successful_reschedule_creates_successor_and_preserves_original() -> None:
    context = create_rescheduling_context()
    hold = context.hold_service.create_hold(
        availability_slot_id=context.new_slot.id,
        doctor_id=context.doctor.id,
        start_time=context.new_slot.start_time,
        end_time=context.new_slot.end_time,
        owner_id="call-123",
    )

    result = context.service.reschedule_appointment(
        _build_request(
            context,
            hold_id=hold.hold_id,
            new_slot_id=context.new_slot.id,
        ),
    )

    assert result.duplicate is False
    assert result.already_rescheduled is False
    assert result.confirmation_email_created is True
    assert context.original_appointment.status == AppointmentStatus.RESCHEDULED
    assert len(context.appointment_repository.appointments) == 2

    new_appointment = context.appointment_repository.get_by_id(result.new_appointment_id)
    assert new_appointment is not None
    assert new_appointment.status == AppointmentStatus.SCHEDULED
    assert new_appointment.rescheduled_from_appointment_id == context.original_appointment.id
    assert new_appointment.availability_slot_id == context.new_slot.id
    assert context.new_slot.status == AvailabilitySlotStatus.BOOKED
    assert context.original_slot.status == AvailabilitySlotStatus.AVAILABLE


def test_missing_appointment_is_rejected() -> None:
    context = create_rescheduling_context()
    request = _build_request(context)
    request = AppointmentReschedulingRequest(
        appointment_id=uuid4(),
        new_slot_id=context.new_slot.id,
        explicit_confirmation=True,
        idempotency_key="missing-appointment",
    )

    with pytest.raises(AppointmentReschedulingNotFoundError):
        context.service.reschedule_appointment(request)


def test_non_reschedulable_appointment_is_rejected() -> None:
    context = create_rescheduling_context(original_status=AppointmentStatus.COMPLETED)

    with pytest.raises(AppointmentReschedulingNotReschedulableError):
        context.service.reschedule_appointment(_build_request(context))


def test_missing_confirmation_is_rejected() -> None:
    context = create_rescheduling_context()

    with pytest.raises(AppointmentReschedulingMissingConfirmationError):
        context.service.reschedule_appointment(
            _build_request(context, explicit_confirmation=False),
        )


def test_missing_hold_and_slot_is_rejected() -> None:
    context = create_rescheduling_context()
    request = AppointmentReschedulingRequest(
        appointment_id=context.original_appointment.id,
        explicit_confirmation=True,
        idempotency_key="missing-target",
    )

    with pytest.raises(AppointmentReschedulingMissingTargetError):
        context.service.reschedule_appointment(request)


def test_expired_hold_is_rejected() -> None:
    context = create_rescheduling_context()

    with pytest.raises(AppointmentReschedulingHoldExpiredError):
        context.service.reschedule_appointment(
            _build_request(
                context,
                hold_id=uuid4(),
                new_slot_id=context.new_slot.id,
            ),
        )


def test_unavailable_slot_is_rejected() -> None:
    context = create_rescheduling_context(
        new_slot_status=AvailabilitySlotStatus.BLOCKED,
    )

    with pytest.raises(AppointmentReschedulingSlotUnavailableError):
        context.service.reschedule_appointment(_build_request(context))


def test_duplicate_request_is_idempotent() -> None:
    context = create_rescheduling_context()
    request = _build_request(context, idempotency_key="reschedule-dup-1")

    first = context.service.reschedule_appointment(request)
    second = context.service.reschedule_appointment(request)

    assert first.duplicate is False
    assert second.duplicate is True
    assert second.new_appointment_id == first.new_appointment_id
    assert len(context.appointment_repository.appointments) == 2
    assert len(context.attempt_repository.attempts) == 1
    assert len(context.audit_logs.records) == 1
    assert len(context.email_repository.email_jobs) == 1


def test_original_appointment_is_not_deleted() -> None:
    context = create_rescheduling_context()
    original_id = context.original_appointment.id

    context.service.reschedule_appointment(_build_request(context))

    stored = context.appointment_repository.get_by_id(original_id)
    assert stored is not None
    assert stored.status == AppointmentStatus.RESCHEDULED


def test_duplicate_reschedule_does_not_create_duplicate_email_job() -> None:
    context = create_rescheduling_context()
    request = _build_request(context, idempotency_key="reschedule-email-dup")

    context.service.reschedule_appointment(request)
    context.service.reschedule_appointment(request)

    confirmation_jobs = [
        job
        for job in context.email_repository.email_jobs
        if job.job_type == EmailJobType.APPOINTMENT_CONFIRMATION
    ]
    assert len(confirmation_jobs) == 1


def test_conversation_metadata_is_safely_merged_after_reschedule() -> None:
    context = create_rescheduling_context()

    result = context.service.reschedule_appointment(_build_request(context))

    updated = context.conversation_service.get_conversation(context.conversation.id)
    voice_context = read_voice_context(updated.conversation_metadata)

    assert voice_context["appointment_id"] == str(result.new_appointment_id)
    assert voice_context["appointment_status"] == AppointmentStatus.SCHEDULED.value
    assert voice_context["availability_slot_id"] == str(context.new_slot.id)
    assert voice_context.get("hold_id") is None
    assert updated.conversation_metadata["source"] == "retell_voice"


def test_audit_log_written_on_successful_reschedule() -> None:
    context = create_rescheduling_context()

    result = context.service.reschedule_appointment(_build_request(context))

    assert len(context.audit_logs.records) == 1
    audit_record = context.audit_logs.records[0]
    assert audit_record.event_type == AuditEventType.APPOINTMENT_RESCHEDULING_CONFIRMED
    assert audit_record.outcome == AuditEventOutcome.SUCCESS
    assert audit_record.appointment_id == result.new_appointment_id
    assert audit_record.event_metadata["original_appointment_id"] == str(
        context.original_appointment.id,
    )
