from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from uuid import uuid4

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.request_context import reset_request_context, set_request_context

REQUEST_ID_HEADER = "X-Request-ID"
CORRELATION_ID_HEADER = "X-Correlation-ID"

logger = logging.getLogger("app.request")


class RequestCorrelationMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = self._resolve_header_or_generate(request, REQUEST_ID_HEADER)
        correlation_id = self._resolve_header_or_default(
            request=request,
            header_name=CORRELATION_ID_HEADER,
            default=request_id,
        )
        tokens = set_request_context(
            request_id=request_id,
            correlation_id=correlation_id,
        )
        request.state.request_id = request_id
        request.state.correlation_id = correlation_id
        started_at = time.perf_counter()
        request.state.started_at = started_at

        try:
            response = await call_next(request)
        except Exception:
            duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
            logger.exception(
                "request_failed",
                extra={
                    "event": "request_failed",
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": duration_ms,
                    "client_host": self._client_host(request),
                },
            )
            raise
        finally:
            reset_request_context(tokens)

        duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
        response.headers[REQUEST_ID_HEADER] = request_id
        response.headers[CORRELATION_ID_HEADER] = correlation_id
        logger.info(
            "request_completed",
            extra={
                "event": "request_completed",
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
                "client_host": self._client_host(request),
            },
        )

        return response

    def _resolve_header_or_generate(self, request: Request, header_name: str) -> str:
        value = request.headers.get(header_name)

        if value is not None and value.strip():
            return value.strip()

        return str(uuid4())

    def _resolve_header_or_default(
        self,
        *,
        request: Request,
        header_name: str,
        default: str,
    ) -> str:
        value = request.headers.get(header_name)

        if value is not None and value.strip():
            return value.strip()

        return default

    def _client_host(self, request: Request) -> str | None:
        if request.client is None:
            return None

        return request.client.host