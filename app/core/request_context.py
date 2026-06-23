from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)


@dataclass(frozen=True, slots=True)
class RequestContextTokens:
    request_id: Token[str | None]
    correlation_id: Token[str | None]


def set_request_context(
    *,
    request_id: str,
    correlation_id: str,
) -> RequestContextTokens:
    request_token = _request_id.set(request_id)
    correlation_token = _correlation_id.set(correlation_id)

    return RequestContextTokens(
        request_id=request_token,
        correlation_id=correlation_token,
    )


def reset_request_context(tokens: RequestContextTokens) -> None:
    _request_id.reset(tokens.request_id)
    _correlation_id.reset(tokens.correlation_id)


def get_request_id() -> str | None:
    return _request_id.get()


def get_correlation_id() -> str | None:
    return _correlation_id.get()
