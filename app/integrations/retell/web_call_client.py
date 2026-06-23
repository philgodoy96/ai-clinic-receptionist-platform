from __future__ import annotations

import json
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

RETELL_CREATE_WEB_CALL_URL = "https://api.retellai.com/v2/create-web-call"
RETELL_HTTP_USER_AGENT = "ai-clinic-receptionist-platform/1.0"


class RetellWebCallClientError(Exception):
    """Raised when the Retell web call HTTP client cannot complete a request."""


class RetellWebCallTimeoutError(RetellWebCallClientError):
    """Raised when the Retell web call HTTP client times out."""


class RetellWebCallClient(Protocol):
    def create_web_call(
        self,
        *,
        payload: dict[str, Any],
        timeout_seconds: int,
    ) -> dict[str, Any]:
        raise NotImplementedError


class UrllibRetellWebCallClient:
    def __init__(self, *, api_key: str) -> None:
        self._api_key = api_key

    def create_web_call(
        self,
        *,
        payload: dict[str, Any],
        timeout_seconds: int,
    ) -> dict[str, Any]:
        request = Request(
            RETELL_CREATE_WEB_CALL_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "User-Agent": RETELL_HTTP_USER_AGENT,
            },
            method="POST",
        )

        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except HTTPError as exc:
            raise RetellWebCallClientError("retell create-web-call request failed") from exc
        except TimeoutError as exc:
            raise RetellWebCallTimeoutError("retell create-web-call request timed out") from exc
        except URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise RetellWebCallTimeoutError(
                    "retell create-web-call request timed out",
                ) from exc
            raise RetellWebCallClientError("retell create-web-call request failed") from exc

        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            raise RetellWebCallClientError("retell create-web-call returned invalid json") from exc

        if not isinstance(parsed, dict):
            raise RetellWebCallClientError("retell create-web-call returned unexpected response")

        return parsed
