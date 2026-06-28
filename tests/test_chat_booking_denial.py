"""Final booking confirmation denial: abort the booking and release the hold.

Covers the QA gap where a clear "No" at the final booking confirmation was not
treated as a denial: the appointment must not be created, no confirmation email
job must be enqueued, the active Redis hold must be released, and the booking
context must be cleared so the next turn does not re-enter final confirmation.
"""

from __future__ import annotations

from typing import cast
from uuid import UUID

import pytest

from app.models.conversations import Conversation
from app.services.appointment_booking import AppointmentBookingService
from app.services.chat_receptionist import (
    ChatMessageInput,
    ChatReceptionistIntent,
    ChatReceptionistService,
)
from app.services.conversations import ConversationService
from app.services.scheduling import SchedulingService
from tests.chat_booking_flow_support import (
    FINAL_BOOKING_CONFIRM,
    advance_new_patient_to_booking_summary,
    conversation_with_active_hold,
)
from tests.test_chat_receptionist_service import (
    FakeAppointmentHoldService,
    TrackingAppointmentBookingService,
    _create_hold_service,
    create_appointment_booking_service_for_scheduling,
    create_chat_receptionist_service,
)
from tests.test_conversations import FakeConversationRepository
from tests.test_scheduling_services import (
    FakeAppointmentRepository,
    create_demo_scheduling_service_with_emily_july_availability,
)

BookingFlowContext = tuple[
    ChatReceptionistService,
    TrackingAppointmentBookingService,
    FakeAppointmentHoldService,
    SchedulingService,
]


@pytest.fixture()
def booking_flow_context() -> BookingFlowContext:
    repository = FakeConversationRepository()
    conversations = ConversationService(repository=repository)
    hold_service = _create_hold_service()
    scheduling = create_demo_scheduling_service_with_emily_july_availability(patients=[])
    inner_booking = create_appointment_booking_service_for_scheduling(scheduling, hold_service)
    tracking_booking = TrackingAppointmentBookingService(inner_booking)
    service = create_chat_receptionist_service(
        conversations=conversations,
        scheduling=scheduling,
        hold_service=hold_service,
        appointment_booking=cast(AppointmentBookingService, tracking_booking),
    )
    return service, tracking_booking, hold_service, scheduling


def _summary_hold_id(conversation: Conversation) -> UUID:
    chat_context = conversation.conversation_metadata["chat_context"]
    hold_id = chat_context.get("hold_id")
    assert isinstance(hold_id, str) and hold_id
    return UUID(hold_id)


def _assert_booking_context_cleared(conversation: Conversation) -> None:
    chat_context = conversation.conversation_metadata["chat_context"]
    assert chat_context["hold_id"] is None
    assert chat_context["hold_expires_at"] is None
    assert chat_context["hold_owner_id"] is None
    assert chat_context["selected_availability_slot_id"] is None
    assert chat_context["selected_start_time"] is None
    assert chat_context["offered_slots"] == []
    assert chat_context.get("booking_identity_step") is None
    assert chat_context.get("pending_confirmation_email") is None
    assert chat_context["appointment_intake_awaiting"] == "date_or_time_preference"


@pytest.mark.parametrize(
    "denial_message",
    ["No", "No thanks", "Don't book it"],
)
def test_final_confirmation_denial_aborts_and_releases_hold(
    booking_flow_context: BookingFlowContext,
    denial_message: str,
) -> None:
    service, tracking_booking, hold_service, scheduling = booking_flow_context
    appointments = scheduling.appointments
    assert isinstance(appointments, FakeAppointmentRepository)

    conversation = conversation_with_active_hold(service)
    summary = advance_new_patient_to_booking_summary(service, conversation)
    assert summary.intent == ChatReceptionistIntent.BOOKING_CONFIRMATION_REQUIRED
    hold_id = _summary_hold_id(summary.conversation)
    assert hold_service.get_hold_by_id(hold_id) is not None

    result = service.handle_message(
        ChatMessageInput(message=denial_message, conversation_id=conversation.id),
    )

    assert result.intent == ChatReceptionistIntent.BOOKING_DECLINED
    assert result.booking_confirmed is False
    # The appointment is never created and the booking service is never invoked.
    assert tracking_booking.book_calls == []
    assert appointments.appointments == []
    # The active hold is released immediately (best-effort) on denial.
    assert hold_service.get_hold_by_id(hold_id) is None
    # No hold release is deferred to the route layer (that path is booking-only).
    assert result.hold_id_to_release is None
    assert result.pending_hold_release is None
    reply = result.reply.lower()
    assert "won't book" in reply or "will not book" in reply
    assert "another time" in reply
    _assert_booking_context_cleared(result.conversation)


def test_final_confirmation_yes_still_books(
    booking_flow_context: BookingFlowContext,
) -> None:
    service, tracking_booking, _hold_service, scheduling = booking_flow_context
    appointments = scheduling.appointments
    assert isinstance(appointments, FakeAppointmentRepository)

    conversation = conversation_with_active_hold(service)
    advance_new_patient_to_booking_summary(service, conversation)

    result = service.handle_message(
        ChatMessageInput(message=FINAL_BOOKING_CONFIRM, conversation_id=conversation.id),
    )

    assert result.intent == ChatReceptionistIntent.BOOKING_CONFIRMED
    assert result.booking_confirmed is True
    assert len(tracking_booking.book_calls) == 1
    assert len(appointments.appointments) == 1
    assert result.conversation.conversation_metadata["chat_context"]["appointment_id"]


def test_final_confirmation_revision_phrase_is_not_treated_as_denial(
    booking_flow_context: BookingFlowContext,
) -> None:
    service, tracking_booking, hold_service, scheduling = booking_flow_context
    appointments = scheduling.appointments
    assert isinstance(appointments, FakeAppointmentRepository)

    conversation = conversation_with_active_hold(service)
    summary = advance_new_patient_to_booking_summary(service, conversation)
    hold_id = _summary_hold_id(summary.conversation)

    result = service.handle_message(
        ChatMessageInput(
            message="On second thought, I want the one at 14",
            conversation_id=conversation.id,
        ),
    )

    # A revision phrase must not abort the booking nor book the old held slot.
    assert result.intent != ChatReceptionistIntent.BOOKING_DECLINED
    assert result.booking_confirmed is False
    assert tracking_booking.book_calls == []
    assert appointments.appointments == []
    assert "won't book" not in result.reply.lower()
    # The previously held slot is not silently booked while the user revises.
    assert hold_service.get_hold_by_id(hold_id) is not None


def test_plain_no_at_seen_before_is_new_patient_not_abort(
    booking_flow_context: BookingFlowContext,
) -> None:
    service, _tracking_booking, hold_service, _scheduling = booking_flow_context
    conversation = conversation_with_active_hold(service)
    hold_id = _summary_hold_id(conversation)

    # At the "have you been seen here before?" prompt a bare "No" answers the
    # question (new patient) and must NOT abort the booking.
    result = service.handle_message(
        ChatMessageInput(message="No", conversation_id=conversation.id),
    )

    assert result.intent != ChatReceptionistIntent.BOOKING_DECLINED
    assert hold_service.get_hold_by_id(hold_id) is not None
    chat_context = result.conversation.conversation_metadata["chat_context"]
    assert chat_context.get("booking_identity_step") is not None


def test_explicit_abort_before_identity_releases_hold(
    booking_flow_context: BookingFlowContext,
) -> None:
    service, tracking_booking, hold_service, scheduling = booking_flow_context
    appointments = scheduling.appointments
    assert isinstance(appointments, FakeAppointmentRepository)

    conversation = conversation_with_active_hold(service)
    hold_id = _summary_hold_id(conversation)
    assert hold_service.get_hold_by_id(hold_id) is not None

    # An unambiguous abort phrase at the identity stage aborts and releases the
    # hold rather than proceeding to collect identity for that slot.
    result = service.handle_message(
        ChatMessageInput(message="Don't book it", conversation_id=conversation.id),
    )

    assert result.intent == ChatReceptionistIntent.BOOKING_DECLINED
    assert tracking_booking.book_calls == []
    assert appointments.appointments == []
    assert hold_service.get_hold_by_id(hold_id) is None
    assert "another time" in result.reply.lower()
    _assert_booking_context_cleared(result.conversation)


def test_new_booking_request_after_denial_starts_clean(
    booking_flow_context: BookingFlowContext,
) -> None:
    service, tracking_booking, _hold_service, _scheduling = booking_flow_context
    conversation = conversation_with_active_hold(service)
    advance_new_patient_to_booking_summary(service, conversation)

    service.handle_message(
        ChatMessageInput(message="No", conversation_id=conversation.id),
    )

    result = service.handle_message(
        ChatMessageInput(
            message="I want to book dermatology",
            conversation_id=conversation.id,
        ),
    )

    # The next turn must not resume the stale final confirmation.
    assert result.intent not in {
        ChatReceptionistIntent.BOOKING_CONFIRMATION_REQUIRED,
        ChatReceptionistIntent.BOOKING_DECLINED,
    }
    assert "won't book" not in result.reply.lower()
    assert tracking_booking.book_calls == []


def test_denied_booking_via_api_creates_no_appointment_or_email_job() -> None:
    from fastapi.testclient import TestClient

    from tests.chat_booking_flow_support import NEW_PATIENT_IDENTITY_STEPS
    from tests.demo_guardrail_support import (
        create_guarded_chat_app,
        make_chat_booking_guardrail_settings,
    )

    app, _, email_jobs = create_guarded_chat_app(
        make_chat_booking_guardrail_settings(),
        track_email_jobs=True,
    )

    with TestClient(app) as client:
        availability = client.post(
            "/api/v1/chat/messages",
            json={"message": "Dr. Emily Carter on 2026-07-02"},
        )
        conversation_id = availability.json()["conversation_id"]
        client.post(
            "/api/v1/chat/messages",
            json={"message": "I'll take 09:00", "conversation_id": conversation_id},
        )
        for message in NEW_PATIENT_IDENTITY_STEPS:
            client.post(
                "/api/v1/chat/messages",
                json={"message": message, "conversation_id": conversation_id},
            )
        denial = client.post(
            "/api/v1/chat/messages",
            json={"message": "No", "conversation_id": conversation_id},
        )

    app.dependency_overrides.clear()

    assert denial.status_code == 200
    body = denial.json()
    assert body["booking_confirmed"] is False
    assert body.get("appointment_id") is None
    # No appointment confirmation email job is enqueued for a denied booking.
    assert email_jobs is not None
    assert email_jobs.jobs == []
