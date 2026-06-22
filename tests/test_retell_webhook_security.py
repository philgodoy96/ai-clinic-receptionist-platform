from __future__ import annotations

from collections.abc import Generator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.adapters.retell.scheduling_tools import RetellSchedulingToolAdapter
from app.api.dependencies import (
    get_retell_scheduling_tool_adapter,
    get_retell_signature_verifier,
)
from app.integrations.retell.signature import (
    FakeRetellSignatureVerifier,
    HmacRetellSignatureVerifier,
)
from app.main import create_app
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
    app.dependency_overrides[get_retell_scheduling_tool_adapter] = (
        lambda: RetellSchedulingToolAdapter(EmptySchedulingService())
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
    app.dependency_overrides[get_retell_scheduling_tool_adapter] = (
        lambda: RetellSchedulingToolAdapter(EmptySchedulingService())
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


class TrackingSchedulingService(EmptySchedulingService):
    def __init__(self) -> None:
        super().__init__()
        self.list_specialties_calls = 0

    def list_specialties(self) -> list[Any]:
        self.list_specialties_calls += 1
        return list(super().list_specialties())
