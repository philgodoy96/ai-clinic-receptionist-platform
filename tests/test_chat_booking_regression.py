"""Regression tests: Retell voice booking must not break existing booking flows."""

from __future__ import annotations

from typing import cast
from unittest.mock import patch

from app.domain.scheduling.appointment_holds import AppointmentHold
from app.schemas.retell_tools import RetellToolCallRequest
from app.services.appointment_booking import AppointmentBookingService
from app.services.chat_receptionist import (
    ChatMessageInput,
    ChatReceptionistIntent,
    ChatReceptionistService,
)
from app.services.conversations import ConversationService
from app.services.scheduling import SchedulingService
from tests.retell_cancellation_test_support import (
    cancellation_tool_request,
    create_retell_cancellation_tool_context,
)
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
from tests.test_retell_tool_adapter import AdapterBundle
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


def test_regression_check_availability_still_works(adapter_bundle: AdapterBundle) -> None:
    response = adapter_bundle.adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": "retell-call-regression-availability",
                "tool_name": "check_availability",
                "arguments": {
                    "doctor_id": str(adapter_bundle.doctor_id),
                    "start_from": "2026-07-01T13:00:00Z",
                    "start_to": "2026-07-01T17:00:00Z",
                    "limit": 1,
                },
            },
        ),
    )

    assert response.status == "succeeded"
    assert len(adapter_bundle.scheduling_service.check_availability_calls) == 1
    assert len(response.result["available_slots"]) == 1
    assert adapter_bundle.hold_repository.create_calls == []
    assert adapter_bundle.booking_service.calls == []


def test_regression_hold_appointment_slot_still_works_without_creating_appointment(
    adapter_bundle: AdapterBundle,
) -> None:
    response = adapter_bundle.adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": "retell-call-regression-hold",
                "tool_call_id": "hold-regression-1",
                "tool_name": "hold_appointment_slot",
                "arguments": {
                    "availability_slot_id": str(adapter_bundle.availability_slot.id),
                },
            },
        ),
    )

    assert response.status == "succeeded"
    assert len(adapter_bundle.hold_repository.create_calls) == 1
    assert "hold_id" in response.result
    assert adapter_bundle.booking_service.calls == []
    assert adapter_bundle.scheduling_service.appointments == []


def test_regression_release_appointment_hold_still_works(
    adapter_bundle: AdapterBundle,
) -> None:
    hold = AppointmentHold.create(
        availability_slot_id=adapter_bundle.availability_slot.id,
        doctor_id=adapter_bundle.availability_slot.doctor_id,
        start_time=adapter_bundle.availability_slot.start_time,
        end_time=adapter_bundle.availability_slot.end_time,
        owner_id="retell-call-regression-release",
    )
    adapter_bundle.hold_repository.holds[(hold.doctor_id, hold.start_time)] = hold
    adapter_bundle.hold_repository.holds_by_id[hold.hold_id] = hold

    response = adapter_bundle.adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": "retell-call-regression-release",
                "tool_call_id": "release-regression-1",
                "tool_name": "release_appointment_hold",
                "arguments": {
                    "hold_id": str(hold.hold_id),
                },
            },
        ),
    )

    assert response.status == "succeeded"
    assert adapter_bundle.hold_repository.delete_calls == [
        (hold.doctor_id, hold.start_time),
    ]
    assert adapter_bundle.booking_service.calls == []


def test_regression_chat_booking_flow_still_works() -> None:
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


def test_regression_book_appointment_tool_still_works() -> None:
    context = create_retell_booking_tool_context()
    booking_context = context["booking_context"]
    hold_id = _hold_id(booking_context)

    response = context["adapter"].execute(
        _tool_request(hold_id=hold_id, slot_id=str(booking_context.slot.id)),
    )

    assert response.status == "succeeded"
    assert len(context["tracking_booking"].book_calls) == 1
    assert response.result["appointment_id"] is not None
    assert response.result["status"] == "scheduled"


def test_regression_cancellation_tool_does_not_enqueue_email() -> None:
    context = create_retell_cancellation_tool_context()
    appointment = context["appointment"]

    with patch(
        "app.services.email_jobs.EmailJobService.enqueue_appointment_confirmation",
    ) as enqueue_mock:
        response = context["adapter"].execute(
            cancellation_tool_request(
                appointment_id=str(appointment.id),
                patient_resolution_id=context["patient_resolution_id"],
                tool_call_id="tool-call-cancel-regression-email",
            ),
        )

    assert response.status == "succeeded"
    enqueue_mock.assert_not_called()


def test_regression_cancellation_tool_does_not_trigger_llm() -> None:
    context = create_retell_cancellation_tool_context()
    appointment = context["appointment"]

    with patch("app.ai.provider_factory.build_llm_provider") as llm_factory_mock:
        response = context["adapter"].execute(
            cancellation_tool_request(
                appointment_id=str(appointment.id),
                patient_resolution_id=context["patient_resolution_id"],
                tool_call_id="tool-call-cancel-regression-llm",
            ),
        )

    assert response.status == "succeeded"
    llm_factory_mock.assert_not_called()


def test_regression_booking_tool_does_not_trigger_llm() -> None:
    context = create_retell_booking_tool_context()
    booking_context = context["booking_context"]
    hold_id = _hold_id(booking_context)

    with patch("app.ai.provider_factory.build_llm_provider") as llm_factory_mock:
        response = context["adapter"].execute(
            _tool_request(hold_id=hold_id, slot_id=str(booking_context.slot.id)),
        )

    assert response.status == "succeeded"
    llm_factory_mock.assert_not_called()
