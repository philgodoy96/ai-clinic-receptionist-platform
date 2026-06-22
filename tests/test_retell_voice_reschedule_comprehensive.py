"""Comprehensive coverage for the Retell voice reschedule_appointment tool."""

from __future__ import annotations

import inspect
from typing import cast
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import app.services.retell_tool_adapter as retell_tool_adapter_module
from app.api.dependencies import get_retell_tool_calling_adapter
from app.domain.retell_tools import (
    ParsedRetellToolCall,
    RetellSupportedToolName,
    parse_retell_tool_call,
)
from app.domain.scheduling.enums import AppointmentStatus
from app.domain.voice_conversation import read_last_reschedule_summary, read_voice_context
from app.domain.voice_rescheduling import is_reschedule_appointment_executable
from app.main import create_app
from app.schemas.retell_tools import (
    MAX_RESCHEDULE_APPOINTMENT_CONFIRMATION_TEXT_LENGTH,
    MAX_RESCHEDULE_APPOINTMENT_RESCHEDULE_REASON_LENGTH,
    RescheduleAppointmentToolArguments,
    RetellProviderToolCallRequest,
    RetellToolCallRequest,
)
from app.services.appointment_booking import AppointmentBookingService
from app.services.chat_receptionist import (
    ChatMessageInput,
    ChatReceptionistIntent,
    ChatReceptionistService,
)
from app.services.conversations import ConversationService
from app.services.retell_tool_adapter import RetellToolCallingAdapter
from app.services.scheduling import SchedulingService
from app.services.voice_conversation_bridge import VoiceConversationBridgeService
from tests.retell_cancellation_test_support import (
    cancellation_tool_request,
    create_retell_cancellation_tool_context,
)
from tests.retell_rescheduling_test_support import (
    PROVIDER_CALL_ID,
    TOOL_CALL_ID,
    create_retell_rescheduling_tool_context,
    reschedule_arguments,
    reschedule_tool_request,
    valid_reschedule_arguments,
)
from tests.retell_webhook_support import (
    NeverCalledRetellToolCallingAdapter,
    configure_retell_for_tests,
    install_fake_retell_verifier,
    make_retell_enabled_settings,
    make_secured_retell_settings,
    post_retell_tool,
    retell_request_headers,
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
    create_retell_booking_tool_context,
)
from tests.test_retell_voice_booking_tool import (
    _tool_request as booking_tool_request,
)
from tests.test_scheduling_services import (
    EMILY_JULY_SLOT_1_ID,
    FakeAppointmentRepository,
    create_demo_scheduling_service_with_emily_july_availability,
)

pytest_plugins = [
    "tests.test_retell_tool_adapter",
]

_BLOCKED_RETELL_DASHBOARD_MARKERS = (
    "dashboard",
    "retell.ai",
    "configure_agent",
    "create_agent",
    "update_agent",
)


def _reschedule_request(
    *,
    original_appointment_id: str | None = None,
    hold_id: str | None = None,
    new_slot_id: str | None = None,
    explicit_confirmation: bool = True,
    tool_call_id: str = TOOL_CALL_ID,
) -> RetellToolCallRequest:
    return reschedule_tool_request(
        original_appointment_id=original_appointment_id,
        hold_id=hold_id,
        new_slot_id=new_slot_id,
        explicit_confirmation=explicit_confirmation,
        tool_call_id=tool_call_id,
    )


def _create_chat_booking_flow_context() -> tuple[
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


# --- Schema validation ---


def test_schema_valid_args_parse() -> None:
    appointment_id = str(uuid4())
    hold_id = str(uuid4())

    arguments = RescheduleAppointmentToolArguments.model_validate(
        valid_reschedule_arguments(
            original_appointment_id=appointment_id,
            hold_id=hold_id,
            new_slot_id=None,
        ),
    )

    assert str(arguments.original_appointment_id) == appointment_id
    assert arguments.hold_id == hold_id
    assert arguments.explicit_confirmation is True
    assert is_reschedule_appointment_executable(arguments) is True


def test_schema_valid_tool_call_parses_through_retell_adapter() -> None:
    appointment_id = str(uuid4())
    hold_id = str(uuid4())

    parsed = parse_retell_tool_call(
        RetellProviderToolCallRequest.model_validate(
            {
                "provider_call_id": "retell-call-reschedule-schema",
                "tool_call_id": "tool-call-reschedule-schema",
                "tool_name": "reschedule_appointment",
                "arguments": valid_reschedule_arguments(
                    original_appointment_id=appointment_id,
                    hold_id=hold_id,
                ),
            },
        ),
    )

    assert isinstance(parsed, ParsedRetellToolCall)
    assert parsed.tool_name is RetellSupportedToolName.RESCHEDULE_APPOINTMENT
    assert isinstance(parsed.arguments, RescheduleAppointmentToolArguments)


def test_schema_missing_explicit_confirmation_rejected() -> None:
    with pytest.raises(ValidationError):
        RescheduleAppointmentToolArguments.model_validate(
            {
                "hold_id": str(uuid4()),
                "confirmation_text": "Yes, reschedule it.",
            },
        )


def test_schema_missing_confirmation_false_is_not_executable() -> None:
    arguments = RescheduleAppointmentToolArguments.model_validate(
        valid_reschedule_arguments(explicit_confirmation=False),
    )

    assert is_reschedule_appointment_executable(arguments) is False


def test_schema_missing_hold_and_new_slot_rejected() -> None:
    with pytest.raises(ValidationError, match="either hold_id or new_slot_id is required"):
        RescheduleAppointmentToolArguments.model_validate(
            {
                "original_appointment_id": str(uuid4()),
                "explicit_confirmation": True,
            },
        )


def test_schema_confirmation_text_bounded() -> None:
    with pytest.raises(ValidationError):
        RescheduleAppointmentToolArguments.model_validate(
            valid_reschedule_arguments(
                confirmation_text="x" * (MAX_RESCHEDULE_APPOINTMENT_CONFIRMATION_TEXT_LENGTH + 1),
            ),
        )


def test_schema_reschedule_reason_bounded() -> None:
    with pytest.raises(ValidationError):
        RescheduleAppointmentToolArguments.model_validate(
            valid_reschedule_arguments(
                reschedule_reason="x" * (MAX_RESCHEDULE_APPOINTMENT_RESCHEDULE_REASON_LENGTH + 1),
            ),
        )


def test_schema_blocked_transcript_fields_rejected() -> None:
    payload = valid_reschedule_arguments()
    payload["transcript"] = "Patient said yes please reschedule"

    with pytest.raises(ValidationError):
        RescheduleAppointmentToolArguments.model_validate(payload)


# --- Adapter and route ---


def test_route_invalid_signature_prevents_rescheduling() -> None:
    app = create_app()
    settings = make_secured_retell_settings()
    configure_retell_for_tests(app, settings=settings)
    install_fake_retell_verifier(app)
    tracking_adapter = NeverCalledRetellToolCallingAdapter()
    app.dependency_overrides[get_retell_tool_calling_adapter] = lambda: tracking_adapter

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/retell/tools",
            content=(
                b'{"provider_call_id":"retell-call-123","tool_name":"reschedule_appointment",'
                b'"tool_call_id":"tool-call-1","arguments":{"original_appointment_id":"'
                + str(uuid4()).encode()
                + b'","hold_id":"'
                + str(uuid4()).encode()
                + b'","explicit_confirmation":true}}'
            ),
            headers={
                "Content-Type": "application/json",
                **retell_request_headers(signature="v=1,d=invalid"),
            },
        )

    app.dependency_overrides.clear()

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "retell_signature_invalid"
    assert tracking_adapter.execute_calls == []


def test_route_retell_disabled_rejects_reschedule() -> None:
    app = create_app()
    settings = make_retell_enabled_settings(RETELL_ENABLED=False)
    configure_retell_for_tests(app, settings=settings)
    tracking_adapter = NeverCalledRetellToolCallingAdapter()
    app.dependency_overrides[get_retell_tool_calling_adapter] = lambda: tracking_adapter

    with TestClient(app) as client:
        response = post_retell_tool(
            client,
            "/api/v1/retell/tools",
            settings=settings,
            json_body={
                "provider_call_id": PROVIDER_CALL_ID,
                "tool_call_id": TOOL_CALL_ID,
                "tool_name": "reschedule_appointment",
                "arguments": reschedule_arguments(
                    original_appointment_id=str(uuid4()),
                    hold_id=str(uuid4()),
                ),
            },
        )

    app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "retell_disabled"
    assert tracking_adapter.execute_calls == []


def test_adapter_valid_verified_reschedule_succeeds() -> None:
    context = create_retell_rescheduling_tool_context()
    original_appointment = context["original_appointment"]
    hold = context["hold"]

    response = context["adapter"].execute(
        _reschedule_request(
            original_appointment_id=str(original_appointment.id),
            hold_id=str(hold.hold_id),
            new_slot_id=str(context["new_slot"].id),
        ),
    )

    assert response.status == "succeeded"
    assert response.result["original_appointment_id"] == str(original_appointment.id)
    assert response.result["status"] == AppointmentStatus.SCHEDULED.value
    assert response.duplicate is False


def test_route_verified_reschedule_returns_provider_safe_response() -> None:
    context = create_retell_rescheduling_tool_context()
    original_appointment = context["original_appointment"]
    hold = context["hold"]

    app = create_app()
    settings = make_secured_retell_settings()
    configure_retell_for_tests(app, settings=settings)
    install_fake_retell_verifier(app, accept_all=True)
    app.dependency_overrides[get_retell_tool_calling_adapter] = lambda: context["adapter"]

    with TestClient(app) as client:
        response = post_retell_tool(
            client,
            "/api/v1/retell/tools",
            settings=settings,
            json_body={
                "provider_call_id": PROVIDER_CALL_ID,
                "tool_call_id": TOOL_CALL_ID,
                "tool_name": "reschedule_appointment",
                "arguments": reschedule_arguments(
                    original_appointment_id=str(original_appointment.id),
                    hold_id=str(hold.hold_id),
                    new_slot_id=str(context["new_slot"].id),
                ),
            },
        )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["result"]["original_appointment_id"] == str(original_appointment.id)
    assert "traceback" not in str(body).lower()
    assert "webhook_secret" not in str(body).lower()
    assert "transcript" not in str(body).lower()


def test_adapter_missing_appointment_reference_rejected() -> None:
    context = create_retell_rescheduling_tool_context()
    conversation = context["conversation"]
    hold = context["hold"]
    conversation.appointment_id = None
    conversation.conversation_metadata = {
        "voice_context": {
            "hold_id": str(hold.hold_id),
            "availability_slot_id": str(context["new_slot"].id),
        },
    }

    response = context["adapter"].execute(
        _reschedule_request(
            original_appointment_id=None,
            hold_id=str(hold.hold_id),
            new_slot_id=str(context["new_slot"].id),
        ),
    )

    assert response.status == "failed"
    assert response.error_code == "appointment_reference_required"
    assert context["tracking_rescheduling"].reschedule_calls == []


def test_adapter_missing_hold_and_slot_rejected() -> None:
    context = create_retell_rescheduling_tool_context()
    original_appointment = context["original_appointment"]

    response = context["adapter"].execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": PROVIDER_CALL_ID,
                "tool_call_id": "tool-call-reschedule-missing-target",
                "tool_name": "reschedule_appointment",
                "arguments": {
                    "original_appointment_id": str(original_appointment.id),
                    "explicit_confirmation": True,
                    "confirmation_text": "Yes, reschedule it.",
                },
            },
        ),
    )

    assert response.status == "rejected"
    assert response.error_code == "retell_tool_arguments_invalid"
    assert context["tracking_rescheduling"].reschedule_calls == []


def test_adapter_missing_confirmation_rejected() -> None:
    context = create_retell_rescheduling_tool_context()
    original_appointment = context["original_appointment"]
    hold = context["hold"]

    response = context["adapter"].execute(
        _reschedule_request(
            original_appointment_id=str(original_appointment.id),
            hold_id=str(hold.hold_id),
            new_slot_id=str(context["new_slot"].id),
            explicit_confirmation=False,
        ),
    )

    assert response.status == "failed"
    assert response.error_code == "reschedule_confirmation_required"
    assert context["tracking_rescheduling"].reschedule_calls == []


def test_adapter_duplicate_provider_callback_is_idempotent() -> None:
    context = create_retell_rescheduling_tool_context()
    original_appointment = context["original_appointment"]
    hold = context["hold"]
    request = _reschedule_request(
        original_appointment_id=str(original_appointment.id),
        hold_id=str(hold.hold_id),
        new_slot_id=str(context["new_slot"].id),
    )

    first = context["adapter"].execute(request)
    second = context["adapter"].execute(request)

    assert first.status == "succeeded"
    assert second.status == "succeeded"
    assert second.duplicate is True
    assert len(context["tracking_rescheduling"].reschedule_calls) == 1
    assert second.result["new_appointment_id"] == first.result["new_appointment_id"]
    assert second.result["original_appointment_id"] == first.result["original_appointment_id"]


def test_adapter_unknown_tool_rejected() -> None:
    context = create_retell_rescheduling_tool_context()

    response = context["adapter"].execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": PROVIDER_CALL_ID,
                "tool_name": "delete_appointment",
                "arguments": {},
            },
        ),
    )

    assert response.status == "rejected"
    assert response.error_code == "unsupported_retell_tool"
    assert context["tracking_rescheduling"].reschedule_calls == []


# --- Service integration ---


def test_service_called_exactly_once_for_valid_non_duplicate_request() -> None:
    context = create_retell_rescheduling_tool_context()
    original_appointment = context["original_appointment"]
    hold = context["hold"]

    response = context["adapter"].execute(
        _reschedule_request(
            original_appointment_id=str(original_appointment.id),
            hold_id=str(hold.hold_id),
            new_slot_id=str(context["new_slot"].id),
        ),
    )

    assert response.status == "succeeded"
    assert len(context["tracking_rescheduling"].reschedule_calls) == 1
    assert (
        context["tracking_rescheduling"].reschedule_calls[0].appointment_id
        == original_appointment.id
    )


def test_adapter_does_not_update_appointment_directly() -> None:
    dispatch_source = inspect.getsource(
        RetellToolCallingAdapter._execute_reschedule_appointment,
    )
    module_source = inspect.getsource(retell_tool_adapter_module)

    assert ".status =" not in dispatch_source
    assert "rescheduled_from_appointment_id" not in dispatch_source
    assert "appointment.status =" not in module_source.split(
        "def _execute_reschedule_appointment",
    )[1].split("def _build_reschedule_appointment_idempotency_key")[0]


# --- Conversation metadata ---


def test_metadata_latest_appointment_summary_updated() -> None:
    context = create_retell_rescheduling_tool_context()
    original_appointment = context["original_appointment"]
    hold = context["hold"]
    conversation = context["conversation"]
    conversation.conversation_metadata["locale"] = "en-US"

    response = context["adapter"].execute(
        _reschedule_request(
            original_appointment_id=str(original_appointment.id),
            hold_id=str(hold.hold_id),
            new_slot_id=str(context["new_slot"].id),
        ),
    )

    stored = context["conversation_repository"].get_by_id(conversation.id)
    assert stored is not None
    voice_context = read_voice_context(stored.conversation_metadata)
    summary = read_last_reschedule_summary(stored.conversation_metadata)

    assert response.status == "succeeded"
    assert voice_context["appointment_id"] == str(response.result["new_appointment_id"])
    assert voice_context["rescheduled_from_appointment_id"] == str(original_appointment.id)
    assert stored.conversation_metadata["locale"] == "en-US"
    assert summary is not None
    assert summary.new_appointment_id == str(response.result["new_appointment_id"])


def test_metadata_active_hold_cleared_on_success() -> None:
    context = create_retell_rescheduling_tool_context()
    original_appointment = context["original_appointment"]
    hold = context["hold"]

    response = context["adapter"].execute(
        _reschedule_request(
            original_appointment_id=str(original_appointment.id),
            hold_id=str(hold.hold_id),
            new_slot_id=str(context["new_slot"].id),
        ),
    )

    assert response.status == "succeeded"
    bridge_context = context["bridge"].get_context_for_provider_call("retell", PROVIDER_CALL_ID)
    assert bridge_context.active_hold is None


def test_metadata_duplicate_callback_does_not_corrupt_metadata() -> None:
    context = create_retell_rescheduling_tool_context()
    original_appointment = context["original_appointment"]
    hold = context["hold"]
    conversation = context["conversation"]
    request = _reschedule_request(
        original_appointment_id=str(original_appointment.id),
        hold_id=str(hold.hold_id),
        new_slot_id=str(context["new_slot"].id),
    )

    context["adapter"].execute(request)
    stored_after_first = context["conversation_repository"].get_by_id(conversation.id)
    assert stored_after_first is not None
    voice_context_after_first = read_voice_context(stored_after_first.conversation_metadata)
    summary_after_first = read_last_reschedule_summary(
        stored_after_first.conversation_metadata,
    )

    context["adapter"].execute(request)

    stored_after_second = context["conversation_repository"].get_by_id(conversation.id)
    assert stored_after_second is not None
    voice_context_after_second = read_voice_context(stored_after_second.conversation_metadata)
    summary_after_second = read_last_reschedule_summary(
        stored_after_second.conversation_metadata,
    )

    assert voice_context_after_second == voice_context_after_first
    assert summary_after_second == summary_after_first


def test_debug_context_returns_safe_reschedule_summary() -> None:
    context = create_retell_rescheduling_tool_context()
    original_appointment = context["original_appointment"]
    hold = context["hold"]

    response = context["adapter"].execute(
        _reschedule_request(
            original_appointment_id=str(original_appointment.id),
            hold_id=str(hold.hold_id),
            new_slot_id=str(context["new_slot"].id),
            tool_call_id="tool-call-reschedule-debug",
        ),
    )

    debug_context = context["bridge"].get_debug_context_for_voice_call(context["voice_call"].id)

    assert response.status == "succeeded"
    assert debug_context.last_reschedule_summary is not None
    assert debug_context.last_reschedule_summary.status == "succeeded"
    assert debug_context.last_reschedule_summary.original_appointment_id == str(
        original_appointment.id,
    )
    serialized = repr(debug_context)
    assert "transcript" not in serialized
    assert "webhook_secret" not in serialized


# --- Regression: other Retell tools and chat flows ---


def test_regression_check_availability_still_works(adapter_bundle: AdapterBundle) -> None:
    response = adapter_bundle.adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": "retell-call-reschedule-regression-availability",
                "tool_name": "check_availability",
                "arguments": {
                    "doctor_id": str(adapter_bundle.doctor_id),
                    "start_from": "2026-07-01T09:00:00Z",
                    "start_to": "2026-07-01T12:00:00Z",
                    "limit": 1,
                },
            },
        ),
    )

    assert response.status == "succeeded"
    assert len(adapter_bundle.scheduling_service.check_availability_calls) == 1


def test_regression_hold_appointment_slot_still_works(adapter_bundle: AdapterBundle) -> None:
    response = adapter_bundle.adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": "retell-call-reschedule-regression-hold",
                "tool_call_id": "hold-reschedule-regression-1",
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


def test_regression_book_appointment_still_works() -> None:
    context = create_retell_booking_tool_context()
    booking_context = context["booking_context"]
    hold_id = _hold_id(booking_context)

    response = context["adapter"].execute(
        booking_tool_request(hold_id=hold_id, slot_id=str(booking_context.slot.id)),
    )

    assert response.status == "succeeded"
    assert len(context["tracking_booking"].book_calls) == 1
    assert response.result["status"] == "scheduled"


def test_regression_cancel_appointment_still_works() -> None:
    context = create_retell_cancellation_tool_context()
    appointment = context["appointment"]

    response = context["adapter"].execute(
        cancellation_tool_request(
            appointment_id=str(appointment.id),
            tool_call_id="tool-call-cancel-reschedule-regression",
        ),
    )

    assert response.status == "succeeded"
    assert len(context["tracking_cancellation"].cancel_calls) == 1
    assert response.result["status"] == AppointmentStatus.CANCELLED.value


def test_regression_chat_booking_still_works() -> None:
    service, tracking_booking, _hold_service, scheduling = _create_chat_booking_flow_context()
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
    assert len(tracking_booking.book_calls) == 1
    assert result.conversation.conversation_metadata["chat_context"]["appointment_id"]
    assert appointments.appointments[0].availability_slot_id == EMILY_JULY_SLOT_1_ID


def test_regression_reschedule_does_not_trigger_llm() -> None:
    context = create_retell_rescheduling_tool_context()
    original_appointment = context["original_appointment"]
    hold = context["hold"]

    with patch("app.ai.provider_factory.build_llm_provider") as llm_factory_mock:
        response = context["adapter"].execute(
            _reschedule_request(
                original_appointment_id=str(original_appointment.id),
                hold_id=str(hold.hold_id),
                new_slot_id=str(context["new_slot"].id),
            ),
        )

    assert response.status == "succeeded"
    llm_factory_mock.assert_not_called()


def test_regression_reschedule_has_no_retell_dashboard_dependency() -> None:
    adapter_source = inspect.getsource(RetellToolCallingAdapter._execute_reschedule_appointment)
    bridge_source = inspect.getsource(
        VoiceConversationBridgeService.record_successful_reschedule_context,
    )

    combined = f"{adapter_source}\n{bridge_source}".lower()
    for marker in _BLOCKED_RETELL_DASHBOARD_MARKERS:
        assert marker not in combined
