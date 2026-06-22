from __future__ import annotations

import hashlib
import hmac

import pytest

from app.core.config import Settings
from app.integrations.retell.signature import (
    FakeRetellSignatureVerifier,
    HmacRetellSignatureVerifier,
    NoOpRetellSignatureVerifier,
    RetellInvalidSignatureError,
    RetellMissingSignatureError,
    RetellSignatureVerificationError,
    create_retell_signature_verifier,
)


def _sign_body(*, raw_body: bytes, secret: str, timestamp_ms: int) -> str:
    body_text = raw_body.decode("utf-8")
    timestamp_str = str(timestamp_ms)
    digest = hmac.new(
        secret.encode("utf-8"),
        f"{body_text}{timestamp_str}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"v={timestamp_ms},d={digest}"


def test_fake_verifier_accepts_valid_signature() -> None:
    raw_body = b'{"event":"call_started"}'
    signature = "v=123,d=abc"
    verifier = FakeRetellSignatureVerifier(valid_signatures={signature})

    verifier.verify(raw_body=raw_body, signature=signature)

    assert verifier.last_raw_body == raw_body
    assert verifier.last_signature == signature


def test_fake_verifier_rejects_invalid_signature() -> None:
    verifier = FakeRetellSignatureVerifier(valid_signatures={"v=123,d=abc"})

    with pytest.raises(RetellInvalidSignatureError):
        verifier.verify(raw_body=b"{}", signature="v=123,d=wrong")


def test_fake_verifier_rejects_missing_signature() -> None:
    verifier = FakeRetellSignatureVerifier()

    with pytest.raises(RetellMissingSignatureError):
        verifier.verify(raw_body=b"{}", signature="")


def test_verifier_exceptions_do_not_expose_secret() -> None:
    secret = "super-secret-webhook-key"
    verifier = HmacRetellSignatureVerifier(secret=secret, now_millis=lambda: 1_700_000_000_000)

    with pytest.raises(RetellInvalidSignatureError) as exc_info:
        verifier.verify(raw_body=b"{}", signature="v=1700000000000,d=deadbeef")

    message = str(exc_info.value)
    assert secret not in message
    assert "deadbeef" not in message


def test_hmac_verifier_accepts_valid_signature() -> None:
    secret = "test-webhook-secret"
    timestamp_ms = 1_700_000_000_000
    raw_body = b'{"event":"call_started","call":{"call_id":"call-1"}}'
    signature = _sign_body(raw_body=raw_body, secret=secret, timestamp_ms=timestamp_ms)
    verifier = HmacRetellSignatureVerifier(secret=secret, now_millis=lambda: timestamp_ms)

    verifier.verify(raw_body=raw_body, signature=signature)


def test_hmac_verifier_passes_raw_body_bytes_unchanged() -> None:
    secret = "test-webhook-secret"
    timestamp_ms = 1_700_000_000_000
    raw_body = b'{"spacing": "preserved"}'
    signature = _sign_body(raw_body=raw_body, secret=secret, timestamp_ms=timestamp_ms)
    verifier = HmacRetellSignatureVerifier(secret=secret, now_millis=lambda: timestamp_ms)

    verifier.verify(raw_body=raw_body, signature=signature)


def test_create_verifier_returns_noop_when_verification_disabled() -> None:
    settings = Settings(
        _env_file=None,
        RETELL_WEBHOOK_VERIFICATION_ENABLED="false",
    )

    verifier = create_retell_signature_verifier(settings)

    assert isinstance(verifier, NoOpRetellSignatureVerifier)
    verifier.verify(raw_body=b"{}", signature="")


def test_create_verifier_returns_hmac_when_verification_enabled() -> None:
    settings = Settings(
        _env_file=None,
        RETELL_ENABLED="true",
        RETELL_WEBHOOK_VERIFICATION_ENABLED="true",
        RETELL_WEBHOOK_SECRET="test-webhook-secret",
    )

    verifier = create_retell_signature_verifier(settings)

    assert isinstance(verifier, HmacRetellSignatureVerifier)


def test_verification_errors_are_safe_subclasses() -> None:
    with pytest.raises(RetellSignatureVerificationError):
        FakeRetellSignatureVerifier().verify(raw_body=b"{}", signature="")
