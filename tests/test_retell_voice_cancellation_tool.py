from __future__ import annotations

import inspect
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

import app.services.retell_tool_adapter as retell_tool_adapter_module
from app.api.dependencies import get_retell_tool_calling_adapter
from app.domain.appointments import (
    AppointmentCancellationRequest,
    AppointmentCancellationResult,
)
from app.domain.conversations.enums import ConversationChannel, ConversationStatus
from app.domain.scheduling.enums import AppointmentStatus
from app.domain.voice_calls.enums import VoiceCallStatus
from app.main import create_app
from app.models.conversations import Conversation
from app.models.voice_calls import VoiceCall
from app.schemas.retell_tools import RetellToolCallRequest
from app.services.appointment_cancellation import AppointmentCancellationService
from app.services.conversations import ConversationService
from app.services.retell_tool_adapter import RetellToolCallingAdapter
from app.services.scheduling import SchedulingService
from app.services.voice_conversation_bridge import VoiceConversationBridgeService
from tests.retell_webhook_support import (
    configure_retell_for_tests,
    install_fake_retell_verifier,
    make_retell_enabled_settings,
    make_secured_retell_settings,
    post_retell_tool,
    retell_request_headers,
)
from tests.test_appointment_booking_service import create_booking_context
from tests.test_appointment_cancellation_service import create_cancellation_context
from tests.test_conversations import FakeConversationRepository
from tests.test_retell_call_lifecycle_service import FakeVoiceCallRepository
from tests.test_retell_tool_adapter import TrackingVoiceCallRepository
from tests.test_retell_tool_route import NeverCalledRetellToolCallingAdapter
from tests.test_scheduling_services import FakeSpecialtyRepository

PROVIDER_CALL_ID = "retell-call-cancel-1"
TOOL_CALL_ID = "tool-call-cancel-1"


class TrackingAppointmentCancellationService:
    def __init__(self, inner: AppointmentCancellationService) -> None:
        self.inner = inner
        self.cancel_calls: list[AppointmentCancellationRequest] = []

    def cancel_appointment(
        self,
        request: AppointmentCancellationRequest,
    ) -> AppointmentCancellationResult:
        self.cancel_calls.append(request)
        return self.inner.cancel_appointment(request)


def _cancellation_arguments(
    *,
    appointment_id: str | None = None,
    explicit_confirmation: bool = True,
    tool_call_id: str = TOOL_CALL_ID,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "explicit_confirmation": explicit_confirmation,
        "confirmation_text": "Yes, please cancel it.",
        "cancellation_reason": "Patient requested cancellation",
    }
    if appointment_id is not None:
        payload["appointment_id"] = appointment_id
    return payload


def _tool_request(
    *,
    appointment_id: str | None = None,
    explicit_confirmation: bool = True,
    tool_call_id: str = TOOL_CALL_ID,
) -> RetellToolCallRequest:
    return RetellToolCallRequest.model_validate(
        {
            "provider_call_id": PROVIDER_CALL_ID,
            "tool_call_id": tool_call_id,
            "tool_name": "cancel_appointment",
            "arguments": _cancellation_arguments(
                appointment_id=appointment_id,
                explicit_confirmation=explicit_confirmation,
                tool_call_id=tool_call_id,
            ),
        },
    )


def create_retell_cancellation_tool_context(
    *,
    status: AppointmentStatus = AppointmentStatus.SCHEDULED,
) -> dict[str, Any]:
    cancellation_context = create_cancellation_context(status=status)
    appointment = cancellation_context.appointment
    booking_context = create_booking_context()

    conversation_repository = FakeConversationRepository()
    conversation = Conversation(
        id=uuid4(),
        channel=ConversationChannel.VOICE,
        status=ConversationStatus.ACTIVE,
        external_conversation_id=PROVIDER_CALL_ID,
        call_id=PROVIDER_CALL_ID,
        appointment_id=appointment.id,
        conversation_metadata={
            "voice_context": {
                "appointment_id": str(appointment.id),
            },
        },
    )
    conversation_repository.conversations.append(conversation)

    voice_calls = FakeVoiceCallRepository()
    voice_call = VoiceCall(
        id=uuid4(),
        provider="retell",
        provider_call_id=PROVIDER_CALL_ID,
        status=VoiceCallStatus.IN_PROGRESS,
        conversation_id=conversation.id,
        created_at=datetime(2026, 6, 25, 10, 0, tzinfo=UTC),
        updated_at=datetime(2026, 6, 25, 10, 0, tzinfo=UTC),
    )
    voice_calls.voice_calls.append(voice_call)

    conversations = ConversationService(repository=conversation_repository)
    bridge = VoiceConversationBridgeService(
        voice_calls=voice_calls,
        conversations=conversation_repository,
        conversation_service=conversations,
    )

    tracking_cancellation = TrackingAppointmentCancellationService(
        cancellation_context.service,
    )
    tracking_voice_calls = TrackingVoiceCallRepository()
    tracking_voice_calls.voice_calls[PROVIDER_CALL_ID] = voice_call

    scheduling_service = SchedulingService(
        specialties=FakeSpecialtyRepository([]),
        doctors=booking_context.booking_service.doctors,
        patients=booking_context.booking_service.patients,
        availability_slots=booking_context.booking_service.availability_slots,
        appointments=cancellation_context.appointment_repository,
    )

    adapter = RetellToolCallingAdapter(
        scheduling_service=scheduling_service,
        hold_service=booking_context.hold_service,
        voice_calls=tracking_voice_calls,
        voice_conversation_bridge=bridge,
        conversations=conversations,
        appointment_cancellation=tracking_cancellation,
        appointments=cancellation_context.appointment_repository,
    )

    return {
        "adapter": adapter,
        "tracking_cancellation": tracking_cancellation,
        "cancellation_context": cancellation_context,
        "conversation": conversation,
        "conversation_repository": conversation_repository,
        "appointment": appointment,
    }


def test_valid_verified_cancellation_cancels_through_service() -> None:
    context = create_retell_cancellation_tool_context()
    appointment = context["appointment"]

    response = context["adapter"].execute(
        _tool_request(appointment_id=str(appointment.id)),
    )

    assert response.status == "succeeded"
    assert len(context["tracking_cancellation"].cancel_calls) == 1
    assert appointment.status == AppointmentStatus.CANCELLED
    assert response.result["status"] == AppointmentStatus.CANCELLED.value
    assert response.result["appointment_id"] == str(appointment.id)
    assert response.duplicate is False

    stored_conversation = context["conversation_repository"].get_by_id(
        context["conversation"].id,
    )
    assert stored_conversation is not None
    voice_context = stored_conversation.conversation_metadata["voice_context"]
    assert voice_context["appointment_status"] == AppointmentStatus.CANCELLED.value
    assert voice_context["appointment_id"] == str(appointment.id)


def test_missing_appointment_reference_rejected() -> None:
    context = create_retell_cancellation_tool_context()
    conversation = context["conversation"]
    conversation.appointment_id = None
    conversation.conversation_metadata = {"voice_context": {}}

    response = context["adapter"].execute(
        _tool_request(appointment_id=None),
    )

    assert response.status == "failed"
    assert response.error_code == "appointment_reference_required"
    assert context["tracking_cancellation"].cancel_calls == []


def test_missing_confirmation_rejected() -> None:
    context = create_retell_cancellation_tool_context()
    appointment = context["appointment"]

    response = context["adapter"].execute(
        _tool_request(
            appointment_id=str(appointment.id),
            explicit_confirmation=False,
        ),
    )

    assert response.status == "failed"
    assert response.error_code == "cancellation_confirmation_required"
    assert context["tracking_cancellation"].cancel_calls == []


def test_duplicate_tool_callback_does_not_duplicate_side_effects() -> None:
    context = create_retell_cancellation_tool_context()
    appointment = context["appointment"]
    request = _tool_request(appointment_id=str(appointment.id))

    first = context["adapter"].execute(request)
    second = context["adapter"].execute(request)

    assert first.status == "succeeded"
    assert second.status == "succeeded"
    assert second.duplicate is True
    assert len(context["tracking_cancellation"].cancel_calls) == 1
    assert len(context["cancellation_context"].attempt_repository.attempts) == 1


def test_already_cancelled_appointment_returns_safe_result() -> None:
    context = create_retell_cancellation_tool_context(
        status=AppointmentStatus.CANCELLED,
    )
    appointment = context["appointment"]
    appointment.cancelled_at = datetime(2026, 6, 20, 9, 0, tzinfo=UTC)

    response = context["adapter"].execute(
        _tool_request(
            appointment_id=str(appointment.id),
            tool_call_id="tool-call-cancel-already",
        ),
    )

    assert response.status == "succeeded"
    assert response.result["already_cancelled"] is True
    assert response.result["status"] == AppointmentStatus.CANCELLED.value
    assert len(context["tracking_cancellation"].cancel_calls) == 1


def test_appointment_row_remains_after_cancellation() -> None:
    context = create_retell_cancellation_tool_context()
    appointment = context["appointment"]
    appointment_id = appointment.id

    context["adapter"].execute(_tool_request(appointment_id=str(appointment_id)))

    stored = context["cancellation_context"].appointment_repository.get_by_id(appointment_id)
    assert stored is not None
    assert stored.status == AppointmentStatus.CANCELLED
    assert len(context["cancellation_context"].appointment_repository.appointments) == 1


def test_unknown_tool_still_rejected() -> None:
    context = create_retell_cancellation_tool_context()

    response = context["adapter"].execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": PROVIDER_CALL_ID,
                "tool_name": "reschedule_appointment",
                "arguments": {},
            },
        ),
    )

    assert response.status == "rejected"
    assert response.error_code == "unsupported_retell_tool"
    assert context["tracking_cancellation"].cancel_calls == []


def test_adapter_does_not_update_appointment_directly() -> None:
    dispatch_source = inspect.getsource(RetellToolCallingAdapter._execute_cancel_appointment)

    assert ".status =" not in dispatch_source
    assert "cancelled_at" not in dispatch_source


def test_success_response_has_no_raw_payload_or_secrets() -> None:
    context = create_retell_cancellation_tool_context()
    appointment = context["appointment"]

    response = context["adapter"].execute(
        _tool_request(appointment_id=str(appointment.id)),
    )

    serialized = response.model_dump(mode="json")
    payload_text = str(serialized).lower()

    assert response.status == "succeeded"
    assert "traceback" not in payload_text
    assert "webhook_secret" not in payload_text
    assert "api_key" not in payload_text
    assert "transcript" not in payload_text


def test_invalid_signature_prevents_cancellation() -> None:
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
                b'{"provider_call_id":"retell-call-123","tool_name":"cancel_appointment",'
                b'"tool_call_id":"tool-call-1","arguments":{"appointment_id":"'
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


def test_verified_route_cancellation_returns_provider_safe_response() -> None:
    context = create_retell_cancellation_tool_context()
    appointment = context["appointment"]

    app = create_app()
    settings = make_secured_retell_settings()
    configure_retell_for_tests(app, settings=settings)
    install_fake_retell_verifier(app, accept_all=True)

    app.dependency_overrides[get_retell_tool_calling_adapter] = (
        lambda: context["adapter"]
    )

    with TestClient(app) as client:
        response = post_retell_tool(
            client,
            "/api/v1/retell/tools",
            settings=settings,
            json_body={
                "provider_call_id": PROVIDER_CALL_ID,
                "tool_call_id": TOOL_CALL_ID,
                "tool_name": "cancel_appointment",
                "arguments": _cancellation_arguments(appointment_id=str(appointment.id)),
            },
        )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["result"]["appointment_id"] == str(appointment.id)
    assert body["result"]["status"] == AppointmentStatus.CANCELLED.value
    assert "traceback" not in str(body).lower()


def test_retell_disabled_rejects_cancellation_route() -> None:
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
                "tool_name": "cancel_appointment",
                "arguments": _cancellation_arguments(appointment_id=str(uuid4())),
            },
        )

    app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "retell_disabled"
    assert tracking_adapter.execute_calls == []


def test_cancellation_tool_does_not_trigger_llm() -> None:
    context = create_retell_cancellation_tool_context()
    appointment = context["appointment"]

    with patch("app.ai.provider_factory.build_llm_provider") as llm_factory_mock:
        response = context["adapter"].execute(
            _tool_request(appointment_id=str(appointment.id)),
        )

    assert response.status == "succeeded"
    llm_factory_mock.assert_not_called()


def test_adapter_dispatch_does_not_mutate_appointment_in_cancel_path() -> None:
    module_source = inspect.getsource(retell_tool_adapter_module)

    assert "appointment.status =" not in module_source.split(
        "def _execute_cancel_appointment",
    )[1].split("def _build_cancel_appointment_idempotency_key")[0]
