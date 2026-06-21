from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.human_escalations import (
    HumanEscalationPriority,
    HumanEscalationReason,
    HumanEscalationSource,
    HumanEscalationStatus,
)
from app.models.human_escalation import HumanEscalation

_ACTIVE_ESCALATION_STATUSES = frozenset(
    {
        HumanEscalationStatus.OPEN,
        HumanEscalationStatus.ACKNOWLEDGED,
    },
)


def compute_human_escalation_is_overdue(
    escalation: HumanEscalation,
    *,
    now: datetime,
) -> bool:
    if escalation.due_at is None:
        return False

    if escalation.status not in _ACTIVE_ESCALATION_STATUSES:
        return False

    return escalation.due_at < now


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
    assigned_to: str | None
    assigned_at: datetime | None
    due_at: datetime | None
    is_overdue: bool = False
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


def human_escalation_to_response(
    escalation: HumanEscalation,
    *,
    now: datetime,
) -> HumanEscalationResponse:
    response = HumanEscalationResponse.model_validate(escalation)
    return response.model_copy(
        update={
            "is_overdue": compute_human_escalation_is_overdue(escalation, now=now),
        },
    )


class HumanEscalationListResponse(BaseModel):
    items: list[HumanEscalationResponse]
    next_cursor: str | None


class AcknowledgeHumanEscalationRequest(BaseModel):
    acknowledged_by: str = Field(min_length=1, max_length=160)


class ResolveHumanEscalationRequest(BaseModel):
    resolved_by: str = Field(min_length=1, max_length=160)
    resolution_notes: str | None = None


class AssignHumanEscalationRequest(BaseModel):
    assigned_to: str = Field(min_length=1, max_length=160)
