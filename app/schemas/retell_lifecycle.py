from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from app.services.retell_call_lifecycle import RetellLifecyclePayload

_SAFE_METADATA_FIELD_NAMES = (
    "agent_id",
    "agent_version",
    "call_status",
    "call_type",
    "disconnect_reason",
    "disconnection_reason",
    "duration_ms",
    "duration_seconds",
    "end_reason",
    "occurred_at_source",
)


class RetellLifecycleCallPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    call_id: str = Field(min_length=1, max_length=120)
    direction: str | None = Field(default=None, max_length=32)
    from_number: str | None = Field(default=None, max_length=40)
    to_number: str | None = Field(default=None, max_length=40)


class RetellLifecycleWebhookRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    event: str = Field(
        min_length=1,
        max_length=120,
        validation_alias=AliasChoices("event", "event_type"),
    )
    occurred_at: datetime
    call_id: str | None = Field(default=None, max_length=120)
    call: RetellLifecycleCallPayload | None = None
    event_id: str | None = Field(
        default=None,
        max_length=120,
        validation_alias=AliasChoices("event_id", "provider_event_id"),
    )
    sequence_number: int | None = None
    direction: str | None = Field(default=None, max_length=32)
    from_number: str | None = Field(default=None, max_length=40)
    to_number: str | None = Field(default=None, max_length=40)
    agent_id: str | None = Field(default=None, max_length=120)
    agent_version: str | None = Field(default=None, max_length=120)
    call_status: str | None = Field(default=None, max_length=120)
    call_type: str | None = Field(default=None, max_length=120)
    disconnect_reason: str | None = Field(default=None, max_length=120)
    disconnection_reason: str | None = Field(default=None, max_length=120)
    end_reason: str | None = Field(default=None, max_length=120)
    duration_ms: int | None = None
    duration_seconds: int | None = None
    occurred_at_source: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def validate_call_reference(self) -> RetellLifecycleWebhookRequest:
        if self.call_id is None and self.call is None:
            msg = "call_id or call is required"
            raise ValueError(msg)

        return self

    def resolve_provider_call_id(self) -> str:
        if self.call is not None:
            return self.call.call_id

        if self.call_id is None:
            msg = "call_id is required"
            raise ValueError(msg)

        return self.call_id


class RetellLifecycleWebhookResponse(BaseModel):
    status: Literal["accepted"] = "accepted"
    voice_call_id: UUID
    event_id: UUID
    duplicate: bool


def build_retell_lifecycle_payload(
    request: RetellLifecycleWebhookRequest,
) -> RetellLifecyclePayload:
    safe_metadata: dict[str, Any] = {}
    for field_name in _SAFE_METADATA_FIELD_NAMES:
        value = getattr(request, field_name)
        if value is not None:
            safe_metadata[field_name] = value

    direction = request.direction
    from_number = request.from_number
    to_number = request.to_number
    if request.call is not None:
        direction = direction or request.call.direction
        from_number = from_number or request.call.from_number
        to_number = to_number or request.call.to_number

    return RetellLifecyclePayload(
        provider_call_id=request.resolve_provider_call_id(),
        event_type=request.event,
        occurred_at=request.occurred_at,
        provider_event_id=request.event_id,
        sequence_number=request.sequence_number,
        direction=direction,
        from_number=from_number,
        to_number=to_number,
        safe_metadata=safe_metadata,
    )
