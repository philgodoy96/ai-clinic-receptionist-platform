from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.voice_calls.enums import NormalizedVoiceCallEventType, VoiceCallStatus
from app.domain.voice_calls.lifecycle import build_safe_event_metadata
from app.models.voice_calls import VoiceCall, VoiceCallEvent


class VoiceCallResponse(BaseModel):
    id: UUID
    provider: str
    provider_call_id: str
    conversation_id: UUID | None
    status: VoiceCallStatus
    direction: str | None
    from_number_redacted: str | None
    to_number_redacted: str | None
    started_at: datetime | None
    ended_at: datetime | None
    last_event_at: datetime | None
    event_metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class VoiceCallEventResponse(BaseModel):
    id: UUID
    voice_call_id: UUID
    provider: str
    provider_call_id: str
    provider_event_id: str | None
    event_type: str
    normalized_event_type: NormalizedVoiceCallEventType | None
    occurred_at: datetime
    sequence_number: int | None
    event_metadata: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class VoiceCallListResponse(BaseModel):
    items: list[VoiceCallResponse]
    next_cursor: str | None


class VoiceCallEventListResponse(BaseModel):
    items: list[VoiceCallEventResponse]
    next_cursor: str | None


def voice_call_to_response(voice_call: VoiceCall) -> VoiceCallResponse:
    response = VoiceCallResponse.model_validate(voice_call)
    return response.model_copy(
        update={"event_metadata": build_safe_event_metadata(voice_call.event_metadata)},
    )


def voice_call_event_to_response(voice_call_event: VoiceCallEvent) -> VoiceCallEventResponse:
    response = VoiceCallEventResponse.model_validate(voice_call_event)
    return response.model_copy(
        update={
            "event_metadata": build_safe_event_metadata(voice_call_event.event_metadata),
        },
    )
