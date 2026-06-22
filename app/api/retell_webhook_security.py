from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Request, status
from pydantic import BaseModel, ValidationError

from app.api.dependencies import get_retell_signature_verifier
from app.api.errors import (
    INVALID_RETELL_PAYLOAD_CODE,
    RETELL_DISABLED_CODE,
    RETELL_PAYLOAD_TOO_LARGE_CODE,
    RETELL_SIGNATURE_INVALID_CODE,
    RETELL_SIGNATURE_MISSING_CODE,
    RETELL_WEBHOOK_VERIFICATION_UNAVAILABLE_CODE,
    APIError,
)
from app.core.config import Settings, get_settings
from app.integrations.retell.signature import (
    RetellInvalidSignatureError,
    RetellMissingSignatureError,
    RetellSignatureVerificationError,
    RetellSignatureVerifier,
)

logger = logging.getLogger("app.retell_webhook_security")

_RETELL_RAW_BODY_STATE_KEY = "retell_raw_body"


async def require_retell_webhook_security(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    verifier: Annotated[RetellSignatureVerifier, Depends(get_retell_signature_verifier)],
) -> None:
    if not settings.retell_enabled:
        raise APIError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code=RETELL_DISABLED_CODE,
            message="Retell integration is disabled.",
        )

    _ensure_supported_content_type(request)

    if _verification_required(settings):
        if not (settings.retell_webhook_secret or "").strip():
            raise APIError(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code=RETELL_WEBHOOK_VERIFICATION_UNAVAILABLE_CODE,
                message="Retell webhook verification is unavailable.",
            )

    raw_body = await _read_raw_body(request, settings.retell_request_max_body_bytes)

    if _verification_required(settings):
        signature = request.headers.get(settings.retell_signature_header_name)
        if signature is None or not signature.strip():
            raise APIError(
                status_code=status.HTTP_401_UNAUTHORIZED,
                code=RETELL_SIGNATURE_MISSING_CODE,
                message="Retell webhook signature is missing.",
            )

        try:
            verifier.verify(raw_body=raw_body, signature=signature)
        except RetellMissingSignatureError as exc:
            raise APIError(
                status_code=status.HTTP_401_UNAUTHORIZED,
                code=RETELL_SIGNATURE_MISSING_CODE,
                message="Retell webhook signature is missing.",
            ) from exc
        except RetellInvalidSignatureError as exc:
            raise APIError(
                status_code=status.HTTP_401_UNAUTHORIZED,
                code=RETELL_SIGNATURE_INVALID_CODE,
                message="Retell webhook signature is invalid.",
            ) from exc
        except RetellSignatureVerificationError as exc:
            logger.error(
                "retell_webhook_verification_failed",
                extra={"event": "retell_webhook_verification_failed"},
            )
            raise APIError(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code=RETELL_WEBHOOK_VERIFICATION_UNAVAILABLE_CODE,
                message="Retell webhook verification is unavailable.",
            ) from exc

    setattr(request.state, _RETELL_RAW_BODY_STATE_KEY, raw_body)


def retell_tool_payload[T: BaseModel](
    model: type[T],
) -> Callable[..., Awaitable[T]]:
    async def _parse_payload(request: Request) -> T:
        raw_body = getattr(request.state, _RETELL_RAW_BODY_STATE_KEY, None)
        if raw_body is None:
            raise APIError(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code=RETELL_WEBHOOK_VERIFICATION_UNAVAILABLE_CODE,
                message="Retell webhook verification is unavailable.",
            )

        try:
            parsed = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise APIError(
                status_code=status.HTTP_400_BAD_REQUEST,
                code=INVALID_RETELL_PAYLOAD_CODE,
                message="Retell payload is not valid JSON.",
            ) from exc

        if not isinstance(parsed, dict):
            raise APIError(
                status_code=status.HTTP_400_BAD_REQUEST,
                code=INVALID_RETELL_PAYLOAD_CODE,
                message="Retell payload must be a JSON object.",
            )

        try:
            return model.model_validate(parsed)
        except ValidationError as exc:
            raise APIError(
                status_code=status.HTTP_400_BAD_REQUEST,
                code=INVALID_RETELL_PAYLOAD_CODE,
                message="Retell payload is invalid.",
            ) from exc

    return _parse_payload


def _verification_required(settings: Settings) -> bool:
    return (
        settings.retell_webhook_verification_enabled
        and not settings.retell_allow_insecure_webhooks
    )


def _ensure_supported_content_type(request: Request) -> None:
    media_type = request.headers.get("content-type", "").split(";", maxsplit=1)[0].strip().lower()
    if media_type not in ("application/json", ""):
        raise APIError(
            status_code=status.HTTP_400_BAD_REQUEST,
            code=INVALID_RETELL_PAYLOAD_CODE,
            message="Retell payload content type is not supported.",
        )


async def _read_raw_body(request: Request, max_bytes: int) -> bytes:
    content_length_header = request.headers.get("content-length")
    if content_length_header is not None:
        try:
            content_length = int(content_length_header)
        except ValueError as exc:
            raise APIError(
                status_code=status.HTTP_400_BAD_REQUEST,
                code=INVALID_RETELL_PAYLOAD_CODE,
                message="Retell payload content length is invalid.",
            ) from exc

        if content_length > max_bytes:
            raise APIError(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                code=RETELL_PAYLOAD_TOO_LARGE_CODE,
                message="Retell payload is too large.",
            )

    body = await request.body()
    if len(body) > max_bytes:
        raise APIError(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            code=RETELL_PAYLOAD_TOO_LARGE_CODE,
            message="Retell payload is too large.",
        )

    return body
