from __future__ import annotations

import inspect
from uuid import uuid4

from fastapi.testclient import TestClient

import app.services.retell_tool_adapter as retell_tool_adapter_module
from app.api.dependencies import get_retell_tool_calling_adapter
from app.domain.scheduling.enums import AppointmentStatus
from app.main import create_app
from app.schemas.retell_tools import RetellToolCallRequest
from app.services.retell_tool_adapter import RetellToolCallingAdapter
from tests.retell_rescheduling_test_support import (
    PROVIDER_CALL_ID,
    TOOL_CALL_ID,
    create_retell_rescheduling_tool_context,
    reschedule_arguments,
    reschedule_tool_request,
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


def _tool_request(
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


def test_valid_verified_reschedule_calls_rescheduling_service() -> None:
    context = create_retell_rescheduling_tool_context()
    original_appointment = context["original_appointment"]
    hold = context["hold"]

    response = context["adapter"].execute(
        _tool_request(
            original_appointment_id=str(original_appointment.id),
            hold_id=str(hold.hold_id),
            new_slot_id=str(context["new_slot"].id),
        ),
    )

    assert response.status == "succeeded"
    assert len(context["tracking_rescheduling"].reschedule_calls) == 1
    assert original_appointment.status == AppointmentStatus.RESCHEDULED
    assert response.result["original_appointment_id"] == str(original_appointment.id)
    assert response.result["status"] == AppointmentStatus.SCHEDULED.value
    assert response.duplicate is False


def test_reschedule_resolves_appointment_from_voice_context_without_arg() -> None:
    context = create_retell_rescheduling_tool_context()
    original_appointment = context["original_appointment"]
    hold = context["hold"]

    response = context["adapter"].execute(
        _tool_request(
            original_appointment_id=None,
            hold_id=str(hold.hold_id),
            new_slot_id=str(context["new_slot"].id),
            tool_call_id="tool-call-reschedule-context",
        ),
    )

    assert response.status == "succeeded"
    assert response.result["original_appointment_id"] == str(original_appointment.id)
    assert len(context["tracking_rescheduling"].reschedule_calls) == 1
    assert (
        context["tracking_rescheduling"].reschedule_calls[0].appointment_id
        == original_appointment.id
    )


def test_missing_appointment_reference_rejected() -> None:
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
        _tool_request(
            original_appointment_id=None,
            hold_id=str(hold.hold_id),
            new_slot_id=str(context["new_slot"].id),
        ),
    )

    assert response.status == "failed"
    assert response.error_code == "appointment_reference_required"
    assert context["tracking_rescheduling"].reschedule_calls == []


def test_missing_hold_and_slot_rejected() -> None:
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


def test_missing_confirmation_rejected() -> None:
    context = create_retell_rescheduling_tool_context()
    original_appointment = context["original_appointment"]
    hold = context["hold"]

    response = context["adapter"].execute(
        _tool_request(
            original_appointment_id=str(original_appointment.id),
            hold_id=str(hold.hold_id),
            new_slot_id=str(context["new_slot"].id),
            explicit_confirmation=False,
        ),
    )

    assert response.status == "failed"
    assert response.error_code == "reschedule_confirmation_required"
    assert context["tracking_rescheduling"].reschedule_calls == []


def test_duplicate_tool_callback_is_idempotent() -> None:
    context = create_retell_rescheduling_tool_context()
    original_appointment = context["original_appointment"]
    hold = context["hold"]
    request = _tool_request(
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
    assert len(context["rescheduling_context"].attempt_repository.attempts) == 1


def test_unknown_tool_still_rejected() -> None:
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


def test_adapter_does_not_update_appointment_directly() -> None:
    dispatch_source = inspect.getsource(
        RetellToolCallingAdapter._execute_reschedule_appointment,
    )

    assert ".status =" not in dispatch_source
    assert "rescheduled_from_appointment_id" not in dispatch_source


def test_success_response_has_no_raw_payload_or_secrets() -> None:
    context = create_retell_rescheduling_tool_context()
    original_appointment = context["original_appointment"]
    hold = context["hold"]

    response = context["adapter"].execute(
        _tool_request(
            original_appointment_id=str(original_appointment.id),
            hold_id=str(hold.hold_id),
            new_slot_id=str(context["new_slot"].id),
        ),
    )

    serialized = response.model_dump(mode="json")
    payload_text = str(serialized).lower()

    assert response.status == "succeeded"
    assert "traceback" not in payload_text
    assert "webhook_secret" not in payload_text
    assert "api_key" not in payload_text
    assert "transcript" not in payload_text


def test_invalid_signature_prevents_rescheduling() -> None:
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


def test_verified_route_reschedule_returns_provider_safe_response() -> None:
    context = create_retell_rescheduling_tool_context()
    original_appointment = context["original_appointment"]
    hold = context["hold"]

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
    assert body["result"]["status"] == AppointmentStatus.SCHEDULED.value
    assert "traceback" not in str(body).lower()


def test_retell_disabled_rejects_reschedule_route() -> None:
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


def test_adapter_dispatch_does_not_mutate_appointment_in_reschedule_path() -> None:
    module_source = inspect.getsource(retell_tool_adapter_module)

    assert "appointment.status =" not in module_source.split(
        "def _execute_reschedule_appointment",
    )[1].split("def _build_reschedule_appointment_idempotency_key")[0]
