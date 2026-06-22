from __future__ import annotations

import hashlib
import hmac
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import get_retell_signature_verifier
from app.core.config import Settings, get_settings
from app.integrations.retell.signature import FakeRetellSignatureVerifier


def make_retell_enabled_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "RETELL_ENABLED": True,
        "RETELL_ALLOW_INSECURE_WEBHOOKS": True,
        "RETELL_WEBHOOK_SECRET": "test-webhook-secret",
        "APP_ENV": "local",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def make_secured_retell_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "RETELL_ENABLED": True,
        "RETELL_WEBHOOK_VERIFICATION_ENABLED": True,
        "RETELL_ALLOW_INSECURE_WEBHOOKS": False,
        "RETELL_WEBHOOK_SECRET": "test-webhook-secret",
        "APP_ENV": "local",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def configure_retell_for_tests(
    app: FastAPI,
    *,
    settings: Settings | None = None,
) -> Settings:
    resolved_settings = settings or make_retell_enabled_settings()

    def override_settings() -> Settings:
        return resolved_settings

    app.dependency_overrides[get_settings] = override_settings
    return resolved_settings


def sign_retell_body(*, raw_body: bytes, secret: str, timestamp_ms: int) -> str:
    body_text = raw_body.decode("utf-8")
    timestamp_str = str(timestamp_ms)
    digest = hmac.new(
        secret.encode("utf-8"),
        f"{body_text}{timestamp_str}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"v={timestamp_ms},d={digest}"


def retell_request_headers(
    *,
    signature: str,
    header_name: str = "x-retell-signature",
) -> dict[str, str]:
    return {header_name: signature}


def post_retell_tool(
    client: TestClient,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
    settings: Settings | None = None,
    content: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    payload = json_body or {}
    raw_body = content if content is not None else _encode_json(payload)
    request_headers = {"Content-Type": "application/json", **(headers or {})}

    if settings is not None and not settings.retell_allow_insecure_webhooks:
        signature = sign_retell_body(
            raw_body=raw_body,
            secret=(settings.retell_webhook_secret or ""),
            timestamp_ms=1_700_000_000_000,
        )
        request_headers.update(
            retell_request_headers(
                signature=signature,
                header_name=settings.retell_signature_header_name,
            ),
        )

    return client.post(path, content=raw_body, headers=request_headers)


def _encode_json(payload: dict[str, Any]) -> bytes:
    import json

    return json.dumps(payload, separators=(",", ":")).encode()


def install_fake_retell_verifier(
    app: FastAPI,
    *,
    accept_all: bool = False,
    valid_signatures: set[str] | None = None,
) -> FakeRetellSignatureVerifier:
    verifier = FakeRetellSignatureVerifier(
        accept_all=accept_all,
        valid_signatures=valid_signatures,
    )

    def override_verifier() -> FakeRetellSignatureVerifier:
        return verifier

    app.dependency_overrides[get_retell_signature_verifier] = override_verifier
    return verifier
