from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.api.demo_guardrail_enforcement import enforce_voice_web_call_allowed
from app.api.dependencies import (
    get_demo_guardrail_service,
    get_public_demo_voice_session_service,
)
from app.api.errors import (
    VOICE_DEMO_CONFIGURATION_ERROR_CODE,
    VOICE_DEMO_DISABLED_CODE,
    VOICE_DEMO_PROVIDER_UNAVAILABLE_CODE,
    APIError,
)
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.schemas.retell_web_call import (
    RetellWebCallRequestSchema,
    RetellWebCallResponseSchema,
    retell_web_call_result_to_response,
)
from app.services.demo_guardrails import DemoGuardrailService
from app.services.public_demo_voice_session import (
    PublicDemoVoiceSessionConversationNotFoundError,
    PublicDemoVoiceSessionRequest,
    PublicDemoVoiceSessionService,
)
from app.services.retell_web_call import (
    RetellWebCallConfigurationError,
    RetellWebCallDisabledError,
    RetellWebCallProviderError,
)

router = APIRouter(prefix="/api/v1/demo/voice", tags=["demo-voice"])


@router.post(
    "/retell-web-call",
    response_model=RetellWebCallResponseSchema,
    status_code=status.HTTP_200_OK,
)
def create_retell_web_call(
    request: Request,
    payload: RetellWebCallRequestSchema,
    settings: Annotated[Settings, Depends(get_settings)],
    guardrails: Annotated[DemoGuardrailService, Depends(get_demo_guardrail_service)],
    session_service: Annotated[
        PublicDemoVoiceSessionService,
        Depends(get_public_demo_voice_session_service),
    ],
    db: Annotated[Session, Depends(get_db)],
) -> RetellWebCallResponseSchema:
    if not settings.retell_web_call_enabled:
        raise APIError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code=VOICE_DEMO_DISABLED_CODE,
            message="Voice demo is disabled.",
        )

    enforce_voice_web_call_allowed(request, settings, guardrails)

    try:
        result = session_service.create_session(
            PublicDemoVoiceSessionRequest(
                demo_session_id=payload.demo_session_id,
                conversation_id=payload.conversation_id,
            ),
        )
        db.commit()
    except PublicDemoVoiceSessionConversationNotFoundError as exc:
        db.rollback()
        raise APIError(
            status_code=status.HTTP_400_BAD_REQUEST,
            code=VOICE_DEMO_CONFIGURATION_ERROR_CODE,
            message="Conversation was not found.",
        ) from exc
    except RetellWebCallDisabledError as exc:
        db.rollback()
        raise APIError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code=VOICE_DEMO_DISABLED_CODE,
            message="Voice demo is disabled.",
        ) from exc
    except RetellWebCallConfigurationError as exc:
        db.rollback()
        raise APIError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code=VOICE_DEMO_CONFIGURATION_ERROR_CODE,
            message="Voice demo is not configured.",
        ) from exc
    except RetellWebCallProviderError as exc:
        db.rollback()
        raise APIError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code=VOICE_DEMO_PROVIDER_UNAVAILABLE_CODE,
            message="Voice provider is temporarily unavailable.",
        ) from exc
    except Exception:
        db.rollback()
        raise

    return retell_web_call_result_to_response(result)
