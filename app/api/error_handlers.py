from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, cast

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.errors import APIError
from app.core.request_context import get_correlation_id, get_request_id
from app.schemas.errors import ErrorBody, ErrorResponse

logger = logging.getLogger("app.errors")

_ExceptionHandler = Callable[
    [Request, Exception],
    JSONResponse | Awaitable[JSONResponse],
]


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(APIError, cast(_ExceptionHandler, api_error_handler))
    app.add_exception_handler(
        StarletteHTTPException,
        cast(_ExceptionHandler, http_exception_handler),
    )
    app.add_exception_handler(
        RequestValidationError,
        cast(_ExceptionHandler, validation_error_handler),
    )
    app.add_exception_handler(Exception, unexpected_error_handler)


async def api_error_handler(request: Request, exc: APIError) -> JSONResponse:
    return build_error_response(
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        details=exc.details,
    )


async def http_exception_handler(
    request: Request,
    exc: StarletteHTTPException,
) -> JSONResponse:
    detail = exc.detail

    if isinstance(detail, dict) and "code" in detail and "message" in detail:
        code = str(detail["code"])
        message = str(detail["message"])
        details = detail.get("details")
    elif isinstance(detail, str):
        code = f"http_{exc.status_code}"
        message = detail
        details = None
    else:
        code = f"http_{exc.status_code}"
        message = "HTTP error"
        details = None

    return build_error_response(
        status_code=exc.status_code,
        code=code,
        message=message,
        details=details,
    )


async def validation_error_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    return build_error_response(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        code="validation_error",
        message="Request validation failed.",
        details={
            "errors": sanitize_validation_errors(exc.errors()),
        },
    )


async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception(
        "unhandled_api_error",
        extra={
            "event": "unhandled_api_error",
            "path": request.url.path,
            "method": request.method,
        },
    )

    return build_error_response(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        code="internal_server_error",
        message="Internal server error.",
        details=None,
    )


def build_error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    details: Any | None = None,
) -> JSONResponse:
    response = ErrorResponse(
        error=ErrorBody(
            code=code,
            message=message,
            details=details,
            request_id=get_request_id(),
            correlation_id=get_correlation_id(),
        ),
    )

    return JSONResponse(
        status_code=status_code,
        content=response.model_dump(mode="json"),
    )


def sanitize_validation_errors(errors: Sequence[Any]) -> list[dict[str, Any]]:
    sanitized_errors: list[dict[str, Any]] = []

    for error in errors:
        if not isinstance(error, dict):
            continue

        sanitized_error = {
            "loc": error.get("loc"),
            "msg": error.get("msg"),
            "type": error.get("type"),
        }
        sanitized_errors.append(sanitized_error)

    return sanitized_errors