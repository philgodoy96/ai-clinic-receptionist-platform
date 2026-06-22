from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.adapters.retell.scheduling_tools import RetellSchedulingToolAdapter
from app.api.dependencies import get_retell_scheduling_tool_adapter, get_retell_signature_verifier
from app.integrations.retell.signature import (
    HmacRetellSignatureVerifier,
    RetellSignatureVerificationError,
)
from app.main import create_app
from tests.retell_webhook_support import (
    configure_retell_for_tests,
    install_fake_retell_verifier,
    make_retell_enabled_settings,
    make_secured_retell_settings,
    retell_request_headers,
    sign_retell_body,
)
from tests.test_api_errors import EmptySchedulingService

TIMESTAMP_MS = 1_700_000_000_000
WEBHOOK_SECRET = "test-webhook-secret"


def _configure_secured(app: FastAPI, *, verifier_accept_all: bool) -> None:
    configure_retell_for_tests(app, settings=make_secured_retell_settings())
    install_fake_retell_verifier(app, accept_all=verifier_accept_all)


def _configure_secured_with_hmac(app: FastAPI) -> None:
    configure_retell_for_tests(app, settings=make_secured_retell_settings())
    app.dependency_overrides[get_retell_signature_verifier] = lambda: HmacRetellSignatureVerifier(
        secret=WEBHOOK_SECRET,
        now_millis=lambda: TIMESTAMP_MS,
    )


def _configure_failing_verifier(app: FastAPI) -> None:
    configure_retell_for_tests(app, settings=make_secured_retell_settings())

    class FailingVerifier:
        def verify(self, *, raw_body: bytes, signature: str) -> None:
            raise RetellSignatureVerificationError("backend unavailable")

    app.dependency_overrides[get_retell_signature_verifier] = lambda: FailingVerifier()


def _configure_payload_limit(app: FastAPI) -> None:
    configure_retell_for_tests(
        app,
        settings=make_secured_retell_settings(RETELL_REQUEST_MAX_BODY_BYTES=8),
    )
    install_fake_retell_verifier(app, accept_all=True)


@pytest.mark.parametrize(
    ("status_code", "expected_code", "setup", "request_headers"),
    [
        (
            503,
            "retell_disabled",
            lambda app: configure_retell_for_tests(
                app,
                settings=make_retell_enabled_settings(RETELL_ENABLED=False),
            ),
            {"Content-Type": "application/json"},
        ),
        (
            401,
            "retell_signature_missing",
            lambda app: _configure_secured(app, verifier_accept_all=True),
            {"Content-Type": "application/json"},
        ),
        (
            401,
            "retell_signature_invalid",
            lambda app: _configure_secured(app, verifier_accept_all=False),
            {
                "Content-Type": "application/json",
                **retell_request_headers(signature="v=1,d=bad"),
            },
        ),
        (
            503,
            "retell_webhook_verification_unavailable",
            _configure_failing_verifier,
            {
                "Content-Type": "application/json",
                **retell_request_headers(signature="v=1,d=test"),
            },
        ),
        (
            413,
            "retell_payload_too_large",
            _configure_payload_limit,
            {
                "Content-Type": "application/json",
                "Content-Length": "20",
            },
        ),
    ],
)
def test_retell_security_errors_use_standardized_envelope(
    status_code: int,
    expected_code: str,
    setup: Callable[[FastAPI], None],
    request_headers: dict[str, str],
) -> None:
    app = create_app()
    setup(app)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/retell/tools/list-specialties",
            content=b'{"too":"large"}' if expected_code == "retell_payload_too_large" else b"{}",
            headers=request_headers,
        )

    app.dependency_overrides.clear()

    body = response.json()
    assert response.status_code == status_code
    assert body["error"]["code"] == expected_code
    assert body["error"]["message"]
    assert body["error"]["request_id"] is not None


def test_invalid_retell_payload_uses_standardized_envelope() -> None:
    app = create_app()
    _configure_secured_with_hmac(app)
    app.dependency_overrides[get_retell_scheduling_tool_adapter] = (
        lambda: RetellSchedulingToolAdapter(EmptySchedulingService())
    )

    raw_body = b"{not-json"
    signature = sign_retell_body(
        raw_body=raw_body,
        secret=WEBHOOK_SECRET,
        timestamp_ms=TIMESTAMP_MS,
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

    body = response.json()
    assert response.status_code == 400
    assert body["error"]["code"] == "invalid_retell_payload"
    assert body["error"]["request_id"] is not None
