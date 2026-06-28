from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import cast
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.audit.enums import AuditEventOutcome, AuditEventType
from app.domain.conversations.enums import ConversationChannel, ConversationStatus
from app.domain.voice_booking import (
    VOICE_BOOKING_SOURCE,
    VoiceBookingConfirmationRequest,
    VoiceBookingExpiredHoldError,
    VoiceBookingMissingConfirmationError,
    VoiceBookingMissingHoldError,
    VoiceBookingMissingIdentityError,
    VoiceBookingPatientNotFoundError,
    VoiceBookingTemporaryFailureError,
)
from app.domain.voice_booking_enums import VoiceBookingAttemptStatus
from app.domain.voice_conversation import read_voice_context
from app.domain.voice_patient_intake import VoicePatientIntakeMode
from app.models.conversations import Conversation
from app.models.scheduling import Patient
from app.models.voice_booking_attempt import VoiceBookingAttempt
from app.services.appointment_booking import (
    AppointmentBookingRequest,
    AppointmentBookingService,
    AppointmentSlotAlreadyBookedError,
)
from app.services.audit_logs import AuditLogService
from app.services.conversations import ConversationService
from app.services.email_jobs import (
    EmailJobService,
    build_appointment_confirmation_idempotency_key,
)
from app.services.patient_identity_resolution import PatientIdentityResolutionService
from app.services.patient_intake import PatientIntakeService
from app.services.scheduling import SchedulingService
from app.services.voice_booking_confirmation import VoiceBookingConfirmationService
from tests.test_appointment_booking_api import FakeAuditLogService, FakeDatabaseSession
from tests.test_appointment_booking_service import (
    BookingContext,
    FakeAppointmentRepository,
    FakePatientRepository,
    create_booking_context,
)
from tests.test_chat_receptionist_service import TrackingAppointmentBookingService
from tests.test_conversations import FakeConversationRepository
from tests.test_email_jobs import FakeEmailJobRepository
from tests.test_scheduling_services import FakeSpecialtyRepository


@dataclass
class VoiceBookingConfirmationContext:
    service: VoiceBookingConfirmationService
    booking_context: BookingContext
    conversation: Conversation
    voice_call_id: UUID
    tracking_booking: TrackingAppointmentBookingService
    attempt_repository: FakeVoiceBookingAttemptRepository
    audit_logs: FakeAuditLogService
    email_jobs: EmailJobService
    email_repository: FakeEmailJobRepository
    db: FakeDatabaseSession
    appointment_repository: FakeAppointmentRepository


class FakeVoiceBookingAttemptRepository:
    def __init__(self) -> None:
        self.attempts: list[VoiceBookingAttempt] = []
        self._idempotency_keys: set[str] = set()

    def add(self, attempt: VoiceBookingAttempt) -> VoiceBookingAttempt:
        if attempt.idempotency_key in self._idempotency_keys:
            raise IntegrityError("duplicate idempotency key", {}, Exception())

        if attempt.id is None:
            attempt.id = uuid4()

        self._idempotency_keys.add(attempt.idempotency_key)
        self.attempts.append(attempt)
        return attempt

    def update(self, attempt: VoiceBookingAttempt) -> VoiceBookingAttempt:
        return attempt

    def get_by_idempotency_key(self, idempotency_key: str) -> VoiceBookingAttempt | None:
        for attempt in self.attempts:
            if attempt.idempotency_key == idempotency_key:
                return attempt
        return None

    def get_by_id(self, attempt_id: UUID) -> VoiceBookingAttempt | None:
        for attempt in self.attempts:
            if attempt.id == attempt_id:
                return attempt
        return None


def _build_request(
    context: VoiceBookingConfirmationContext,
    *,
    hold_id: str,
    explicit_confirmation: bool = True,
    patient_name: str = "John Miller",
    patient_email: str = "john.miller@example.test",
    patient_date_of_birth: date = date(1985, 4, 12),
    patient_phone: str | None = "+1-555-0201",
    idempotency_key: str = "retell:voice-booking:tool-call-1",
) -> VoiceBookingConfirmationRequest:
    return VoiceBookingConfirmationRequest(
        provider="retell",
        provider_call_id="retell-call-123",
        tool_call_id="tool-call-1",
        voice_call_id=context.voice_call_id,
        conversation_id=context.conversation.id,
        hold_id=hold_id,
        slot_id=str(context.booking_context.slot.id),
        patient_name=patient_name,
        patient_date_of_birth=patient_date_of_birth,
        patient_email=patient_email,
        patient_phone=patient_phone,
        patient_resolution_id=None,
        explicit_confirmation=explicit_confirmation,
        confirmation_text="Yes, please book it.",
        idempotency_key=idempotency_key,
        client_ip="203.0.113.10",
        notes="Annual checkup",
    )


def _patient_repository(context: VoiceBookingConfirmationContext) -> FakePatientRepository:
    return cast(FakePatientRepository, context.booking_context.booking_service.patients)


def create_voice_booking_confirmation_context(
    *,
    book_error: Exception | None = None,
    patients: list[Patient] | None = None,
    voice_patient_intake_mode: VoicePatientIntakeMode = VoicePatientIntakeMode.LOOKUP_ONLY,
    patient_identity_resolution: PatientIdentityResolutionService | None = None,
) -> VoiceBookingConfirmationContext:
    booking_context = create_booking_context()
    if patients is not None:
        booking_context.booking_service.patients = FakePatientRepository(patients)
    inner_booking = booking_context.booking_service
    hold = booking_context.hold_service.create_hold(
        availability_slot_id=booking_context.slot.id,
        doctor_id=booking_context.doctor.id,
        start_time=booking_context.slot.start_time,
        end_time=booking_context.slot.end_time,
        owner_id="retell-call-123",
    )

    conversation_repository = FakeConversationRepository()
    conversation = Conversation(
        id=uuid4(),
        channel=ConversationChannel.VOICE,
        status=ConversationStatus.ACTIVE,
        external_conversation_id="retell-call-123",
        call_id="retell-call-123",
        conversation_metadata={
            "voice_context": {
                "hold_id": str(hold.hold_id),
                "availability_slot_id": str(booking_context.slot.id),
                "start_time": booking_context.slot.start_time.isoformat(),
                "end_time": booking_context.slot.end_time.isoformat(),
            },
        },
    )
    conversation_repository.conversations.append(conversation)

    scheduling_service = SchedulingService(
        specialties=FakeSpecialtyRepository([booking_context.specialty]),
        doctors=inner_booking.doctors,
        patients=inner_booking.patients,
        availability_slots=inner_booking.availability_slots,
        appointments=inner_booking.appointments,
    )

    tracking_booking = TrackingAppointmentBookingService(
        inner_booking,
        book_error=book_error,
    )
    attempt_repository = FakeVoiceBookingAttemptRepository()
    audit_logs = FakeAuditLogService()
    email_repository = FakeEmailJobRepository()
    email_jobs = EmailJobService(repository=email_repository)
    db = FakeDatabaseSession()

    service = VoiceBookingConfirmationService(
        db=cast(Session, db),
        booking_service=cast(AppointmentBookingService, tracking_booking),
        hold_service=booking_context.hold_service,
        scheduling_service=scheduling_service,
        conversations=ConversationService(repository=conversation_repository),
        audit_logs=cast(AuditLogService, audit_logs),
        email_jobs=email_jobs,
        voice_booking_attempts=attempt_repository,
        appointments=booking_context.appointment_repository,
        availability_slots=inner_booking.availability_slots,
        patient_intake=PatientIntakeService(
            patients=inner_booking.patients,
            mode=voice_patient_intake_mode,
            db=cast(Session, db),
        ),
        patient_identity_resolution=patient_identity_resolution,
    )

    return VoiceBookingConfirmationContext(
        service=service,
        booking_context=booking_context,
        conversation=conversation,
        voice_call_id=uuid4(),
        tracking_booking=tracking_booking,
        attempt_repository=attempt_repository,
        audit_logs=audit_logs,
        email_jobs=email_jobs,
        email_repository=email_repository,
        db=db,
        appointment_repository=booking_context.appointment_repository,
    )


def _active_hold_id(context: VoiceBookingConfirmationContext) -> str:
    hold = next(iter(context.booking_context.hold_repository.holds.values()))
    return str(hold.hold_id)


def test_successful_booking_calls_appointment_booking_service_exactly_once() -> None:
    context = create_voice_booking_confirmation_context()
    hold_id = _active_hold_id(context)

    result = context.service.confirm_and_book(_build_request(context, hold_id=hold_id))

    assert len(context.tracking_booking.book_calls) == 1
    assert context.tracking_booking.book_calls[0] == AppointmentBookingRequest(
        hold_id=UUID(hold_id),
        availability_slot_id=context.booking_context.slot.id,
        patient_id=context.booking_context.patient.id,
        owner_id="retell-call-123",
        reason="Annual checkup",
    )
    assert result.duplicate is False
    assert result.appointment_id is not None
    assert context.db.committed is True
    assert context.audit_logs.records[-1].event_type == AuditEventType.APPOINTMENT_BOOKING_CONFIRMED


def test_duplicate_callback_returns_existing_appointment() -> None:
    context = create_voice_booking_confirmation_context()
    hold_id = _active_hold_id(context)
    request = _build_request(context, hold_id=hold_id)

    first = context.service.confirm_and_book(request)
    second = context.service.confirm_and_book(request)

    assert len(context.tracking_booking.book_calls) == 1
    assert second.duplicate is True
    assert second.appointment_id == first.appointment_id


def test_missing_hold_rejected() -> None:
    context = create_voice_booking_confirmation_context()
    context.conversation.conversation_metadata = {"voice_context": {}}
    request = _build_request(context, hold_id="")

    with pytest.raises(VoiceBookingMissingHoldError):
        context.service.confirm_and_book(request)

    assert context.tracking_booking.book_calls == []


def test_expired_hold_rejected() -> None:
    context = create_voice_booking_confirmation_context()
    hold_id = _active_hold_id(context)
    hold = context.booking_context.hold_repository.get_by_hold_id(UUID(hold_id))
    assert hold is not None
    context.booking_context.hold_repository.delete(
        doctor_id=hold.doctor_id,
        start_time=hold.start_time,
    )

    with pytest.raises(VoiceBookingExpiredHoldError):
        context.service.confirm_and_book(_build_request(context, hold_id=hold_id))

    assert context.tracking_booking.book_calls == []


def test_missing_identity_rejected() -> None:
    context = create_voice_booking_confirmation_context()
    hold_id = _active_hold_id(context)

    with pytest.raises(VoiceBookingMissingIdentityError):
        context.service.confirm_and_book(
            _build_request(
                context,
                hold_id=hold_id,
                patient_name="   ",
            ),
        )

    assert context.tracking_booking.book_calls == []


def test_missing_confirmation_rejected() -> None:
    context = create_voice_booking_confirmation_context()
    hold_id = _active_hold_id(context)

    with pytest.raises(VoiceBookingMissingConfirmationError):
        context.service.confirm_and_book(
            _build_request(context, hold_id=hold_id, explicit_confirmation=False),
        )

    assert context.tracking_booking.book_calls == []


def test_booking_service_failure_returns_temporary_failure() -> None:
    context = create_voice_booking_confirmation_context(
        book_error=AppointmentSlotAlreadyBookedError("slot already booked"),
    )
    hold_id = _active_hold_id(context)

    with pytest.raises(VoiceBookingTemporaryFailureError) as exc_info:
        context.service.confirm_and_book(_build_request(context, hold_id=hold_id))

    assert exc_info.value.error_code == "slot_already_booked"
    assert len(context.tracking_booking.book_calls) == 1
    assert context.attempt_repository.attempts[-1].status == VoiceBookingAttemptStatus.FAILED


def test_successful_booking_clears_hold_context() -> None:
    context = create_voice_booking_confirmation_context()
    hold_id = _active_hold_id(context)

    context.service.confirm_and_book(_build_request(context, hold_id=hold_id))

    voice_context = read_voice_context(context.conversation.conversation_metadata)
    assert voice_context.get("hold_id") is None
    assert voice_context.get("availability_slot_id") is None
    assert voice_context.get("appointment_id") is not None


def test_no_direct_appointment_insert_outside_service() -> None:
    context = create_voice_booking_confirmation_context()
    hold_id = _active_hold_id(context)
    appointments_before = len(context.appointment_repository.appointments)

    context.service.confirm_and_book(_build_request(context, hold_id=hold_id))

    appointments_after_booking_service = len(context.appointment_repository.appointments)
    assert appointments_after_booking_service == appointments_before + 1
    assert len(context.tracking_booking.book_calls) == 1


def test_active_hold_required() -> None:
    context = create_voice_booking_confirmation_context()
    context.conversation.conversation_metadata = {
        "voice_context": {
            "specialty_name": "Dermatology",
            "doctor_name": "Dr. Emily Carter",
        },
    }

    with pytest.raises(VoiceBookingMissingHoldError):
        context.service.confirm_and_book(
            _build_request(context, hold_id=""),
        )

    assert context.tracking_booking.book_calls == []


def test_no_duplicate_email_job_on_duplicate_callback() -> None:
    context = create_voice_booking_confirmation_context()
    hold_id = _active_hold_id(context)
    request = _build_request(context, hold_id=hold_id)

    context.service.confirm_and_book(request)
    first_email_count = len(context.email_repository.email_jobs)
    context.service.confirm_and_book(request)

    assert len(context.email_repository.email_jobs) == first_email_count


def test_voice_booking_enriches_appointment_confirmation_email_job() -> None:
    context = create_voice_booking_confirmation_context()
    hold_id = _active_hold_id(context)

    result = context.service.confirm_and_book(_build_request(context, hold_id=hold_id))

    assert len(context.email_repository.email_jobs) == 1
    email_job = context.email_repository.email_jobs[0]
    assert email_job.recipient_email == context.booking_context.patient.email
    assert email_job.payload["patient_name"] == context.booking_context.patient.full_name
    assert email_job.payload["doctor_name"] == context.booking_context.doctor.full_name
    assert email_job.payload["source"] == VOICE_BOOKING_SOURCE
    assert email_job.payload["specialty_name"] == context.booking_context.specialty.name
    assert email_job.idempotency_key == build_appointment_confirmation_idempotency_key(
        result.appointment_id,
    )


def test_voice_booking_does_not_call_resend_directly() -> None:
    context = create_voice_booking_confirmation_context()
    hold_id = _active_hold_id(context)

    with patch("app.email.resend_provider.ResendEmailProvider.send") as resend_send:
        context.service.confirm_and_book(_build_request(context, hold_id=hold_id))

    resend_send.assert_not_called()


def test_failed_recoverable_booking_preserves_useful_context() -> None:
    context = create_voice_booking_confirmation_context(
        book_error=AppointmentSlotAlreadyBookedError("slot already booked"),
    )
    hold_id = _active_hold_id(context)
    voice_context_before = read_voice_context(context.conversation.conversation_metadata)

    with pytest.raises(VoiceBookingTemporaryFailureError):
        context.service.confirm_and_book(_build_request(context, hold_id=hold_id))

    voice_context_after = read_voice_context(context.conversation.conversation_metadata)
    assert voice_context_after.get("hold_id") == voice_context_before.get("hold_id")
    assert voice_context_after.get("availability_slot_id") == voice_context_before.get(
        "availability_slot_id",
    )
    assert voice_context_after.get("appointment_id") is None


def test_audit_success_event_recorded_when_audit_service_available() -> None:
    context = create_voice_booking_confirmation_context()
    hold_id = _active_hold_id(context)

    context.service.confirm_and_book(_build_request(context, hold_id=hold_id))

    assert len(context.audit_logs.records) == 1
    audit_record = context.audit_logs.records[0]
    assert audit_record.event_type == AuditEventType.APPOINTMENT_BOOKING_CONFIRMED
    assert audit_record.outcome == AuditEventOutcome.SUCCESS
    assert audit_record.appointment_id is not None
    assert "transcript" not in audit_record.event_metadata


def test_audit_failure_event_recorded_on_recoverable_booking_error() -> None:
    context = create_voice_booking_confirmation_context(
        book_error=AppointmentSlotAlreadyBookedError("slot already booked"),
    )
    hold_id = _active_hold_id(context)

    with pytest.raises(VoiceBookingTemporaryFailureError):
        context.service.confirm_and_book(_build_request(context, hold_id=hold_id))

    assert len(context.audit_logs.records) == 1
    audit_record = context.audit_logs.records[0]
    assert audit_record.event_type == AuditEventType.APPOINTMENT_BOOKING_FAILED
    assert audit_record.outcome == AuditEventOutcome.FAILURE


def test_existing_seeded_patient_booking_succeeds_in_lookup_only_mode() -> None:
    context = create_voice_booking_confirmation_context(
        voice_patient_intake_mode=VoicePatientIntakeMode.LOOKUP_ONLY,
    )
    hold_id = _active_hold_id(context)

    result = context.service.confirm_and_book(_build_request(context, hold_id=hold_id))

    assert result.patient_id == context.booking_context.patient.id
    assert len(context.tracking_booking.book_calls) == 1


def test_new_patient_fails_in_lookup_only_mode() -> None:
    context = create_voice_booking_confirmation_context(
        patients=[],
        voice_patient_intake_mode=VoicePatientIntakeMode.LOOKUP_ONLY,
    )
    hold_id = _active_hold_id(context)

    with pytest.raises(VoiceBookingPatientNotFoundError):
        context.service.confirm_and_book(
            _build_request(
                context,
                hold_id=hold_id,
                patient_name="Ava Thompson",
                patient_email="ava.thompson@example.test",
                patient_date_of_birth=date(1992, 9, 3),
                patient_phone=None,
            ),
        )

    assert context.tracking_booking.book_calls == []


def test_new_patient_succeeds_in_demo_auto_create_mode() -> None:
    context = create_voice_booking_confirmation_context(
        patients=[],
        voice_patient_intake_mode=VoicePatientIntakeMode.DEMO_AUTO_CREATE,
    )
    hold_id = _active_hold_id(context)

    result = context.service.confirm_and_book(
        _build_request(
            context,
            hold_id=hold_id,
            patient_name="Ava Thompson",
            patient_email="ava.thompson@example.test",
            patient_date_of_birth=date(1992, 9, 3),
            patient_phone=None,
        ),
    )

    patient_repo = _patient_repository(context)
    assert len(patient_repo.patients) == 1
    assert patient_repo.patients[0].phone_number is None
    assert result.patient_id == patient_repo.patients[0].id
    assert len(context.tracking_booking.book_calls) == 1


def test_new_demo_patient_booking_uses_caller_provided_email_only() -> None:
    context = create_voice_booking_confirmation_context(
        patients=[],
        voice_patient_intake_mode=VoicePatientIntakeMode.DEMO_AUTO_CREATE,
    )
    hold_id = _active_hold_id(context)
    caller_email = "felipe.logan@example.test"

    result = context.service.confirm_and_book(
        _build_request(
            context,
            hold_id=hold_id,
            patient_name="Felipe Logan",
            patient_email=caller_email,
            patient_date_of_birth=date(1990, 3, 15),
            patient_phone=None,
        ),
    )

    patient = _patient_repository(context).patients[0]
    assert patient.email == caller_email
    assert patient.phone_number is None
    assert result.patient_id == patient.id


def test_missing_email_rejected() -> None:
    context = create_voice_booking_confirmation_context()
    hold_id = _active_hold_id(context)

    with pytest.raises(VoiceBookingMissingIdentityError):
        context.service.confirm_and_book(
            _build_request(
                context,
                hold_id=hold_id,
                patient_email="   ",
            ),
        )

    assert context.tracking_booking.book_calls == []


def test_duplicate_new_patient_callback_does_not_duplicate_patient_or_appointment() -> None:
    context = create_voice_booking_confirmation_context(
        patients=[],
        voice_patient_intake_mode=VoicePatientIntakeMode.DEMO_AUTO_CREATE,
    )
    hold_id = _active_hold_id(context)
    request = _build_request(
        context,
        hold_id=hold_id,
        patient_name="Ava Thompson",
        patient_email="ava.thompson@example.test",
        patient_date_of_birth=date(1992, 9, 3),
        patient_phone=None,
    )

    first = context.service.confirm_and_book(request)
    second = context.service.confirm_and_book(request)

    assert len(_patient_repository(context).patients) == 1
    assert len(context.tracking_booking.book_calls) == 1
    assert second.duplicate is True
    assert second.appointment_id == first.appointment_id
