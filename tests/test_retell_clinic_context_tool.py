from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_retell_signature_verifier, get_retell_tool_calling_adapter
from app.core.config import Settings
from app.integrations.retell.signature import HmacRetellSignatureVerifier
from app.main import create_app
from app.schemas.retell_tools import RetellToolCallRequest
from app.services.appointment_holds import AppointmentHoldService
from app.services.clock import FixedClock
from app.services.retell_tool_adapter import RetellToolCallingAdapter
from tests.clinic_time_test_support import make_test_clinic_time_service
from tests.retell_webhook_support import (
    configure_retell_for_tests,
    make_retell_enabled_settings,
    make_secured_retell_settings,
    post_retell_tool,
    retell_request_headers,
    sign_retell_body,
)
from tests.test_retell_tool_adapter import (
    TrackingAppointmentHoldRepository,
    TrackingSchedulingService,
    TrackingVoiceCallRepository,
)

REFERENCE_NOW_UTC = datetime(2026, 7, 1, 14, 0, tzinfo=UTC)
WEBHOOK_SECRET = "test-webhook-secret"
TIMESTAMP_MS = 1_700_000_000_000


def make_clinic_context_adapter(
    *,
    clock: FixedClock | None = None,
) -> RetellToolCallingAdapter:
    hold_repository = TrackingAppointmentHoldRepository()
    return RetellToolCallingAdapter(
        scheduling_service=TrackingSchedulingService(
            specialties=[],
            doctors=[],
            availability_slots=[],
        ),
        hold_service=AppointmentHoldService(repository=hold_repository, ttl_seconds=300),
        voice_calls=TrackingVoiceCallRepository(),
        clinic_time_service=make_test_clinic_time_service(clock=clock),
    )


@pytest.fixture()
def clinic_context_adapter() -> RetellToolCallingAdapter:
    return make_clinic_context_adapter()


def test_get_clinic_context_returns_fixed_date_with_fixed_clock(
    clinic_context_adapter: RetellToolCallingAdapter,
) -> None:
    response = clinic_context_adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": "retell-call-123",
                "tool_name": "get_clinic_context",
                "arguments": {},
            },
        ),
    )

    assert response.status == "succeeded"
    assert response.result == {
        "clinic_name": "Demo Clinic",
        "clinic_timezone": "America/New_York",
        "current_date": "2026-07-01",
        "current_weekday": "wednesday",
        "business_days": [
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
        ],
        "business_hours": {
            "start": "09:00",
            "end": "17:00",
        },
    }


def test_get_clinic_context_response_uses_clinic_timezone() -> None:
    adapter = make_clinic_context_adapter(
        clock=FixedClock(current_time=datetime(2026, 7, 2, 3, 0, tzinfo=UTC)),
    )

    response = adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": "retell-call-456",
                "tool_name": "get_clinic_context",
                "arguments": {},
            },
        ),
    )

    assert response.status == "succeeded"
    assert response.result["current_date"] == "2026-07-01"
    assert response.result["current_weekday"] == "wednesday"
    assert response.result["clinic_timezone"] == "America/New_York"


def test_get_clinic_context_response_contains_no_secrets(
    clinic_context_adapter: RetellToolCallingAdapter,
) -> None:
    response = clinic_context_adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": "retell-call-789",
                "tool_name": "get_clinic_context",
                "arguments": {},
            },
        ),
    )

    serialized = str(response.model_dump(mode="json"))
    assert response.status == "succeeded"
    assert "test-webhook-secret" not in serialized
    assert "DATABASE_URL" not in serialized
    assert "RETELL_API_KEY" not in serialized
    assert "_business_days" not in serialized
    assert "ZoneInfo" not in serialized


def test_get_clinic_context_has_no_business_side_effects(
    clinic_context_adapter: RetellToolCallingAdapter,
) -> None:
    scheduling_service = clinic_context_adapter.scheduling_service
    hold_service = clinic_context_adapter.hold_service
    voice_calls = clinic_context_adapter.voice_calls
    assert isinstance(scheduling_service, TrackingSchedulingService)
    assert isinstance(hold_service, AppointmentHoldService)
    assert isinstance(hold_service.repository, TrackingAppointmentHoldRepository)
    assert isinstance(voice_calls, TrackingVoiceCallRepository)

    response = clinic_context_adapter.execute(
        RetellToolCallRequest.model_validate(
            {
                "provider_call_id": "retell-call-side-effect",
                "tool_name": "get_clinic_context",
                "arguments": {},
            },
        ),
    )

    assert response.status == "succeeded"
    assert scheduling_service.check_availability_calls == []
    assert hold_service.repository.create_calls == []
    assert voice_calls.outcomes == {}


class _SideEffectTrackingAdapter:
    def __init__(self, adapter: RetellToolCallingAdapter) -> None:
        self.adapter = adapter
        self.execute_calls = 0

    def execute(self, request: RetellToolCallRequest) -> Any:
        self.execute_calls += 1
        return self.adapter.execute(request)


@pytest.fixture()
def secured_clinic_context_client() -> Generator[
    tuple[TestClient, Settings, _SideEffectTrackingAdapter],
    None,
    None,
]:
    app = create_app()
    settings = make_secured_retell_settings(RETELL_WEBHOOK_SECRET=WEBHOOK_SECRET)
    configure_retell_for_tests(app, settings=settings)
    tracking_adapter = _SideEffectTrackingAdapter(make_clinic_context_adapter())
    app.dependency_overrides[get_retell_tool_calling_adapter] = lambda: tracking_adapter
    app.dependency_overrides[get_retell_signature_verifier] = lambda: HmacRetellSignatureVerifier(
        secret=WEBHOOK_SECRET,
        now_millis=lambda: TIMESTAMP_MS,
    )

    with TestClient(app) as client:
        yield client, settings, tracking_adapter

    app.dependency_overrides.clear()


def _get_clinic_context_body() -> bytes:
    return (
        b'{"provider_call_id":"retell-call-http","tool_name":"get_clinic_context","arguments":{}}'
    )


def test_valid_verified_get_clinic_context_reaches_adapter_via_http(
    secured_clinic_context_client: tuple[
        TestClient,
        Settings,
        _SideEffectTrackingAdapter,
    ],
) -> None:
    client, _settings, tracking_adapter = secured_clinic_context_client
    raw_body = _get_clinic_context_body()
    signature = sign_retell_body(
        raw_body=raw_body,
        secret=WEBHOOK_SECRET,
        timestamp_ms=TIMESTAMP_MS,
    )

    response = client.post(
        "/api/v1/retell/tools",
        content=raw_body,
        headers={
            "Content-Type": "application/json",
            **retell_request_headers(signature=signature),
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["result"]["current_date"] == "2026-07-01"
    assert tracking_adapter.execute_calls == 1


def test_invalid_signature_prevents_get_clinic_context_execution(
    secured_clinic_context_client: tuple[
        TestClient,
        Settings,
        _SideEffectTrackingAdapter,
    ],
) -> None:
    client, _settings, tracking_adapter = secured_clinic_context_client

    response = client.post(
        "/api/v1/retell/tools",
        content=_get_clinic_context_body(),
        headers={
            "Content-Type": "application/json",
            **retell_request_headers(signature="v=1,d=invalid"),
        },
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "retell_signature_invalid"
    assert tracking_adapter.execute_calls == 0


def test_retell_disabled_rejects_get_clinic_context_route() -> None:
    app = create_app()
    settings = make_retell_enabled_settings(RETELL_ENABLED=False)
    configure_retell_for_tests(app, settings=settings)
    tracking_adapter = _SideEffectTrackingAdapter(make_clinic_context_adapter())
    app.dependency_overrides[get_retell_tool_calling_adapter] = lambda: tracking_adapter

    with TestClient(app) as client:
        response = post_retell_tool(
            client,
            "/api/v1/retell/tools",
            settings=settings,
            json_body={
                "provider_call_id": "retell-call-disabled",
                "tool_name": "get_clinic_context",
                "arguments": {},
            },
        )

    app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "retell_disabled"
    assert tracking_adapter.execute_calls == 0
