"""Regression smoke tests: voice conversation work must not break core flows."""

from __future__ import annotations

from typing import cast
from unittest.mock import patch

from app.services.appointment_booking import AppointmentBookingService
from app.services.chat_receptionist import (
    ChatMessageInput,
    ChatReceptionistIntent,
    ChatReceptionistService,
)
from app.services.conversations import ConversationService
from app.services.scheduling import SchedulingService
from tests.test_chat_booking_confirmation_flow import (
    FULL_IDENTITY_WITH_CONFIRM,
    _conversation_with_active_hold,
    create_jane_doe_patient,
)
from tests.test_chat_receptionist_service import (
    FakeAppointmentHoldService,
    TrackingAppointmentBookingService,
    _create_hold_service,
    create_appointment_booking_service_for_scheduling,
    create_chat_receptionist_service,
)
from tests.test_conversations import FakeConversationRepository
from tests.test_retell_call_lifecycle_service import (
    test_call_start_event_creates_voice_call_and_event,
)
from tests.test_retell_tool_adapter import (
    AdapterBundle,
    test_hold_appointment_slot_creates_only_hold_not_appointment,
)
from tests.test_retell_webhook_security import test_valid_signature_allows_route_to_continue
from tests.test_scheduling_services import (
    EMILY_JULY_SLOT_1_ID,
    FakeAppointmentRepository,
    create_demo_scheduling_service_with_emily_july_availability,
)

pytest_plugins = [
    "tests.test_retell_tool_adapter",
]


def _create_booking_flow_context() -> tuple[
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


def test_regression_chat_booking_flow_still_books() -> None:
    service, tracking_booking, _hold_service, scheduling = _create_booking_flow_context()
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


def test_regression_retell_webhook_security_still_validates_signatures() -> None:
    test_valid_signature_allows_route_to_continue()


def test_regression_retell_lifecycle_still_creates_voice_call_and_event() -> None:
    test_call_start_event_creates_voice_call_and_event()


def test_regression_retell_tool_adapter_hold_still_creates_only_hold(
    adapter_bundle: AdapterBundle,
) -> None:
    test_hold_appointment_slot_creates_only_hold_not_appointment(adapter_bundle)
