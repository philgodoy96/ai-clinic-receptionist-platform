from __future__ import annotations

import json
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class ResendEmailClientError(Exception):
    """Raised when the Resend HTTP client cannot complete a request."""


class ResendEmailClient(Protocol):
    def send_email(self, *, payload: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError


class HttpResendEmailClient:
    def __init__(self, *, api_key: str, timeout_seconds: int = 10) -> None:
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds

    def send_email(self, *, payload: dict[str, Any]) -> dict[str, Any]:
        request = Request(
            "https://api.resend.com/emails",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except HTTPError as exc:
            raise ResendEmailClientError("resend api request failed") from exc
        except URLError as exc:
            raise ResendEmailClientError("resend api request failed") from exc

        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ResendEmailClientError("resend api returned invalid json") from exc

        if not isinstance(parsed, dict):
            raise ResendEmailClientError("resend api returned unexpected response")

        return parsed
