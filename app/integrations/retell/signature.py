from __future__ import annotations

import hashlib
import hmac
import re
import time
from collections.abc import Callable
from typing import Protocol

from app.core.config import Settings

_SIGNATURE_PATTERN = re.compile(r"^v=(\d+),d=(.*)$")
_DEFAULT_MAX_SKEW_MILLIS = 5 * 60 * 1000


class RetellSignatureVerificationError(Exception):
    """Raised when Retell webhook signature verification fails."""


class RetellMissingSignatureError(RetellSignatureVerificationError):
    pass


class RetellInvalidSignatureError(RetellSignatureVerificationError):
    pass


class RetellSignatureVerifier(Protocol):
    def verify(self, *, raw_body: bytes, signature: str) -> None:
        raise NotImplementedError


class NoOpRetellSignatureVerifier:
    def verify(self, *, raw_body: bytes, signature: str) -> None:
        return None


class FakeRetellSignatureVerifier:
    def __init__(
        self,
        *,
        accept_all: bool = False,
        valid_signatures: set[str] | None = None,
    ) -> None:
        self._accept_all = accept_all
        self._valid_signatures = valid_signatures or set()
        self.last_raw_body: bytes | None = None
        self.last_signature: str | None = None

    def verify(self, *, raw_body: bytes, signature: str) -> None:
        self.last_raw_body = raw_body
        self.last_signature = signature

        if self._accept_all:
            return

        if not signature.strip():
            raise RetellMissingSignatureError("Retell webhook signature is missing")

        if signature not in self._valid_signatures:
            raise RetellInvalidSignatureError("Retell webhook signature is invalid")


class HmacRetellSignatureVerifier:
    def __init__(
        self,
        *,
        secret: str,
        now_millis: Callable[[], int] | None = None,
        max_skew_millis: int = _DEFAULT_MAX_SKEW_MILLIS,
    ) -> None:
        self._secret = secret
        self._now_millis = now_millis or (lambda: int(time.time() * 1000))
        self._max_skew_millis = max_skew_millis

    def verify(self, *, raw_body: bytes, signature: str) -> None:
        if not signature.strip():
            raise RetellMissingSignatureError("Retell webhook signature is missing")

        match = _SIGNATURE_PATTERN.match(signature.strip())
        if match is None:
            raise RetellInvalidSignatureError("Retell webhook signature is invalid")

        timestamp_str, digest = match.group(1), match.group(2)
        try:
            timestamp_ms = int(timestamp_str)
        except ValueError as exc:
            raise RetellInvalidSignatureError("Retell webhook signature is invalid") from exc

        now_ms = self._now_millis()
        if abs(now_ms - timestamp_ms) > self._max_skew_millis:
            raise RetellInvalidSignatureError("Retell webhook signature is invalid")

        body_text = raw_body.decode("utf-8")
        expected_digest = hmac.new(
            self._secret.encode("utf-8"),
            f"{body_text}{timestamp_str}".encode(),
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(expected_digest, digest):
            raise RetellInvalidSignatureError("Retell webhook signature is invalid")


def create_retell_signature_verifier(settings: Settings) -> RetellSignatureVerifier:
    if not settings.retell_webhook_verification_enabled or settings.retell_allow_insecure_webhooks:
        return NoOpRetellSignatureVerifier()

    secret = (settings.retell_webhook_secret or "").strip()
    return HmacRetellSignatureVerifier(secret=secret)
