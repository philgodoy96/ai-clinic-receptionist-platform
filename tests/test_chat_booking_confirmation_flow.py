from __future__ import annotations

from datetime import date
from typing import cast
from unittest.mock import patch
from uuid import uuid4

import pytest

from app.models.conversations import Conversation
from app.models.scheduling import Patient
from app.services.appointment_booking import (
    AppointmentBookingService,
    AppointmentSlotAlreadyBookedError,
)
from app.services.appointment_holds import AppointmentHoldNotFoundError
from app.services.chat_receptionist import (
    ChatMessageInput,
    ChatReceptionistIntent,
    ChatReceptionistService,
)
from app.services.conversations import ConversationService
from app.services.scheduling import SchedulingService
from tests.test_chat_receptionist_service import (
    FakeAppointmentHoldService,
    TrackingAppointmentBookingService,
    _create_hold_service,
    create_appointment_booking_service_for_scheduling,
    create_chat_receptionist_service,
)
from tests.test_conversations import FakeConversationRepository
from tests.test_scheduling_services import (
    EMILY_JULY_SLOT_1_ID,
    FakeAppointmentRepository,
    create_demo_scheduling_service_with_emily_july_availability,
)

FULL_IDENTITY_MESSAGE = (
    "Jane Doe, 1990-05-15, +1 555-123-4567, jane.doe@example.com"
)
FULL_IDENTITY_WITH_CONFIRM = f"{FULL_IDENTITY_MESSAGE}. Please confirm."


def create_jane_doe_patient() -> Patient:
    return Patient(
        id=uuid4(),
        full_name="Jane Doe",
        date_of_birth=date(1990, 5, 15),
        phone_number="+1 555-123-4567",
        email="jane.doe@example.com",
    )


@pytest.fixture()
def booking_flow_context() -> tuple[
    ChatReceptionistService,
    TrackingAppointmentBookingService,
    FakeAppointmentHoldService,
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
    return service, tracking_booking, hold_service, scheduling


def _conversation_with_active_hold(service: ChatReceptionistService) -> Conversation:
    availability = service.handle_message(
        ChatMessageInput(message="Dr. Emily Carter on 2026-07-02"),
    )
    hold = service.handle_message(
        ChatMessageInput(
            message="I'll take 09:00",
            conversation_id=availability.conversation.id,
        ),
    )
    return hold.conversation


def test_confirm_without_hold_returns_booking_hold_missing_and_does_not_book(
    booking_flow_context: tuple[
        ChatReceptionistService,
        TrackingAppointmentBookingService,
        FakeAppointmentHoldService,
        SchedulingService,
    ],
) -> None:
    service, tracking_booking, _hold_service, _scheduling = booking_flow_context

    result = service.handle_message(ChatMessageInput(message="confirm"))

    assert result.intent == ChatReceptionistIntent.BOOKING_HOLD_MISSING
    assert tracking_booking.book_calls == []


def test_hold_exists_but_identity_missing_on_confirm(
    booking_flow_context: tuple[
        ChatReceptionistService,
        TrackingAppointmentBookingService,
        FakeAppointmentHoldService,
        SchedulingService,
    ],
) -> None:
    service, tracking_booking, _hold_service, _scheduling = booking_flow_context
    conversation = _conversation_with_active_hold(service)

    result = service.handle_message(
        ChatMessageInput(message="confirm", conversation_id=conversation.id),
    )

    assert result.intent == ChatReceptionistIntent.BOOKING_IDENTITY_MISSING
    assert tracking_booking.book_calls == []
    reply = result.reply.lower()
    assert "full name" in reply or "date of birth" in reply
    assert "phone" in reply or "email" in reply


def test_partial_identity_is_stored_in_chat_context(
    booking_flow_context: tuple[
        ChatReceptionistService,
        TrackingAppointmentBookingService,
        FakeAppointmentHoldService,
        SchedulingService,
    ],
) -> None:
    service, tracking_booking, _hold_service, _scheduling = booking_flow_context

    result = service.handle_message(
        ChatMessageInput(message="jane.doe@example.com"),
    )

    patient_identity = result.conversation.conversation_metadata["chat_context"][
        "patient_identity"
    ]

    assert patient_identity["email"] == "jane.doe@example.com"
    assert "full_name" not in patient_identity
    assert result.intent == ChatReceptionistIntent.PATIENT_IDENTITY_PARTIAL
    assert tracking_booking.book_calls == []


def test_complete_identity_without_confirmation_requests_confirmation_and_does_not_book(
    booking_flow_context: tuple[
        ChatReceptionistService,
        TrackingAppointmentBookingService,
        FakeAppointmentHoldService,
        SchedulingService,
    ],
) -> None:
    service, tracking_booking, _hold_service, _scheduling = booking_flow_context
    conversation = _conversation_with_active_hold(service)

    result = service.handle_message(
        ChatMessageInput(
            message=FULL_IDENTITY_MESSAGE,
            conversation_id=conversation.id,
        ),
    )

    assert result.intent == ChatReceptionistIntent.BOOKING_CONFIRMATION_REQUIRED
    assert "confirm" in result.reply.lower()
    assert tracking_booking.book_calls == []


def test_complete_identity_with_confirmation_books_appointment_without_llm(
    booking_flow_context: tuple[
        ChatReceptionistService,
        TrackingAppointmentBookingService,
        FakeAppointmentHoldService,
        SchedulingService,
    ],
) -> None:
    service, tracking_booking, _hold_service, scheduling = booking_flow_context
    appointments = scheduling.appointments
    assert isinstance(appointments, FakeAppointmentRepository)
    conversation = _conversation_with_active_hold(service)

    with patch("app.services.chat_receptionist.openai", create=True) as openai_mock:
        result = service.handle_message(
            ChatMessageInput(
                message=FULL_IDENTITY_WITH_CONFIRM,
                conversation_id=conversation.id,
            ),
        )

    assert openai_mock.call_count == 0
    assert result.intent == ChatReceptionistIntent.BOOKING_CONFIRMED
    assert result.booking_confirmed is True
    assert len(tracking_booking.book_calls) == 1
    assert result.conversation.conversation_metadata["chat_context"]["appointment_id"]
    assert appointments.appointments[0].availability_slot_id == EMILY_JULY_SLOT_1_ID


def test_booking_conflict_when_fake_booking_service_raises_conflict(
    booking_flow_context: tuple[
        ChatReceptionistService,
        TrackingAppointmentBookingService,
        FakeAppointmentHoldService,
        SchedulingService,
    ],
) -> None:
    service, tracking_booking, _hold_service, _scheduling = booking_flow_context
    tracking_booking.book_error = AppointmentSlotAlreadyBookedError(
        "doctor already has a scheduled appointment at this time",
    )
    conversation = _conversation_with_active_hold(service)

    result = service.handle_message(
        ChatMessageInput(
            message=FULL_IDENTITY_WITH_CONFIRM,
            conversation_id=conversation.id,
        ),
    )

    assert result.intent == ChatReceptionistIntent.BOOKING_CONFLICT
    assert len(tracking_booking.book_calls) == 1
    reply = result.reply.lower()
    assert "no longer available" in reply or "conflict" in reply


def test_hold_expired_when_booking_service_raises_hold_not_found(
    booking_flow_context: tuple[
        ChatReceptionistService,
        TrackingAppointmentBookingService,
        FakeAppointmentHoldService,
        SchedulingService,
    ],
) -> None:
    service, tracking_booking, _hold_service, _scheduling = booking_flow_context
    tracking_booking.book_error = AppointmentHoldNotFoundError(
        "appointment hold was not found or expired",
    )
    conversation = _conversation_with_active_hold(service)

    result = service.handle_message(
        ChatMessageInput(
            message=FULL_IDENTITY_WITH_CONFIRM,
            conversation_id=conversation.id,
        ),
    )

    assert result.intent == ChatReceptionistIntent.BOOKING_HOLD_EXPIRED
    assert len(tracking_booking.book_calls) == 1
    assert "expired" in result.reply.lower() or "not found" in result.reply.lower()


def test_emergency_takes_priority_over_booking_confirmation(
    booking_flow_context: tuple[
        ChatReceptionistService,
        TrackingAppointmentBookingService,
        FakeAppointmentHoldService,
        SchedulingService,
    ],
) -> None:
    service, tracking_booking, _hold_service, _scheduling = booking_flow_context
    conversation = _conversation_with_active_hold(service)

    result = service.handle_message(
        ChatMessageInput(
            message=f"This is an emergency, {FULL_IDENTITY_WITH_CONFIRM}",
            conversation_id=conversation.id,
        ),
    )

    assert result.intent == ChatReceptionistIntent.EMERGENCY
    assert tracking_booking.book_calls == []
