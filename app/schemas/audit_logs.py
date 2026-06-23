from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.domain.audit.enums import AuditActorType, AuditEventOutcome, AuditEventType


class AuditLogResponse(BaseModel):
    id: UUID
    event_type: AuditEventType
    outcome: AuditEventOutcome
    actor_type: AuditActorType
    actor_id: str | None
    source: str
    request_id: str | None
    call_id: str | None
    conversation_id: str | None
    patient_id: UUID | None
    appointment_id: UUID | None
    availability_slot_id: UUID | None
    event_metadata: dict[str, Any]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AuditLogListResponse(BaseModel):
    items: list[AuditLogResponse]
    next_cursor: str | None
