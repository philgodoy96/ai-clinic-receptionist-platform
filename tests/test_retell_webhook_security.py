from __future__ import annotations

from collections.abc import Generator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.adapters.retell.scheduling_tools import RetellSchedulingToolAdapter
from app.api.dependencies import (
    get_retell_scheduling_tool_adapter,
    get_retell_signature_verifier,
    get_retell_tool_calling_adapter,
)
from app.domain.retell_tools import build_rejected_tool_call_response
from app.integrations.retell.signature import (
    FakeRetellSignatureVerifier,
    HmacRetellSignatureVerifier,
    RetellSignatureVerificationError,
)
from app.main import create_app
from app.schemas.retell_tools import RetellToolCallRequest
from tests.retell_webhook_support import (
    configure_retell_for_tests,
    install_fake_retell_verifier,
    make_retell_enabled_settings,
    make_secured_retell_settings,
    post_retell_tool,
    retell_request_headers,
    sign_retell_body,
)
from tests.test_api_errors import EmptySchedulingService

TIMESTAMP_MS = 1_700_000_000_000
WEBHOOK_SECRET = "test-webhook-secret"


@pytest.fixture()
def secured_retell_client() -> Generator[
    tuple[TestClient, FakeRetellSignatureVerifier],
    None,
    None,
]:
    app = create_app()
    settings = make_secured_retell_settings(RETELL_REQUEST_MAX_BODY_BYTES=1024)
    configure_retell_for_tests(app, settings=settings)
    verifier = install_fake_retell_verifier(app, accept_all=True)
    tracking_service = TrackingSchedulingService()

    def override_adapter() -> RetellSchedulingToolAdapter:
        return RetellSchedulingToolAdapter(tracking_service)

    app.dependency_overrides[get_retell_scheduling_tool_adapter] = override_adapter

    with TestClient(app) as client:
        yield client, verifier

    app.dependency_overrides.clear()


def test_disabled_retell_rejects_route() -> None:
    app = create_app()
    settings = make_retell_enabled_settings(RETELL_ENABLED=False)
    configure_retell_for_tests(app, settings=settings)

    with TestClient(app) as client:
        response = post_retell_tool(
            client,
            "/api/v1/retell/tools/list-specialties",
            settings=settings,
        )

    app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "retell_disabled"


def test_missing_signature_returns_401(secured_retell_client: tuple[TestClient, Any]) -> None:
    client, verifier = secured_retell_client

    response = client.post(
        "/api/v1/retell/tools/list-specialties",
        content=b"{}",
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "retell_signature_missing"
    assert verifier.last_raw_body is None


def test_invalid_signature_returns_401() -> None:
    app = create_app()
    settings = make_secured_retell_settings()
    configure_retell_for_tests(app, settings=settings)
    verifier = install_fake_retell_verifier(app)
    tracking_service = TrackingSchedulingService()

    def override_adapter() -> RetellSchedulingToolAdapter:
        return RetellSchedulingToolAdapter(tracking_service)

    app.dependency_overrides[get_retell_scheduling_tool_adapter] = override_adapter

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/retell/tools/list-specialties",
            content=b"{}",
            headers={
                "Content-Type": "application/json",
                **retell_request_headers(signature="v=1,d=invalid"),
            },
        )

    app.dependency_overrides.clear()

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "retell_signature_invalid"
    assert verifier.last_raw_body == b"{}"
    assert tracking_service.list_specialties_calls == 0


def test_valid_signature_allows_route_to_continue() -> None:
    app = create_app()
    timestamp_ms = 1_700_000_000_000
    settings = make_secured_retell_settings()
    configure_retell_for_tests(app, settings=settings)
    app.dependency_overrides[get_retell_scheduling_tool_adapter] = lambda: (
        RetellSchedulingToolAdapter(EmptySchedulingService())
    )
    app.dependency_overrides[get_retell_signature_verifier] = lambda: HmacRetellSignatureVerifier(
        secret="test-webhook-secret",
        now_millis=lambda: timestamp_ms,
    )

    raw_body = b"{}"
    signature = sign_retell_body(
        raw_body=raw_body,
        secret="test-webhook-secret",
        timestamp_ms=timestamp_ms,
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/retell/tools/list-specialties",
            content=raw_body,
            headers={
                "Content-Type": "application/json",
                **retell_request_headers(signature=signature),
            },
        )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_malformed_json_after_valid_signature_returns_400() -> None:
    app = create_app()
    timestamp_ms = 1_700_000_000_000
    settings = make_secured_retell_settings()
    configure_retell_for_tests(app, settings=settings)
    app.dependency_overrides[get_retell_scheduling_tool_adapter] = lambda: (
        RetellSchedulingToolAdapter(EmptySchedulingService())
    )
    app.dependency_overrides[get_retell_signature_verifier] = lambda: HmacRetellSignatureVerifier(
        secret="test-webhook-secret",
        now_millis=lambda: timestamp_ms,
    )

    raw_body = b"{not-json"
    signature = sign_retell_body(
        raw_body=raw_body,
        secret="test-webhook-secret",
        timestamp_ms=timestamp_ms,
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/retell/tools/list-specialties",
            content=raw_body,
            headers={
                "Content-Type": "application/json",
                **retell_request_headers(signature=signature),
            },
        )

    app.dependency_overrides.clear()

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_retell_payload"


def test_payload_too_large_returns_413() -> None:
    app = create_app()
    settings = make_secured_retell_settings(RETELL_REQUEST_MAX_BODY_BYTES=8)
    configure_retell_for_tests(app, settings=settings)
    install_fake_retell_verifier(app, accept_all=True)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/retell/tools/list-specialties",
            content=b'{"too":"large"}',
            headers={
                "Content-Type": "application/json",
                "Content-Length": "20",
            },
        )

    app.dependency_overrides.clear()

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "retell_payload_too_large"


def test_verifier_receives_raw_body_bytes_unchanged() -> None:
    app = create_app()
    settings = make_secured_retell_settings()
    configure_retell_for_tests(app, settings=settings)
    verifier = install_fake_retell_verifier(app, valid_signatures={"v=1,d=test"})
    tracking_service = TrackingSchedulingService()

    def override_adapter() -> RetellSchedulingToolAdapter:
        return RetellSchedulingToolAdapter(tracking_service)

    app.dependency_overrides[get_retell_scheduling_tool_adapter] = override_adapter

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/retell/tools/list-specialties",
            content=b'{"spacing": "preserved"}',
            headers={
                "Content-Type": "application/json",
                **retell_request_headers(signature="v=1,d=test"),
            },
        )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert verifier.last_raw_body == b'{"spacing": "preserved"}'


def test_domain_service_not_called_when_verification_fails() -> None:
    app = create_app()
    settings = make_secured_retell_settings()
    configure_retell_for_tests(app, settings=settings)
    install_fake_retell_verifier(app)
    tracking_service = TrackingSchedulingService()

    def override_adapter() -> RetellSchedulingToolAdapter:
        return RetellSchedulingToolAdapter(tracking_service)

    app.dependency_overrides[get_retell_scheduling_tool_adapter] = override_adapter

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/retell/tools/list-specialties",
            content=b"{}",
            headers={
                "Content-Type": "application/json",
                **retell_request_headers(signature="v=1,d=bad"),
            },
        )

    app.dependency_overrides.clear()

    assert response.status_code == 401
    assert tracking_service.list_specialties_calls == 0


def test_verifier_exceptions_do_not_expose_secret() -> None:
    app = create_app()
    secret = "super-secret-webhook-key"
    settings = make_secured_retell_settings(RETELL_WEBHOOK_SECRET=secret)
    configure_retell_for_tests(app, settings=settings)
    app.dependency_overrides[get_retell_signature_verifier] = lambda: HmacRetellSignatureVerifier(
        secret=secret,
        now_millis=lambda: 1_700_000_000_000,
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/retell/tools/list-specialties",
            content=b"{}",
            headers={
                "Content-Type": "application/json",
                **retell_request_headers(signature="v=1700000000000,d=deadbeef"),
            },
        )

    app.dependency_overrides.clear()

    assert response.status_code == 401
    body = response.json()
    assert secret not in str(body)
    assert body["error"]["code"] == "retell_signature_invalid"


def test_unsupported_content_type_is_rejected_safely() -> None:
    app = create_app()
    settings = make_secured_retell_settings()
    configure_retell_for_tests(app, settings=settings)
    install_fake_retell_verifier(app, accept_all=True)
    tracking_service = TrackingSchedulingService()
    app.dependency_overrides[get_retell_scheduling_tool_adapter] = lambda: (
        RetellSchedulingToolAdapter(tracking_service)
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/retell/tools/list-specialties",
            content=b"{}",
            headers={
                "Content-Type": "text/plain",
                **retell_request_headers(signature="v=1,d=test"),
            },
        )

    app.dependency_overrides.clear()

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_retell_payload"
    assert tracking_service.list_specialties_calls == 0


def test_unknown_retell_tool_route_returns_404_without_side_effects() -> None:
    app = create_app()
    settings = make_secured_retell_settings()
    configure_retell_for_tests(app, settings=settings)
    install_fake_retell_verifier(app, accept_all=True)

    with TestClient(app) as client:
        response = post_retell_tool(
            client,
            "/api/v1/retell/tools/unsupported-tool",
            settings=settings,
        )

    app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "http_404"


def test_invalid_payload_schema_after_valid_signature_returns_400() -> None:
    app = create_app()
    settings = make_secured_retell_settings()
    configure_retell_for_tests(app, settings=settings)
    app.dependency_overrides[get_retell_scheduling_tool_adapter] = lambda: (
        RetellSchedulingToolAdapter(EmptySchedulingService())
    )
    app.dependency_overrides[get_retell_signature_verifier] = lambda: HmacRetellSignatureVerifier(
        secret=WEBHOOK_SECRET,
        now_millis=lambda: TIMESTAMP_MS,
    )

    raw_body = b'{"start_from":"not-a-datetime","start_to":"also-invalid"}'
    signature = sign_retell_body(
        raw_body=raw_body,
        secret=WEBHOOK_SECRET,
        timestamp_ms=TIMESTAMP_MS,
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/retell/tools/check-availability",
            content=raw_body,
            headers={
                "Content-Type": "application/json",
                **retell_request_headers(signature=signature),
            },
        )

    app.dependency_overrides.clear()

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_retell_payload"


def test_verifier_exception_returns_standardized_503() -> None:
    app = create_app()
    settings = make_secured_retell_settings()
    configure_retell_for_tests(app, settings=settings)

    class FailingVerifier:
        def verify(self, *, raw_body: bytes, signature: str) -> None:
            raise RetellSignatureVerificationError("verification backend unavailable")

    app.dependency_overrides[get_retell_signature_verifier] = lambda: FailingVerifier()

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/retell/tools/list-specialties",
            content=b"{}",
            headers={
                "Content-Type": "application/json",
                **retell_request_headers(signature="v=1,d=test"),
            },
        )

    app.dependency_overrides.clear()

    body = response.json()
    assert response.status_code == 503
    assert body["error"]["code"] == "retell_webhook_verification_unavailable"
    assert WEBHOOK_SECRET not in str(body)
    assert "verification backend unavailable" not in str(body)


def test_disabled_retell_rejects_unified_tool_route() -> None:
    app = create_app()
    settings = make_retell_enabled_settings(RETELL_ENABLED=False)
    configure_retell_for_tests(app, settings=settings)
    tracking_adapter = TrackingRetellToolCallingAdapter()

    app.dependency_overrides[get_retell_tool_calling_adapter] = lambda: tracking_adapter

    with TestClient(app) as client:
        response = post_retell_tool(
            client,
            "/api/v1/retell/tools",
            settings=settings,
            json_body={
                "provider_call_id": "retell-call-123",
                "tool_name": "check_availability",
                "arguments": {
                    "start_from": "2026-07-01T09:00:00Z",
                    "start_to": "2026-07-01T12:00:00Z",
                },
            },
        )

    app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "retell_disabled"
    assert tracking_adapter.execute_calls == 0


def test_malformed_json_on_unified_tool_route_returns_400() -> None:
    app = create_app()
    settings = make_secured_retell_settings()
    configure_retell_for_tests(app, settings=settings)
    tracking_adapter = TrackingRetellToolCallingAdapter()
    app.dependency_overrides[get_retell_tool_calling_adapter] = lambda: tracking_adapter
    app.dependency_overrides[get_retell_signature_verifier] = lambda: HmacRetellSignatureVerifier(
        secret=WEBHOOK_SECRET,
        now_millis=lambda: TIMESTAMP_MS,
    )

    raw_body = b"{not-json"
    signature = sign_retell_body(
        raw_body=raw_body,
        secret=WEBHOOK_SECRET,
        timestamp_ms=TIMESTAMP_MS,
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/retell/tools",
            content=raw_body,
            headers={
                "Content-Type": "application/json",
                **retell_request_headers(signature=signature),
            },
        )

    app.dependency_overrides.clear()

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_retell_payload"
    assert tracking_adapter.execute_calls == 0


def test_valid_signature_reaches_unified_tool_adapter() -> None:
    app = create_app()
    settings = make_secured_retell_settings()
    configure_retell_for_tests(app, settings=settings)
    tracking_adapter = TrackingRetellToolCallingAdapter()
    app.dependency_overrides[get_retell_tool_calling_adapter] = lambda: tracking_adapter
    app.dependency_overrides[get_retell_signature_verifier] = lambda: HmacRetellSignatureVerifier(
        secret=WEBHOOK_SECRET,
        now_millis=lambda: TIMESTAMP_MS,
    )

    raw_body = (
        b'{"provider_call_id":"retell-call-123","tool_name":"check_availability",'
        b'"arguments":{"start_from":"2026-07-01T09:00:00Z","start_to":"2026-07-01T12:00:00Z"}}'
    )
    signature = sign_retell_body(
        raw_body=raw_body,
        secret=WEBHOOK_SECRET,
        timestamp_ms=TIMESTAMP_MS,
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/retell/tools",
            content=raw_body,
            headers={
                "Content-Type": "application/json",
                **retell_request_headers(signature=signature),
            },
        )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    assert tracking_adapter.execute_calls == 1


def test_adapter_not_called_when_verification_fails_on_unified_route() -> None:
    app = create_app()
    settings = make_secured_retell_settings()
    configure_retell_for_tests(app, settings=settings)
    install_fake_retell_verifier(app)
    tracking_adapter = TrackingRetellToolCallingAdapter()
    app.dependency_overrides[get_retell_tool_calling_adapter] = lambda: tracking_adapter

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/retell/tools",
            content=b"{}",
            headers={
                "Content-Type": "application/json",
                **retell_request_headers(signature="v=1,d=bad"),
            },
        )

    app.dependency_overrides.clear()

    assert response.status_code == 401
    assert tracking_adapter.execute_calls == 0


def test_non_retell_route_works_without_retell_signature() -> None:
    app = create_app()
    configure_retell_for_tests(app, settings=make_secured_retell_settings())

    with TestClient(app) as client:
        response = client.get("/health")

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


class TrackingSchedulingService(EmptySchedulingService):
    def __init__(self) -> None:
        super().__init__()
        self.list_specialties_calls = 0

    def list_specialties(self) -> list[Any]:
        self.list_specialties_calls += 1
        return list(super().list_specialties())


class TrackingRetellToolCallingAdapter:
    def __init__(self) -> None:
        self.execute_calls = 0

    def execute(self, request: RetellToolCallRequest) -> Any:
        self.execute_calls += 1
        return build_rejected_tool_call_response(
            tool_name=request.tool_name,
            tool_call_id=request.tool_call_id,
            error_code="unsupported_retell_tool",
        )
