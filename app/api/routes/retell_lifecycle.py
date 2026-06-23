from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.demo_guardrail_enforcement import require_retell_tool_guardrail
from app.api.dependencies import get_retell_call_lifecycle_service
from app.api.errors import INVALID_RETELL_PAYLOAD_CODE, APIError
from app.api.retell_webhook_security import require_retell_webhook_security, retell_tool_payload
from app.db.session import get_db
from app.integrations.retell.payload_normalization import normalize_retell_lifecycle_payload
from app.schemas.retell_lifecycle import (
    RetellLifecycleWebhookRequest,
    RetellLifecycleWebhookResponse,
    build_retell_lifecycle_payload,
)
from app.services.retell_call_lifecycle import (
    MissingProviderCallIdError,
    RetellCallLifecycleService,
)

router = APIRouter(
    prefix="/api/v1/retell/webhooks",
    tags=["retell-webhooks"],
    dependencies=[
        Depends(require_retell_webhook_security),
        Depends(require_retell_tool_guardrail),
    ],
)


@router.post(
    "/lifecycle",
    response_model=RetellLifecycleWebhookResponse,
    status_code=status.HTTP_200_OK,
)
def ingest_retell_lifecycle_event(
    payload: Annotated[
        RetellLifecycleWebhookRequest,
        Depends(
            retell_tool_payload(
                RetellLifecycleWebhookRequest,
                normalizer=normalize_retell_lifecycle_payload,
            ),
        ),
    ],
    service: Annotated[
        RetellCallLifecycleService,
        Depends(get_retell_call_lifecycle_service),
    ],
    db: Annotated[Session, Depends(get_db)],
) -> RetellLifecycleWebhookResponse:
    lifecycle_payload = build_retell_lifecycle_payload(payload)

    try:
        result = service.ingest_event(lifecycle_payload)
    except MissingProviderCallIdError as exc:
        raise APIError(
            status_code=status.HTTP_400_BAD_REQUEST,
            code=INVALID_RETELL_PAYLOAD_CODE,
            message="Retell lifecycle payload is missing provider_call_id.",
        ) from exc

    db.commit()

    return RetellLifecycleWebhookResponse(
        voice_call_id=result.voice_call.id,
        event_id=result.voice_call_event.id,
        duplicate=not result.created_event,
    )
