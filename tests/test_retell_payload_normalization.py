from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import (
    get_retell_call_lifecycle_service,
    get_retell_signature_verifier,
    get_retell_tool_calling_adapter,
)
from app.integrations.retell.payload_normalization import (
    RetellPayloadNormalizationError,
    normalize_retell_lifecycle_payload,
    normalize_retell_tool_payload,
)
from app.integrations.retell.signature import HmacRetellSignatureVerifier
from app.main import create_app
from app.schemas.retell_lifecycle import (
    RetellLifecycleWebhookRequest,
    build_retell_lifecycle_payload,
)
from app.schemas.retell_tools import RetellToolCallRequest
from app.services.appointment_holds import AppointmentHoldService
from app.services.clock import FixedClock
from app.services.retell_call_lifecycle import RetellCallLifecycleService
from app.services.retell_tool_adapter import RetellToolCallingAdapter
from tests.clinic_time_test_support import make_test_clinic_time_service
from tests.retell_webhook_support import (
    NeverCalledRetellToolCallingAdapter,
    configure_retell_for_tests,
    make_secured_retell_settings,
    post_retell_tool,
    retell_request_headers,
    sign_retell_body,
)
from tests.test_retell_call_lifecycle_service import FakeVoiceCallRepository
from tests.test_retell_lifecycle_webhook import LIFECYCLE_WEBHOOK_PATH, OCCURRED_AT
from tests.test_retell_tool_adapter import (
    TrackingAppointmentHoldRepository,
    TrackingSchedulingService,
    TrackingVoiceCallRepository,
)

WEBHOOK_SECRET = "test-webhook-secret"
TIMESTAMP_MS = 1_700_000_000_000
REFERENCE_NOW = datetime(2026, 6, 23, 12, 0, tzinfo=UTC)
START_TS_MS = int(REFERENCE_NOW.timestamp() * 1000)
END_TS_MS = int((REFERENCE_NOW.timestamp() + 60) * 1000)


def _hmac_verifier() -> HmacRetellSignatureVerifier:
    return HmacRetellSignatureVerifier(
        secret=WEBHOOK_SECRET,
        now_millis=lambda: TIMESTAMP_MS,
    )


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode()


def _native_tool_payload(
    *,
    name: str = "get_clinic_context",
    call_id: str = "call-native-1",
    args: dict[str, Any] | None = None,
    tool_call_id: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": name,
        "call": {"call_id": call_id},
        "args": args or {},
    }
    if tool_call_id is not None:
        payload["tool_call_id"] = tool_call_id
    return payload


def _normalized_tool_payload(
    *,
    tool_name: str = "get_clinic_context",
    provider_call_id: str = "call-normalized-1",
    arguments: dict[str, Any] | None = None,
    tool_call_id: str = "tool-call-1",
) -> dict[str, Any]:
    return {
        "provider_call_id": provider_call_id,
        "tool_name": tool_name,
        "tool_call_id": tool_call_id,
        "arguments": arguments or {},
    }


def _native_lifecycle_payload(
    *,
    event: str,
    call_id: str = "call-native-life-1",
    start_timestamp: int | None = START_TS_MS,
    end_timestamp: int | None = END_TS_MS,
) -> dict[str, Any]:
    call: dict[str, Any] = {"call_id": call_id}
    if start_timestamp is not None:
        call["start_timestamp"] = start_timestamp
    if end_timestamp is not None:
        call["end_timestamp"] = end_timestamp
    return {
        "event": event,
        "call": call,
    }


def _make_clinic_context_adapter() -> RetellToolCallingAdapter:
    hold_repository = TrackingAppointmentHoldRepository()
    return RetellToolCallingAdapter(
        scheduling_service=TrackingSchedulingService(
            specialties=[],
            doctors=[],
            availability_slots=[],
        ),
        hold_service=AppointmentHoldService(repository=hold_repository, ttl_seconds=300),
        voice_calls=TrackingVoiceCallRepository(),
        clinic_time_service=make_test_clinic_time_service(
            clock=FixedClock(current_time=REFERENCE_NOW),
        ),
    )


def test_normalize_native_get_clinic_context_payload() -> None:
    normalized = normalize_retell_tool_payload(_native_tool_payload())

    assert normalized["provider_call_id"] == "call-native-1"
    assert normalized["tool_name"] == "get_clinic_context"
    assert normalized["arguments"] == {}
    assert normalized["tool_call_id"].startswith("derived-")


def test_normalize_native_tool_payload_maps_args_to_arguments() -> None:
    normalized = normalize_retell_tool_payload(
        _native_tool_payload(
            name="check_availability",
            args={
                "doctor_id": "11111111-1111-1111-1111-111111111111",
                "date_expression": {"kind": "tomorrow"},
            },
        ),
    )

    assert normalized["tool_name"] == "check_availability"
    assert normalized["arguments"]["date_expression"] == {"kind": "tomorrow"}


def test_normalize_normalized_tool_payload_still_works() -> None:
    payload = _normalized_tool_payload()
    normalized = normalize_retell_tool_payload(payload)

    assert normalized == payload


def test_normalize_tool_payload_rejects_args_only_body() -> None:
    with pytest.raises(RetellPayloadNormalizationError, match='Payload: args only'):
        normalize_retell_tool_payload({})


def test_normalize_native_call_started_lifecycle_payload() -> None:
    normalized = normalize_retell_lifecycle_payload(
        _native_lifecycle_payload(event="call_started"),
        clock=FixedClock(current_time=REFERENCE_NOW),
    )

    assert normalized["event"] == "call_started"
    assert normalized["call_id"] == "call-native-life-1"
    assert normalized["occurred_at"] == "2026-06-23T12:00:00+00:00"


def test_normalize_native_call_ended_lifecycle_payload() -> None:
    normalized = normalize_retell_lifecycle_payload(
        _native_lifecycle_payload(event="call_ended"),
        clock=FixedClock(current_time=REFERENCE_NOW),
    )

    assert normalized["event"] == "call_ended"
    assert normalized["occurred_at"] == "2026-06-23T12:01:00+00:00"


def test_normalize_lifecycle_payload_uses_clock_fallback_when_timestamp_missing() -> None:
    normalized = normalize_retell_lifecycle_payload(
        {
            "event": "call_started",
            "call": {"call_id": "call-no-ts"},
        },
        clock=FixedClock(current_time=REFERENCE_NOW),
    )

    assert normalized["occurred_at"] == REFERENCE_NOW.isoformat()
    assert normalized["occurred_at_source"] == "backend_clock_fallback"


def test_normalize_normalized_lifecycle_payload_still_works() -> None:
    payload = {
        "call_id": "call-123",
        "event": "call_started",
        "occurred_at": OCCURRED_AT,
        "event_id": "evt-1",
    }

    assert normalize_retell_lifecycle_payload(payload) == payload


def test_native_get_clinic_context_reaches_adapter_via_http() -> None:
    adapter = _make_clinic_context_adapter()
    app = create_app()
    settings = make_secured_retell_settings()
    configure_retell_for_tests(app, settings=settings)
    app.dependency_overrides[get_retell_signature_verifier] = lambda: _hmac_verifier()
    app.dependency_overrides[get_retell_tool_calling_adapter] = lambda: adapter

    raw_body = _native_tool_payload(call_id="call-http-native")
    body_bytes = _json_bytes(raw_body)
    signature = sign_retell_body(
        raw_body=body_bytes,
        secret=WEBHOOK_SECRET,
        timestamp_ms=TIMESTAMP_MS,
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/retell/tools",
            content=body_bytes,
            headers={
                "Content-Type": "application/json",
                **retell_request_headers(signature=signature),
            },
        )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["result"]["current_date"] == "2026-06-23"
    assert WEBHOOK_SECRET not in response.text
    assert "RETELL_API_KEY" not in response.text


def test_normalized_tool_payload_still_works_via_http() -> None:
    adapter = _make_clinic_context_adapter()
    app = create_app()
    settings = make_secured_retell_settings()
    configure_retell_for_tests(app, settings=settings)
    app.dependency_overrides[get_retell_signature_verifier] = lambda: _hmac_verifier()
    app.dependency_overrides[get_retell_tool_calling_adapter] = lambda: adapter

    raw_body = _normalized_tool_payload(provider_call_id="call-http-normalized")
    body_bytes = _json_bytes(raw_body)
    signature = sign_retell_body(
        raw_body=body_bytes,
        secret=WEBHOOK_SECRET,
        timestamp_ms=TIMESTAMP_MS,
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/retell/tools",
            content=body_bytes,
            headers={
                "Content-Type": "application/json",
                **retell_request_headers(signature=signature),
            },
        )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["status"] == "succeeded"


def test_native_call_started_lifecycle_is_persisted_via_http() -> None:
    repository = FakeVoiceCallRepository()
    service = RetellCallLifecycleService(repository=repository)
    app = create_app()
    settings = make_secured_retell_settings()
    configure_retell_for_tests(app, settings=settings)
    app.dependency_overrides[get_retell_signature_verifier] = lambda: _hmac_verifier()
    app.dependency_overrides[get_retell_call_lifecycle_service] = lambda: service

    raw_body = _native_lifecycle_payload(event="call_started", call_id="call-life-native")
    body_bytes = _json_bytes(raw_body)
    signature = sign_retell_body(
        raw_body=body_bytes,
        secret=WEBHOOK_SECRET,
        timestamp_ms=TIMESTAMP_MS,
    )

    with TestClient(app) as client:
        response = client.post(
            LIFECYCLE_WEBHOOK_PATH,
            content=body_bytes,
            headers={
                "Content-Type": "application/json",
                **retell_request_headers(signature=signature),
            },
        )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert len(repository.voice_calls) == 1
    assert len(repository.voice_call_events) == 1
    assert WEBHOOK_SECRET not in response.text


def test_native_call_ended_lifecycle_is_persisted_via_http() -> None:
    repository = FakeVoiceCallRepository()
    service = RetellCallLifecycleService(repository=repository)
    app = create_app()
    settings = make_secured_retell_settings()
    configure_retell_for_tests(app, settings=settings)
    app.dependency_overrides[get_retell_signature_verifier] = lambda: _hmac_verifier()
    app.dependency_overrides[get_retell_call_lifecycle_service] = lambda: service

    started = _native_lifecycle_payload(event="call_started", call_id="call-life-end")
    ended = _native_lifecycle_payload(event="call_ended", call_id="call-life-end")

    with TestClient(app) as client:
        for raw_body in (started, ended):
            body_bytes = _json_bytes(raw_body)
            signature = sign_retell_body(
                raw_body=body_bytes,
                secret=WEBHOOK_SECRET,
                timestamp_ms=TIMESTAMP_MS,
            )
            response = client.post(
                LIFECYCLE_WEBHOOK_PATH,
                content=body_bytes,
                headers={
                    "Content-Type": "application/json",
                    **retell_request_headers(signature=signature),
                },
            )
            assert response.status_code == 200

    app.dependency_overrides.clear()

    assert len(repository.voice_call_events) == 2
    assert repository.voice_calls[0].status.value == "ended"


def test_normalized_lifecycle_payload_still_works_via_http() -> None:
    repository = FakeVoiceCallRepository()
    service = RetellCallLifecycleService(repository=repository)
    app = create_app()
    settings = make_secured_retell_settings()
    configure_retell_for_tests(app, settings=settings)
    app.dependency_overrides[get_retell_signature_verifier] = lambda: _hmac_verifier()
    app.dependency_overrides[get_retell_call_lifecycle_service] = lambda: service

    raw_body = {
        "call_id": "call-123",
        "event": "call_started",
        "occurred_at": OCCURRED_AT,
        "event_id": "evt-1",
    }

    with TestClient(app) as client:
        response = post_retell_tool(
            client,
            LIFECYCLE_WEBHOOK_PATH,
            json_body=raw_body,
            settings=settings,
        )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert len(repository.voice_call_events) == 1


def test_invalid_signature_rejects_before_normalization() -> None:
    tracking_adapter = NeverCalledRetellToolCallingAdapter()
    app = create_app()
    settings = make_secured_retell_settings()
    configure_retell_for_tests(app, settings=settings)
    app.dependency_overrides[get_retell_tool_calling_adapter] = lambda: tracking_adapter

    raw_body = _native_tool_payload()

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/retell/tools",
            json=raw_body,
            headers=retell_request_headers(signature="v=1,d=bad"),
        )

    app.dependency_overrides.clear()

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "retell_signature_invalid"
    assert tracking_adapter.execute_calls == []


def test_malformed_native_tool_payload_returns_safe_invalid_retell_payload() -> None:
    app = create_app()
    settings = make_secured_retell_settings()
    configure_retell_for_tests(app, settings=settings)
    app.dependency_overrides[get_retell_signature_verifier] = lambda: _hmac_verifier()

    raw_body = {"name": "get_clinic_context"}
    body_bytes = _json_bytes(raw_body)
    signature = sign_retell_body(
        raw_body=body_bytes,
        secret=WEBHOOK_SECRET,
        timestamp_ms=TIMESTAMP_MS,
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/retell/tools",
            content=body_bytes,
            headers={
                "Content-Type": "application/json",
                **retell_request_headers(signature=signature),
            },
        )

    app.dependency_overrides.clear()

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_retell_payload"
    assert WEBHOOK_SECRET not in response.text


def test_args_only_payload_is_rejected_via_http() -> None:
    app = create_app()
    settings = make_secured_retell_settings()
    configure_retell_for_tests(app, settings=settings)
    app.dependency_overrides[get_retell_signature_verifier] = lambda: _hmac_verifier()

    signature = sign_retell_body(
        raw_body=b"{}",
        secret=WEBHOOK_SECRET,
        timestamp_ms=TIMESTAMP_MS,
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/retell/tools",
            content=b"{}",
            headers={
                "Content-Type": "application/json",
                **retell_request_headers(signature=signature),
            },
        )

    app.dependency_overrides.clear()

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_retell_payload"
    assert "Payload: args only" in response.json()["error"]["message"]


def _retell_dashboard_lifecycle_payload(
    *,
    call_id: str = "test_call",
    event: str = "call_started",
    event_timestamp: int | None = START_TS_MS,
) -> dict[str, Any]:
    call: dict[str, Any] = {
        "call_id": call_id,
        "call_type": "web_call",
        "agent_id": "test_agent",
        "agent_version": 0,
        "agent_name": "Test Single Prompt Agent",
        "call_status": "ongoing",
        "start_timestamp": START_TS_MS,
        "transcript": "",
        "transcript_object": [],
        "transcript_with_tool_calls": [],
        "scrubbed_transcript_with_tool_calls": [],
        "latency": {},
        "call_cost": {
            "product_costs": [],
            "combined_cost": 0,
            "total_duration_seconds": 0,
            "total_duration_unit_price": 0,
        },
        "access_token": "secret-token-must-not-persist",
    }
    payload: dict[str, Any] = {
        "event": event,
        "call": call,
    }
    if event_timestamp is not None:
        payload["event_timestamp"] = event_timestamp
    return payload


def test_retell_dashboard_lifecycle_payload_coerces_agent_version_to_string() -> None:
    normalized = normalize_retell_lifecycle_payload(
        _retell_dashboard_lifecycle_payload(),
        clock=FixedClock(current_time=REFERENCE_NOW),
    )

    assert normalized["agent_version"] == "0"
    assert normalized["agent_id"] == "test_agent"
    assert normalized["call_status"] == "ongoing"
    assert normalized["call_type"] == "web_call"
    assert "access_token" not in normalized
    assert "access_token" not in json.dumps(normalized)


def test_retell_dashboard_lifecycle_payload_passes_pydantic_validation() -> None:
    normalized = normalize_retell_lifecycle_payload(_retell_dashboard_lifecycle_payload())

    request = RetellLifecycleWebhookRequest.model_validate(normalized)

    assert request.agent_version == "0"
    assert request.call_id == "test_call"


def test_retell_dashboard_lifecycle_payload_metadata_excludes_access_token() -> None:
    normalized = normalize_retell_lifecycle_payload(_retell_dashboard_lifecycle_payload())
    request = RetellLifecycleWebhookRequest.model_validate(normalized)
    lifecycle_payload = build_retell_lifecycle_payload(request)

    assert "access_token" not in lifecycle_payload.safe_metadata


def test_lifecycle_event_timestamp_epoch_ms_is_used_for_occurred_at() -> None:
    event_timestamp_ms = START_TS_MS
    normalized = normalize_retell_lifecycle_payload(
        {
            "event": "call_started",
            "event_timestamp": event_timestamp_ms,
            "call": {"call_id": "call-event-ts"},
        },
        clock=FixedClock(current_time=REFERENCE_NOW),
    )

    assert normalized["occurred_at"] == REFERENCE_NOW.isoformat()


def test_retell_dashboard_lifecycle_is_persisted_via_http() -> None:
    repository = FakeVoiceCallRepository()
    service = RetellCallLifecycleService(repository=repository)
    app = create_app()
    settings = make_secured_retell_settings()
    configure_retell_for_tests(app, settings=settings)
    app.dependency_overrides[get_retell_signature_verifier] = lambda: _hmac_verifier()
    app.dependency_overrides[get_retell_call_lifecycle_service] = lambda: service

    raw_body = _retell_dashboard_lifecycle_payload()
    body_bytes = _json_bytes(raw_body)
    signature = sign_retell_body(
        raw_body=body_bytes,
        secret=WEBHOOK_SECRET,
        timestamp_ms=TIMESTAMP_MS,
    )

    with TestClient(app) as client:
        response = client.post(
            LIFECYCLE_WEBHOOK_PATH,
            content=body_bytes,
            headers={
                "Content-Type": "application/json",
                **retell_request_headers(signature=signature),
            },
        )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert len(repository.voice_calls) == 1


def test_malformed_lifecycle_payload_returns_invalid_retell_payload_via_http() -> None:
    app = create_app()
    settings = make_secured_retell_settings()
    configure_retell_for_tests(app, settings=settings)
    app.dependency_overrides[get_retell_signature_verifier] = lambda: _hmac_verifier()

    raw_body = {"event": "call_started"}
    body_bytes = _json_bytes(raw_body)
    signature = sign_retell_body(
        raw_body=body_bytes,
        secret=WEBHOOK_SECRET,
        timestamp_ms=TIMESTAMP_MS,
    )

    with TestClient(app) as client:
        response = client.post(
            LIFECYCLE_WEBHOOK_PATH,
            content=body_bytes,
            headers={
                "Content-Type": "application/json",
                **retell_request_headers(signature=signature),
            },
        )

    app.dependency_overrides.clear()

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_retell_payload"


def test_native_tool_payload_preserves_explicit_tool_call_id() -> None:
    normalized = normalize_retell_tool_payload(
        _native_tool_payload(tool_call_id="tool-native-42"),
    )

    assert normalized["tool_call_id"] == "tool-native-42"


def test_normalized_tool_request_model_accepts_native_mapping_output() -> None:
    request = RetellToolCallRequest.model_validate(
        normalize_retell_tool_payload(_native_tool_payload()),
    )

    assert request.provider_call_id == "call-native-1"
    assert request.tool_name == "get_clinic_context"
