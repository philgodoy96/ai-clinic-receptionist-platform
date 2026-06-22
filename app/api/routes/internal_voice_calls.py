from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_voice_call_inspection_service
from app.api.errors import APIError
from app.domain.voice_calls.enums import VoiceCallStatus
from app.schemas.voice_calls import (
    VoiceCallEventListResponse,
    VoiceCallListResponse,
    VoiceCallResponse,
    voice_call_event_to_response,
    voice_call_to_response,
)
from app.services.voice_call_pagination import (
    InvalidVoiceCallCursorError,
    InvalidVoiceCallEventCursorError,
)
from app.services.voice_calls import (
    InvalidVoiceCallLimitError,
    VoiceCallInspectionService,
    VoiceCallListFilters,
    VoiceCallNotFoundError,
)

router = APIRouter(
    prefix="/api/v1/internal/voice-calls",
    tags=["internal-voice-calls"],
)


@router.get("", response_model=VoiceCallListResponse)
def list_voice_calls(
    service: Annotated[VoiceCallInspectionService, Depends(get_voice_call_inspection_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query()] = None,
    status_filter: Annotated[VoiceCallStatus | None, Query(alias="status")] = None,
    provider: Annotated[str | None, Query(max_length=64)] = None,
    provider_call_id: Annotated[str | None, Query(max_length=120)] = None,
    created_after: Annotated[datetime | None, Query()] = None,
) -> VoiceCallListResponse:
    try:
        result = service.list_voice_calls(
            limit=limit,
            cursor=cursor,
            filters=VoiceCallListFilters(
                status=status_filter,
                provider=provider,
                provider_call_id=provider_call_id,
                created_after=created_after,
            ),
        )
    except InvalidVoiceCallCursorError as exc:
        raise APIError(
            status_code=400,
            code="invalid_voice_call_cursor",
            message="Invalid voice call cursor.",
        ) from exc
    except InvalidVoiceCallLimitError as exc:
        raise APIError(
            status_code=400,
            code="invalid_voice_call_limit",
            message="Voice call limit must be between 1 and 100.",
        ) from exc

    return VoiceCallListResponse(
        items=[voice_call_to_response(item) for item in result.items],
        next_cursor=result.next_cursor,
    )


@router.get("/{voice_call_id}/events", response_model=VoiceCallEventListResponse)
def list_voice_call_events(
    voice_call_id: UUID,
    service: Annotated[VoiceCallInspectionService, Depends(get_voice_call_inspection_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query()] = None,
) -> VoiceCallEventListResponse:
    try:
        result = service.list_voice_call_events(
            voice_call_id=voice_call_id,
            limit=limit,
            cursor=cursor,
        )
    except VoiceCallNotFoundError as exc:
        raise APIError(
            status_code=404,
            code="voice_call_not_found",
            message="Voice call was not found.",
        ) from exc
    except InvalidVoiceCallEventCursorError as exc:
        raise APIError(
            status_code=400,
            code="invalid_voice_call_event_cursor",
            message="Invalid voice call event cursor.",
        ) from exc
    except InvalidVoiceCallLimitError as exc:
        raise APIError(
            status_code=400,
            code="invalid_voice_call_limit",
            message="Voice call limit must be between 1 and 100.",
        ) from exc

    return VoiceCallEventListResponse(
        items=[voice_call_event_to_response(item) for item in result.items],
        next_cursor=result.next_cursor,
    )


@router.get("/{voice_call_id}", response_model=VoiceCallResponse)
def get_voice_call(
    voice_call_id: UUID,
    service: Annotated[VoiceCallInspectionService, Depends(get_voice_call_inspection_service)],
) -> VoiceCallResponse:
    try:
        voice_call = service.get_voice_call(voice_call_id)
    except VoiceCallNotFoundError as exc:
        raise APIError(
            status_code=404,
            code="voice_call_not_found",
            message="Voice call was not found.",
        ) from exc

    return voice_call_to_response(voice_call)
