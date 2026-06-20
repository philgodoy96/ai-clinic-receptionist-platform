from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from uuid import uuid4

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.request_context import reset_request_context, set_request_context

REQUEST_ID_HEADER = "X-Request-ID"
CORRELATION_ID_HEADER = "X-Correlation-ID"


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
        request.state.started_at = time.perf_counter()

        try:
            response = await call_next(request)
        finally:
            reset_request_context(tokens)

        response.headers[REQUEST_ID_HEADER] = request_id
        response.headers[CORRELATION_ID_HEADER] = correlation_id

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