"""Regression suite for Retell web-call activation safety boundaries."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.dependencies import (
    get_retell_call_lifecycle_service,
    get_retell_tool_calling_adapter,
)
from app.core.config import get_settings
from app.domain.retell_web_call import RetellWebCallRequest
from app.integrations.retell.web_call_client import RetellWebCallTimeoutError
from app.main import create_app
from app.services.clinic_time import ClinicTimeService
from app.services.retell_web_call import (
    RetellWebCallConfigurationError,
    RetellWebCallDisabledError,
    RetellWebCallService,
)
from tests.demo_guardrail_support import FakeRedisClient
from tests.retell_cancellation_test_support import (
    create_retell_cancellation_tool_context,
)
from tests.retell_rescheduling_test_support import (
    create_retell_rescheduling_tool_context,
    reschedule_tool_request,
)
from tests.retell_webhook_support import (
    NeverCalledRetellToolCallingAdapter,
    configure_retell_for_tests,
    install_fake_retell_verifier,
    make_secured_retell_settings,
    post_retell_tool,
    retell_request_headers,
)
from tests.test_demo_voice_endpoint import (
    VOICE_ENDPOINT,
    create_demo_voice_app,
    make_voice_settings,
)
from tests.test_retell_call_lifecycle_service import FakeVoiceCallRepository
from tests.test_retell_lifecycle_webhook import (
    LIFECYCLE_WEBHOOK_PATH,
    TrackingRetellCallLifecycleService,
    _lifecycle_payload,
)
from tests.test_retell_voice_booking_tool import (
    _hold_id,
    create_retell_booking_tool_context,
)
from tests.test_retell_voice_booking_tool import (
    _tool_request as booking_tool_request,
)
from tests.test_retell_voice_cancellation_tool import _tool_request as cancellation_tool_request
from tests.test_retell_web_call_service import (
    TEST_ACCESS_TOKEN,
    TEST_AGENT_ID,
    TEST_API_KEY,
    TEST_CALL_ID,
    StubRetellWebCallClient,
    TrackingStubRetellWebCallClient,
    build_service,
    load_settings,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = PROJECT_ROOT / "web"

SAFE_WEB_CALL_RESPONSE_KEYS = frozenset(
    {
        "provider",
        "call_id",
        "access_token",
        "expires_in_seconds",
        "conversation_id",
    },
)


@pytest.fixture(autouse=True)
def clear_settings_cache() -> Any:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_retell_web_call_disabled_rejects_creation() -> None:
    app, stub, _, _ = create_demo_voice_app(
        make_voice_settings(RETELL_WEB_CALL_ENABLED=False),
    )

    with TestClient(app) as client:
        response = client.post(VOICE_ENDPOINT, json={})

    app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "voice_demo_disabled"
    assert stub.last_payload is None

    service = build_service(enabled=False)
    with pytest.raises(RetellWebCallDisabledError):
        service.create_web_call(RetellWebCallRequest())


def test_missing_retell_api_key_rejected_when_web_call_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValidationError, match="RETELL_API_KEY"):
        load_settings(
            monkeypatch,
            RETELL_WEB_CALL_ENABLED="true",
            RETELL_AGENT_ID=TEST_AGENT_ID,
            RETELL_API_KEY="",
        )

    service = build_service(api_key="")
    with pytest.raises(RetellWebCallConfigurationError, match="RETELL_API_KEY"):
        service.create_web_call(RetellWebCallRequest())


def test_missing_retell_agent_id_rejected_when_web_call_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValidationError, match="RETELL_AGENT_ID"):
        load_settings(
            monkeypatch,
            RETELL_WEB_CALL_ENABLED="true",
            RETELL_API_KEY=TEST_API_KEY,
            RETELL_AGENT_ID="",
        )

    service = build_service(agent_id="")
    with pytest.raises(RetellWebCallConfigurationError, match="RETELL_AGENT_ID"):
        service.create_web_call(RetellWebCallRequest())


def test_public_demo_rate_limit_blocks_before_retell_api_call() -> None:
    settings = make_voice_settings(
        PUBLIC_DEMO_GUARDRAILS_ENABLED=True,
        DEMO_RETELL_TOOL_CALLS_PER_MINUTE_PER_IP=1,
    )
    app, stub, _, _ = create_demo_voice_app(settings, redis_client=FakeRedisClient())

    with TestClient(app) as client:
        first = client.post(VOICE_ENDPOINT, json={})
        second = client.post(VOICE_ENDPOINT, json={})

    app.dependency_overrides.clear()

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "rate_limited"
    assert isinstance(stub, TrackingStubRetellWebCallClient)
    assert stub.call_count == 1


def test_valid_web_call_returns_safe_token_fields_only() -> None:
    app, _, _, _ = create_demo_voice_app(make_voice_settings())

    with TestClient(app) as client:
        response = client.post(VOICE_ENDPOINT, json={})

    app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == SAFE_WEB_CALL_RESPONSE_KEYS
    assert body["call_id"] == TEST_CALL_ID
    assert body["access_token"] == TEST_ACCESS_TOKEN
    assert body["provider"] == "retell"


def test_web_call_response_does_not_contain_retell_api_key() -> None:
    app, _, _, _ = create_demo_voice_app(make_voice_settings())

    with TestClient(app) as client:
        response = client.post(VOICE_ENDPOINT, json={})

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert TEST_API_KEY not in response.text
    assert "RETELL_API_KEY" not in response.text


def test_provider_timeout_returns_safe_error_without_secrets() -> None:
    stub = StubRetellWebCallClient(error=RetellWebCallTimeoutError("timed out"))
    app, _, db, _ = create_demo_voice_app(make_voice_settings(), retell_client=stub)

    with TestClient(app) as client:
        response = client.post(VOICE_ENDPOINT, json={})

    app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "provider_unavailable"
    assert db.rolled_back is True
    assert TEST_API_KEY not in response.text


def test_invalid_webhook_signature_prevents_lifecycle_mutation() -> None:
    repository = FakeVoiceCallRepository()
    tracking_service = TrackingRetellCallLifecycleService(repository=repository)
    app = create_app()
    settings = make_secured_retell_settings()
    configure_retell_for_tests(app, settings=settings)
    install_fake_retell_verifier(app)
    app.dependency_overrides[get_retell_call_lifecycle_service] = lambda: tracking_service

    with TestClient(app) as client:
        response = post_retell_tool(
            client,
            LIFECYCLE_WEBHOOK_PATH,
            json_body=_lifecycle_payload(),
            settings=settings,
            headers=retell_request_headers(signature="v=1,d=bad"),
        )

    app.dependency_overrides.clear()

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "retell_signature_invalid"
    assert tracking_service.ingest_calls == 0
    assert len(repository.voice_call_events) == 0


def test_invalid_webhook_signature_prevents_tool_mutation() -> None:
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
                b'{"provider_call_id":"retell-call-123","tool_name":"book_appointment",'
                b'"tool_call_id":"tool-call-1","arguments":{"explicit_confirmation":true}}'
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


def test_booking_tool_callback_idempotency_still_works() -> None:
    context = create_retell_booking_tool_context()
    booking_context = context["booking_context"]
    hold_id = _hold_id(booking_context)
    request = booking_tool_request(hold_id=hold_id, slot_id=str(booking_context.slot.id))

    first = context["adapter"].execute(request)
    second = context["adapter"].execute(request)

    assert first.status == "succeeded"
    assert second.status == "succeeded"
    assert second.duplicate is True
    assert len(context["tracking_booking"].book_calls) == 1


def test_cancel_tool_callback_idempotency_still_works() -> None:
    context = create_retell_cancellation_tool_context()
    appointment = context["appointment"]
    request = cancellation_tool_request(
        appointment_id=str(appointment.id),
        patient_resolution_id=context["patient_resolution_id"],
    )

    first = context["adapter"].execute(request)
    second = context["adapter"].execute(request)

    assert first.status == "succeeded"
    assert second.status == "succeeded"
    assert second.duplicate is True
    assert len(context["tracking_cancellation"].cancel_calls) == 1


def test_reschedule_tool_callback_idempotency_still_works() -> None:
    context = create_retell_rescheduling_tool_context()
    original_appointment = context["original_appointment"]
    hold = context["hold"]
    request = reschedule_tool_request(
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


def test_retell_web_call_service_without_client_never_hits_retell_urlopen() -> None:
    service = RetellWebCallService(
        enabled=True,
        api_key=TEST_API_KEY,
        agent_id=TEST_AGENT_ID,
        agent_version=None,
        timeout_seconds=10,
        clinic_time_service=ClinicTimeService(
            clinic_name="Demo Clinic",
            timezone="America/New_York",
            business_days="monday,tuesday,wednesday,thursday,friday",
            business_hours_start="09:00",
            business_hours_end="17:00",
        ),
        client=StubRetellWebCallClient(
            response={"call_id": TEST_CALL_ID, "access_token": TEST_ACCESS_TOKEN},
        ),
    )

    with patch(
        "app.integrations.retell.web_call_client.urlopen",
        side_effect=AssertionError("live Retell HTTP must not be used when client is injected"),
    ):
        result = service.create_web_call(RetellWebCallRequest())

    assert result.call_id == TEST_CALL_ID


def test_health_endpoint_does_not_call_retell() -> None:
    with (
        patch(
            "app.integrations.retell.web_call_client.urlopen",
            side_effect=AssertionError("health must not call Retell"),
        ),
        patch(
            "app.services.retell_web_call.UrllibRetellWebCallClient",
            side_effect=AssertionError("health must not construct Retell HTTP client"),
        ),
    ):
        client = TestClient(create_app())
        health_response = client.get("/health")
        readiness_response = client.get("/health/dependencies")

    assert health_response.status_code == 200
    assert health_response.json()["status"] == "ok"
    assert readiness_response.status_code == 200


@pytest.mark.skipif(shutil.which("npm") is None, reason="npm is not available")
def test_frontend_build_passes_with_voice_disabled_env() -> None:
    if not (WEB_ROOT / "package.json").exists():
        pytest.skip("web package is not present")

    env = os.environ.copy()
    env["NEXT_PUBLIC_VOICE_DEMO_ENABLED"] = "false"

    result = subprocess.run(
        ["npm", "run", "build"],
        cwd=WEB_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
        shell=sys.platform == "win32",
        check=False,
    )

    assert result.returncode == 0, (
        "frontend build failed with voice disabled\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
