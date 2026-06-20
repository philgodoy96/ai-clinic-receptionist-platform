from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest

from app.domain.scheduling.enums import AvailabilitySlotStatus
from app.models.scheduling import Patient
from app.services.chat_receptionist import (
    ChatMessageInput,
    ChatReceptionistIntent,
    ChatReceptionistService,
)
from app.services.conversations import ConversationService
from app.services.scheduling import SchedulingService
from tests.test_chat_receptionist_service import (
    FakeAppointmentHoldService,
    _create_hold_service,
    create_chat_receptionist_service,
)
from tests.test_conversations import FakeConversationRepository
from tests.test_scheduling_services import (
    EMILY_JULY_SLOT_1_ID,
    FakeAppointmentRepository,
    create_demo_scheduling_service_with_emily_july_availability,
)


def create_jane_doe_patient() -> Patient:
    return Patient(
        id=uuid4(),
        full_name="Jane Doe",
        date_of_birth=date(1990, 5, 15),
        phone_number="+1 555-123-4567",
        email="jane.doe@example.com",
    )


@pytest.fixture()
def booking_chat_service() -> tuple[
    ChatReceptionistService,
    FakeAppointmentHoldService,
    SchedulingService,
]:
    repository = FakeConversationRepository()
    conversations = ConversationService(repository=repository)
    hold_service = _create_hold_service()
    scheduling = create_demo_scheduling_service_with_emily_july_availability(
        patients=[create_jane_doe_patient()],
    )
    service = create_chat_receptionist_service(
        conversations=conversations,
        scheduling=scheduling,
        hold_service=hold_service,
    )
    return service, hold_service, scheduling


def test_confirmation_without_hold_returns_booking_hold_missing(
    booking_chat_service: tuple[
        ChatReceptionistService,
        FakeAppointmentHoldService,
        SchedulingService,
    ],
) -> None:
    service, _hold_service, _scheduling = booking_chat_service

    result = service.handle_message(
        ChatMessageInput(message="Please confirm my appointment"),
    )

    assert result.intent == ChatReceptionistIntent.BOOKING_HOLD_MISSING
    assert result.assistant_message.message_metadata["booking_attempted"] is True


def test_complete_identity_without_confirmation_requests_confirmation(
    booking_chat_service: tuple[
        ChatReceptionistService,
        FakeAppointmentHoldService,
        SchedulingService,
    ],
) -> None:
    service, _hold_service, _scheduling = booking_chat_service

    availability = service.handle_message(
        ChatMessageInput(message="Dr. Emily Carter on 2026-07-02"),
    )
    hold = service.handle_message(
        ChatMessageInput(
            message="I'll take 09:00",
            conversation_id=availability.conversation.id,
        ),
    )

    result = service.handle_message(
        ChatMessageInput(
            message=(
                "Jane Doe, 1990-05-15, +1 555-123-4567, jane.doe@example.com"
            ),
            conversation_id=hold.conversation.id,
        ),
    )

    assert result.intent == ChatReceptionistIntent.BOOKING_CONFIRMATION_REQUIRED
    assert "confirm" in result.reply.lower()


def test_booking_confirmed_creates_appointment_and_updates_context(
    booking_chat_service: tuple[
        ChatReceptionistService,
        FakeAppointmentHoldService,
        SchedulingService,
    ],
) -> None:
    service, hold_service, scheduling = booking_chat_service
    appointments = scheduling.appointments
    assert isinstance(appointments, FakeAppointmentRepository)

    availability = service.handle_message(
        ChatMessageInput(message="Dr. Emily Carter on 2026-07-02"),
    )
    hold = service.handle_message(
        ChatMessageInput(
            message="I'll take 09:00",
            conversation_id=availability.conversation.id,
        ),
    )

    result = service.handle_message(
        ChatMessageInput(
            message=(
                "Jane Doe, 1990-05-15, +1 555-123-4567, jane.doe@example.com. "
                "Please confirm."
            ),
            conversation_id=hold.conversation.id,
        ),
    )

    chat_context = result.conversation.conversation_metadata["chat_context"]

    assert result.intent == ChatReceptionistIntent.BOOKING_CONFIRMED
    assert "booked" in result.reply.lower()
    assert "Dr. Emily Carter" in result.reply
    assert "2026-07-02" in result.reply
    assert "09:00" in result.reply
    assert chat_context["appointment_id"]
    assert chat_context["booking_confirmed_at"]
    assert result.assistant_message.message_metadata["appointment_id"] == chat_context[
        "appointment_id"
    ]
    assert result.assistant_message.message_metadata["booking_attempted"] is True
    assert len(appointments.appointments) == 1
    assert appointments.appointments[0].availability_slot_id == EMILY_JULY_SLOT_1_ID
    assert result.pending_hold_release is not None

    slot = scheduling.availability_slots.get_by_id(EMILY_JULY_SLOT_1_ID)
    assert slot is not None
    assert slot.status == AvailabilitySlotStatus.BOOKED

    stored_hold = hold_service.repository.get(
        doctor_id=slot.doctor_id,
        start_time=slot.start_time,
    )
    assert stored_hold is not None


def test_partial_identity_with_hold_returns_booking_identity_missing(
    booking_chat_service: tuple[
        ChatReceptionistService,
        FakeAppointmentHoldService,
        SchedulingService,
    ],
) -> None:
    service, _hold_service, _scheduling = booking_chat_service

    availability = service.handle_message(
        ChatMessageInput(message="Dr. Emily Carter on 2026-07-02"),
    )
    hold = service.handle_message(
        ChatMessageInput(
            message="I'll take 09:00",
            conversation_id=availability.conversation.id,
        ),
    )

    result = service.handle_message(
        ChatMessageInput(
            message="My name is Jane Doe, 1990-05-15, jane.doe@example.com",
            conversation_id=hold.conversation.id,
        ),
    )

    assert result.intent == ChatReceptionistIntent.BOOKING_IDENTITY_MISSING
    assert "phone" in result.reply.lower()
    assert result.assistant_message.message_metadata["booking_attempted"] is True
