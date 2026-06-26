"""Comprehensive foundation coverage for appointment rescheduling."""

from __future__ import annotations

from dataclasses import fields
from typing import cast
from unittest.mock import patch
from uuid import uuid4

import pytest

from app.domain.appointment_rescheduling import (
    AppointmentReschedulingFailureCode,
    AppointmentReschedulingHoldExpiredError,
    AppointmentReschedulingMissingConfirmationError,
    AppointmentReschedulingMissingTargetError,
    AppointmentReschedulingNotFoundError,
    AppointmentReschedulingNotReschedulableError,
    AppointmentReschedulingRequest,
    AppointmentReschedulingSlotUnavailableError,
    validate_appointment_rescheduling_request,
)
from app.domain.audit.appointment_rescheduling import build_safe_reschedule_audit_metadata
from app.domain.audit.enums import AuditEventOutcome, AuditEventType
from app.domain.jobs.enums import EmailJobType
from app.domain.scheduling.enums import AppointmentStatus, AvailabilitySlotStatus
from app.services.appointment_booking import (
    AppointmentBookingRequest,
    AppointmentBookingService,
)
from app.services.chat_receptionist import (
    ChatReceptionistIntent,
    ChatReceptionistService,
)
from app.services.conversations import ConversationService
from app.services.scheduling import SchedulingService
from tests.chat_booking_flow_support import complete_new_patient_booking
from tests.retell_cancellation_test_support import (
    cancellation_tool_request,
    create_retell_cancellation_tool_context,
)
from tests.test_appointment_booking_service import create_booking_context
from tests.test_appointment_cancellation_service import (
    _build_request as build_cancellation_request,
)
from tests.test_appointment_cancellation_service import (
    create_cancellation_context,
)
from tests.test_appointment_rescheduling_service import (
    _build_request,
    create_rescheduling_context,
)
from tests.test_chat_booking_confirmation_flow import (
    _conversation_with_active_hold,
    create_jane_doe_patient,
)
from tests.test_chat_receptionist_service import (
    TrackingAppointmentBookingService,
    _create_hold_service,
    create_appointment_booking_service_for_scheduling,
    create_chat_receptionist_service,
)
from tests.test_conversations import FakeConversationRepository
from tests.test_retell_voice_booking_tool import (
    _hold_id,
    _tool_request,
    create_retell_booking_tool_context,
)
from tests.test_scheduling_services import (
    EMILY_JULY_SLOT_1_ID,
    FakeAppointmentRepository,
    create_demo_scheduling_service_with_emily_july_availability,
)

_BLOCKED_AUDIT_METADATA_KEYS = frozenset(
    {
        "api_key",
        "audio",
        "email",
        "from_number",
        "phone",
        "phone_number",
        "raw_payload",
        "raw_transcript",
        "recording",
        "recording_url",
        "to_number",
        "transcript",
        "transcript_text",
        "voice_transcript",
        "webhook_secret",
    },
)


def _assert_audit_metadata_is_safe(metadata: dict[str, object]) -> None:
    for key in metadata:
        assert key.strip().lower() not in _BLOCKED_AUDIT_METADATA_KEYS


def test_foundation_valid_request_structure() -> None:
    appointment_id = uuid4()
    new_slot_id = uuid4()
    request = AppointmentReschedulingRequest(
        appointment_id=appointment_id,
        new_slot_id=new_slot_id,
        explicit_confirmation=True,
        idempotency_key="foundation-valid-request",
        owner_id="call-123",
        rescheduling_reason="Need a later time",
    )

    field_names = {field.name for field in fields(AppointmentReschedulingRequest)}
    assert {
        "appointment_id",
        "explicit_confirmation",
        "idempotency_key",
        "hold_id",
        "new_slot_id",
        "owner_id",
        "rescheduling_reason",
    }.issubset(field_names)
    assert request.appointment_id == appointment_id
    assert request.new_slot_id == new_slot_id
    validate_appointment_rescheduling_request(request)


def test_foundation_original_appointment_id_is_required() -> None:
    with pytest.raises(TypeError):
        AppointmentReschedulingRequest(  # type: ignore[call-arg]
            explicit_confirmation=True,
            idempotency_key="foundation-missing-original",
            new_slot_id=uuid4(),
        )


def test_foundation_hold_id_or_new_slot_id_is_required() -> None:
    with pytest.raises(AppointmentReschedulingMissingTargetError) as exc_info:
        validate_appointment_rescheduling_request(
            AppointmentReschedulingRequest(
                appointment_id=uuid4(),
                explicit_confirmation=True,
                idempotency_key="foundation-missing-target",
            ),
        )

    assert exc_info.value.failure_code == AppointmentReschedulingFailureCode.MISSING_TARGET


def test_foundation_explicit_confirmation_is_required() -> None:
    context = create_rescheduling_context()

    with pytest.raises(AppointmentReschedulingMissingConfirmationError) as exc_info:
        context.service.reschedule_appointment(
            _build_request(context, explicit_confirmation=False),
        )

    assert exc_info.value.failure_code == (AppointmentReschedulingFailureCode.MISSING_CONFIRMATION)


def test_foundation_successful_reschedule() -> None:
    context = create_rescheduling_context()

    result = context.service.reschedule_appointment(_build_request(context))

    assert result.duplicate is False
    assert result.already_rescheduled is False
    assert result.original_appointment_id == context.original_appointment.id
    assert context.original_appointment.status == AppointmentStatus.RESCHEDULED

    new_appointment = context.appointment_repository.get_by_id(result.new_appointment_id)
    assert new_appointment is not None
    assert new_appointment.status == AppointmentStatus.SCHEDULED


def test_foundation_original_appointment_is_not_deleted() -> None:
    context = create_rescheduling_context()
    original_id = context.original_appointment.id
    appointment_count_before = len(context.appointment_repository.appointments)

    context.service.reschedule_appointment(_build_request(context))

    assert len(context.appointment_repository.appointments) == appointment_count_before + 1
    stored = context.appointment_repository.get_by_id(original_id)
    assert stored is not None
    assert stored.status == AppointmentStatus.RESCHEDULED


def test_foundation_traceability_between_old_and_new_appointment_is_preserved() -> None:
    context = create_rescheduling_context()

    result = context.service.reschedule_appointment(_build_request(context))

    original = context.appointment_repository.get_by_id(result.original_appointment_id)
    successor = context.appointment_repository.get_by_id(result.new_appointment_id)
    assert original is not None
    assert successor is not None
    assert successor.rescheduled_from_appointment_id == original.id
    assert successor.patient_id == original.patient_id
    assert (
        context.appointment_repository.find_by_rescheduled_from(
            appointment_id=original.id,
        )
        == successor
    )


def test_foundation_missing_appointment_is_rejected() -> None:
    context = create_rescheduling_context()

    with pytest.raises(AppointmentReschedulingNotFoundError):
        context.service.reschedule_appointment(
            AppointmentReschedulingRequest(
                appointment_id=uuid4(),
                new_slot_id=context.new_slot.id,
                explicit_confirmation=True,
                idempotency_key="foundation-missing-appointment",
            ),
        )


def test_foundation_non_reschedulable_appointment_is_rejected() -> None:
    context = create_rescheduling_context(original_status=AppointmentStatus.COMPLETED)

    with pytest.raises(AppointmentReschedulingNotReschedulableError):
        context.service.reschedule_appointment(_build_request(context))


def test_foundation_expired_hold_is_rejected() -> None:
    context = create_rescheduling_context()

    with pytest.raises(AppointmentReschedulingHoldExpiredError):
        context.service.reschedule_appointment(
            _build_request(
                context,
                hold_id=uuid4(),
                new_slot_id=context.new_slot.id,
            ),
        )


def test_foundation_unavailable_slot_is_rejected() -> None:
    context = create_rescheduling_context(
        new_slot_status=AvailabilitySlotStatus.BLOCKED,
    )

    with pytest.raises(AppointmentReschedulingSlotUnavailableError):
        context.service.reschedule_appointment(_build_request(context))


def test_foundation_duplicate_idempotency_key_returns_same_result() -> None:
    context = create_rescheduling_context()
    request = _build_request(context, idempotency_key="foundation-idempotency-dup")

    first = context.service.reschedule_appointment(request)
    second = context.service.reschedule_appointment(request)

    assert first.duplicate is False
    assert second.duplicate is True
    assert second.original_appointment_id == first.original_appointment_id
    assert second.new_appointment_id == first.new_appointment_id
    assert len(context.appointment_repository.appointments) == 2
    assert len(context.attempt_repository.attempts) == 1


def test_foundation_no_duplicate_email_job_on_retry() -> None:
    context = create_rescheduling_context()
    request = _build_request(context, idempotency_key="foundation-email-dup")

    context.service.reschedule_appointment(request)
    context.service.reschedule_appointment(request)

    confirmation_jobs = [
        job
        for job in context.email_repository.email_jobs
        if job.job_type == EmailJobType.APPOINTMENT_CONFIRMATION
    ]
    assert len(confirmation_jobs) == 1


def test_foundation_audit_events_exclude_sensitive_fields() -> None:
    context = create_rescheduling_context()
    unsafe_extra = build_safe_reschedule_audit_metadata(
        idempotency_key="foundation-audit-safe",
        original_appointment_id=uuid4(),
        extra={
            "transcript": "secret transcript",
            "email": "patient@example.test",
            "duplicate": True,
        },
    )
    assert "transcript" not in unsafe_extra
    assert "email" not in unsafe_extra

    context.service.reschedule_appointment(_build_request(context))

    for record in context.audit_logs.records:
        _assert_audit_metadata_is_safe(record.event_metadata)

    succeeded = next(
        record
        for record in context.audit_logs.records
        if record.event_type == AuditEventType.APPOINTMENT_RESCHEDULE_SUCCEEDED
    )
    assert succeeded.outcome == AuditEventOutcome.SUCCESS
    assert succeeded.event_metadata["original_appointment_id"] == str(
        context.original_appointment.id,
    )


def test_foundation_new_slot_id_without_hold_is_accepted() -> None:
    context = create_rescheduling_context()

    result = context.service.reschedule_appointment(
        _build_request(context, hold_id=None, new_slot_id=context.new_slot.id),
    )

    assert result.duplicate is False
    assert result.new_appointment_id is not None


def test_foundation_hold_id_resolves_target_slot_when_new_slot_id_omitted() -> None:
    context = create_rescheduling_context()
    hold = context.hold_service.create_hold(
        availability_slot_id=context.new_slot.id,
        doctor_id=context.doctor.id,
        start_time=context.new_slot.start_time,
        end_time=context.new_slot.end_time,
        owner_id="call-123",
    )

    result = context.service.reschedule_appointment(
        AppointmentReschedulingRequest(
            appointment_id=context.original_appointment.id,
            hold_id=hold.hold_id,
            explicit_confirmation=True,
            idempotency_key="foundation-hold-only",
            owner_id="call-123",
            conversation_id=str(context.conversation.id),
        ),
    )

    successor = context.appointment_repository.get_by_id(result.new_appointment_id)
    assert successor is not None
    assert successor.availability_slot_id == context.new_slot.id


def test_foundation_booking_regression_still_books() -> None:
    context = create_booking_context()
    hold = context.hold_service.create_hold(
        availability_slot_id=context.slot.id,
        doctor_id=context.doctor.id,
        start_time=context.slot.start_time,
        end_time=context.slot.end_time,
        owner_id="call-123",
    )

    result = context.booking_service.book_appointment(
        AppointmentBookingRequest(
            hold_id=hold.hold_id,
            availability_slot_id=context.slot.id,
            patient_id=context.patient.id,
            owner_id="call-123",
        ),
    )

    assert result.appointment.status == AppointmentStatus.SCHEDULED
    assert context.slot.status == AvailabilitySlotStatus.BOOKED


def test_foundation_cancellation_regression_still_cancels() -> None:
    context = create_cancellation_context()

    result = context.service.cancel_appointment(
        build_cancellation_request(context.appointment.id),
    )

    assert result.duplicate is False
    assert context.appointment.status == AppointmentStatus.CANCELLED
    assert len(context.appointment_repository.appointments) == 1


def test_foundation_retell_voice_booking_regression_still_books() -> None:
    context = create_retell_booking_tool_context()
    booking_context = context["booking_context"]
    hold_id = _hold_id(booking_context)

    response = context["adapter"].execute(
        _tool_request(hold_id=hold_id, slot_id=str(booking_context.slot.id)),
    )

    assert response.status == "succeeded"
    assert len(context["tracking_booking"].book_calls) == 1
    assert response.result["status"] == AppointmentStatus.SCHEDULED.value


def test_foundation_retell_voice_cancellation_regression_still_cancels() -> None:
    context = create_retell_cancellation_tool_context()
    appointment = context["appointment"]

    response = context["adapter"].execute(
        cancellation_tool_request(
            appointment_id=str(appointment.id),
            patient_resolution_id=context["patient_resolution_id"],
            tool_call_id="tool-call-cancel-foundation-regression",
        ),
    )

    assert response.status == "succeeded"
    assert appointment.status == AppointmentStatus.CANCELLED
    assert len(context["tracking_cancellation"].cancel_calls) == 1


def _create_chat_booking_flow_context() -> tuple[
    ChatReceptionistService,
    TrackingAppointmentBookingService,
    SchedulingService,
]:
    repository = FakeConversationRepository()
    conversations = ConversationService(repository=repository)
    hold_service = _create_hold_service()
    scheduling = create_demo_scheduling_service_with_emily_july_availability(
        patients=[create_jane_doe_patient()],
    )
    inner_booking = create_appointment_booking_service_for_scheduling(
        scheduling,
        hold_service,
    )
    tracking_booking = TrackingAppointmentBookingService(inner_booking)
    service = create_chat_receptionist_service(
        conversations=conversations,
        scheduling=scheduling,
        hold_service=hold_service,
        appointment_booking=cast(AppointmentBookingService, tracking_booking),
    )
    return service, tracking_booking, scheduling


def test_foundation_chat_booking_regression_still_books() -> None:
    service, tracking_booking, scheduling = _create_chat_booking_flow_context()
    appointments = scheduling.appointments
    assert isinstance(appointments, FakeAppointmentRepository)
    conversation = _conversation_with_active_hold(service)

    with patch("app.services.chat_receptionist.openai", create=True) as openai_mock:
        result = complete_new_patient_booking(service, conversation)

    assert openai_mock.call_count == 0
    assert result.intent == ChatReceptionistIntent.BOOKING_CONFIRMED
    assert result.booking_confirmed is True
    assert len(tracking_booking.book_calls) == 1
    assert appointments.appointments[0].availability_slot_id == EMILY_JULY_SLOT_1_ID
