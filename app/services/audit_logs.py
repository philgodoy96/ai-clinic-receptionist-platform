from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from app.domain.audit.enums import AuditActorType, AuditEventOutcome, AuditEventType
from app.models.audit import AuditLog
from app.repositories.audit_logs import AuditLogRepository


@dataclass(frozen=True, slots=True)
class AuditLogCreate:
    event_type: AuditEventType
    outcome: AuditEventOutcome
    actor_type: AuditActorType
    source: str
    actor_id: str | None = None
    request_id: str | None = None
    call_id: str | None = None
    conversation_id: str | None = None
    patient_id: UUID | None = None
    appointment_id: UUID | None = None
    availability_slot_id: UUID | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class AuditLogService:
    def __init__(self, *, repository: AuditLogRepository) -> None:
        self.repository = repository

    def record(self, payload: AuditLogCreate) -> AuditLog:
        audit_log = AuditLog(
            event_type=payload.event_type,
            outcome=payload.outcome,
            actor_type=payload.actor_type,
            actor_id=payload.actor_id,
            source=payload.source,
            request_id=payload.request_id,
            call_id=payload.call_id,
            conversation_id=payload.conversation_id,
            patient_id=payload.patient_id,
            appointment_id=payload.appointment_id,
            availability_slot_id=payload.availability_slot_id,
            event_metadata=payload.metadata,
        )

        return self.repository.add(audit_log)