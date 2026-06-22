from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Request, status

from app.api.dependencies import get_demo_guardrail_service
from app.api.errors import APIError
from app.core.config import Settings, get_settings
from app.core.request_context import get_correlation_id, get_request_id
from app.services.client_ip import get_client_ip
from app.services.demo_guardrails import (
    DemoGuardrailLimitExceeded,
    DemoGuardrailService,
    DemoGuardrailStoreUnavailable,
)

logger = logging.getLogger("app.demo_guardrails")

DEMO_GUARDRAIL_LIMIT_EXCEEDED_CODE = "demo_guardrail_limit_exceeded"
DEMO_GUARDRAIL_STORE_UNAVAILABLE_CODE = "demo_guardrail_store_unavailable"


def enforce_chat_message_allowed(
    request: Request,
    settings: Settings,
    guardrails: DemoGuardrailService,
) -> str:
    client_ip = get_client_ip(
        request,
        trust_proxy_headers=settings.trust_proxy_headers,
    )
    _enforce_guardrail(
        lambda: guardrails.check_chat_message_allowed(client_ip),
        request=request,
    )
    return client_ip


def enforce_retell_tool_allowed(
    request: Request,
    settings: Settings,
    guardrails: DemoGuardrailService,
) -> None:
    client_ip = get_client_ip(
        request,
        trust_proxy_headers=settings.trust_proxy_headers,
    )
    _enforce_guardrail(
        lambda: guardrails.check_retell_tool_allowed(client_ip),
        request=request,
    )


def require_retell_tool_guardrail(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    guardrails: Annotated[DemoGuardrailService, Depends(get_demo_guardrail_service)],
) -> None:
    enforce_retell_tool_allowed(request, settings, guardrails)


def _enforce_guardrail(
    check: Callable[[], None],
    *,
    request: Request,
) -> None:
    try:
        check()
    except DemoGuardrailLimitExceeded as exc:
        logger.warning(
            "demo_guardrail_limit_exceeded",
            extra={
                "event": "demo_guardrail_limit_exceeded",
                "limit_name": exc.limit_name,
                "endpoint": request.url.path,
                "request_id": get_request_id(),
                "correlation_id": get_correlation_id(),
            },
        )
        raise APIError(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            code=DEMO_GUARDRAIL_LIMIT_EXCEEDED_CODE,
            message="Demo rate limit exceeded.",
            details={
                "limit_name": exc.limit_name,
                "retry_after_seconds": exc.retry_after_seconds,
            },
        ) from exc
    except DemoGuardrailStoreUnavailable as exc:
        logger.error(
            "demo_guardrail_store_unavailable",
            extra={
                "event": "demo_guardrail_store_unavailable",
                "endpoint": request.url.path,
                "request_id": get_request_id(),
                "correlation_id": get_correlation_id(),
            },
        )
        raise APIError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code=DEMO_GUARDRAIL_STORE_UNAVAILABLE_CODE,
            message="Demo guardrail store is unavailable.",
        ) from exc
