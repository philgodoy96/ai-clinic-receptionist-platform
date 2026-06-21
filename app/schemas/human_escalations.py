from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.human_escalations import (
    HumanEscalationPriority,
    HumanEscalationReason,
    HumanEscalationSource,
    HumanEscalationStatus,
)


class HumanEscalationResponse(BaseModel):
    id: UUID
    conversation_id: UUID
    patient_id: UUID | None
    appointment_id: UUID | None
    status: HumanEscalationStatus
    reason: HumanEscalationReason
    priority: HumanEscalationPriority
    source: HumanEscalationSource
    summary: str | None
    created_by: str | None
    acknowledged_at: datetime | None
    acknowledged_by: str | None
    resolved_at: datetime | None
    resolved_by: str | None
    resolution_notes: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class HumanEscalationListResponse(BaseModel):
    items: list[HumanEscalationResponse]
    next_cursor: str | None


class AcknowledgeHumanEscalationRequest(BaseModel):
    acknowledged_by: str = Field(min_length=1, max_length=160)


class ResolveHumanEscalationRequest(BaseModel):
    resolved_by: str = Field(min_length=1, max_length=160)
    resolution_notes: str | None = None
