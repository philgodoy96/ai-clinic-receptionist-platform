from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.audit.enums import AuditEventType
from app.domain.conversations.enums import ConversationChannel, ConversationStatus
from app.domain.voice_booking import (
    VoiceBookingConfirmationRequest,
    VoiceBookingExpiredHoldError,
    VoiceBookingMissingConfirmationError,
    VoiceBookingMissingHoldError,
    VoiceBookingMissingIdentityError,
    VoiceBookingTemporaryFailureError,
)
from app.domain.voice_booking_enums import VoiceBookingAttemptStatus
from app.domain.voice_conversation import read_voice_context
from app.models.conversations import Conversation
from app.models.voice_booking_attempt import VoiceBookingAttempt
from app.services.appointment_booking import (
    AppointmentBookingRequest,
    AppointmentBookingService,
    AppointmentSlotAlreadyBookedError,
)
from app.services.audit_logs import AuditLogService
from app.services.conversations import ConversationService
from app.services.email_jobs import EmailJobService
from app.services.scheduling import SchedulingService
from app.services.voice_booking_confirmation import VoiceBookingConfirmationService
from tests.test_appointment_booking_api import FakeAuditLogService, FakeDatabaseSession
from tests.test_appointment_booking_service import (
    BookingContext,
    FakeAppointmentRepository,
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
        patient_date_of_birth=date(1985, 4, 12),
        patient_email=patient_email,
        patient_phone="+1-555-0201",
        explicit_confirmation=explicit_confirmation,
        confirmation_text="Yes, please book it.",
        idempotency_key=idempotency_key,
        client_ip="203.0.113.10",
        notes="Annual checkup",
    )


def create_voice_booking_confirmation_context(
    *,
    book_error: Exception | None = None,
) -> VoiceBookingConfirmationContext:
    booking_context = create_booking_context()
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
        specialties=FakeSpecialtyRepository([]),
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


def test_no_duplicate_email_job_on_duplicate_callback() -> None:
    context = create_voice_booking_confirmation_context()
    hold_id = _active_hold_id(context)
    request = _build_request(context, hold_id=hold_id)

    context.service.confirm_and_book(request)
    first_email_count = len(context.email_repository.email_jobs)
    context.service.confirm_and_book(request)

    assert len(context.email_repository.email_jobs) == first_email_count
