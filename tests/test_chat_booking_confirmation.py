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
from tests.chat_booking_flow_support import (
    advance_new_patient_to_booking_summary,
    complete_new_patient_booking,
)
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
        patients=[],
    )
    service = create_chat_receptionist_service(
        conversations=conversations,
        scheduling=scheduling,
        hold_service=hold_service,
    )
    return service, hold_service, scheduling


@pytest.mark.parametrize(
    "message",
    ["Yes", "Correct", "Confirm", "Book it"],
)
def test_orphan_confirmation_without_context_returns_menu(
    booking_chat_service: tuple[
        ChatReceptionistService,
        FakeAppointmentHoldService,
        SchedulingService,
    ],
    message: str,
) -> None:
    service, _hold_service, _scheduling = booking_chat_service

    result = service.handle_message(
        ChatMessageInput(message=message),
    )

    reply = result.reply.lower()
    assert result.intent == ChatReceptionistIntent.FALLBACK
    assert "hold it first" not in reply
    assert "book" in reply
    assert "reschedule" in reply
    assert "cancel" in reply
    assert "what would you like" in reply
    assert "booking_attempted" not in result.assistant_message.message_metadata

    chat_context = result.conversation.conversation_metadata.get("chat_context", {})
    assert "hold_id" not in chat_context
    assert "appointment_id" not in chat_context
    assert "patient_identity" not in chat_context


def test_orphan_denial_without_context_does_not_error(
    booking_chat_service: tuple[
        ChatReceptionistService,
        FakeAppointmentHoldService,
        SchedulingService,
    ],
) -> None:
    service, _hold_service, _scheduling = booking_chat_service

    result = service.handle_message(
        ChatMessageInput(message="No"),
    )

    reply = result.reply.lower()
    assert result.intent != ChatReceptionistIntent.BOOKING_HOLD_MISSING
    assert "hold it first" not in reply
    assert "booking_attempted" not in result.assistant_message.message_metadata

    chat_context = result.conversation.conversation_metadata.get("chat_context", {})
    assert "hold_id" not in chat_context
    assert "appointment_id" not in chat_context


def test_confirmation_with_offered_slots_is_not_orphan_menu(
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
    offered_slots = availability.conversation.conversation_metadata["chat_context"].get(
        "offered_slots",
    )
    assert offered_slots

    result = service.handle_message(
        ChatMessageInput(
            message="Yes",
            conversation_id=availability.conversation.id,
        ),
    )

    reply = result.reply.lower()
    assert "what would you like to do" not in reply
    assert "hold it first" not in reply


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

    result = advance_new_patient_to_booking_summary(service, hold.conversation)

    assert result.intent == ChatReceptionistIntent.BOOKING_CONFIRMATION_REQUIRED
    assert "before i book" in result.reply.lower()


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

    result = complete_new_patient_booking(service, hold.conversation)

    chat_context = result.conversation.conversation_metadata["chat_context"]

    assert result.intent == ChatReceptionistIntent.BOOKING_CONFIRMED
    assert result.booking_confirmed is True
    assert result.appointment_id is not None
    assert result.hold_id_to_release
    assert result.booked_patient_id is not None
    assert result.booked_appointment_start_time is not None
    assert "booked" in result.reply.lower()
    assert "Dr. Emily Carter" in result.reply
    assert "2026-07-02" in result.reply
    assert "09:00" in result.reply
    assert chat_context["appointment_id"]
    assert chat_context["booking_confirmed_at"]
    assert (
        result.assistant_message.message_metadata["appointment_id"]
        == chat_context["appointment_id"]
    )
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

    assert result.intent == ChatReceptionistIntent.PATIENT_IDENTITY_PARTIAL
    assert "seen" in result.reply.lower() or "before" in result.reply.lower()
