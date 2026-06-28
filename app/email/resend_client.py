from __future__ import annotations

import json
import re
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_RESEND_EMAILS_ENDPOINT = "https://api.resend.com/emails"
_RESEND_USER_AGENT = "ai-clinic-receptionist-platform/1.0"
_MAX_ERROR_BODY_CHARS = 500
_BEARER_TOKEN_PATTERN = re.compile(r"Bearer\s+\S+", re.IGNORECASE)
_RESEND_KEY_PATTERN = re.compile(r"re_[A-Za-z0-9_\-]+")


class ResendEmailClientError(Exception):
    """Raised when the Resend HTTP client cannot complete a request.

    ``status_code`` and ``response_body`` carry sanitized diagnostic detail
    about a failed Resend HTTP call so callers can surface the real reason
    without exposing the API key or Authorization header.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        response_body: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


def _sanitize_resend_error_detail(raw: str | None) -> str:
    if not raw:
        return ""

    collapsed = " ".join(raw.split())
    collapsed = _BEARER_TOKEN_PATTERN.sub("Bearer [redacted]", collapsed)
    collapsed = _RESEND_KEY_PATTERN.sub("[redacted]", collapsed)

    if len(collapsed) > _MAX_ERROR_BODY_CHARS:
        return collapsed[:_MAX_ERROR_BODY_CHARS] + "...(truncated)"

    return collapsed


def _read_http_error_body(exc: HTTPError) -> str:
    try:
        raw = exc.read()
    except Exception:  # noqa: BLE001 - body is best-effort diagnostic only
        return ""

    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")

    return str(raw)


class ResendEmailClient(Protocol):
    def send_email(
        self,
        *,
        payload: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError


class HttpResendEmailClient:
    def __init__(self, *, api_key: str, timeout_seconds: int = 10) -> None:
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds

    def send_email(
        self,
        *,
        payload: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": _RESEND_USER_AGENT,
        }
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key

        request = Request(
            _RESEND_EMAILS_ENDPOINT,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except HTTPError as exc:
            raise ResendEmailClientError(
                "resend api request failed",
                status_code=exc.code,
                response_body=_sanitize_resend_error_detail(_read_http_error_body(exc)),
            ) from exc
        except URLError as exc:
            raise ResendEmailClientError(
                "resend api request failed",
                response_body=_sanitize_resend_error_detail(str(exc.reason)),
            ) from exc

        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ResendEmailClientError("resend api returned invalid json") from exc

        if not isinstance(parsed, dict):
            raise ResendEmailClientError("resend api returned unexpected response")

        return parsed
