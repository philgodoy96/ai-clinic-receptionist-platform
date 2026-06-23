from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from app.domain.audit.enums import AuditActorType, AuditEventOutcome, AuditEventType
from app.models.audit import AuditLog
from app.services.audit_log_pagination import AuditLogCursor


class AuditLogRepository(Protocol):
    def add(self, audit_log: AuditLog) -> AuditLog:
        raise NotImplementedError

    def list_recent(
        self,
        *,
        limit: int,
        cursor: AuditLogCursor | None = None,
        event_type: AuditEventType | None = None,
        outcome: AuditEventOutcome | None = None,
        actor_type: AuditActorType | None = None,
        source: str | None = None,
        patient_id: UUID | None = None,
        appointment_id: UUID | None = None,
        call_id: str | None = None,
        conversation_id: str | None = None,
    ) -> Sequence[AuditLog]:
        raise NotImplementedError
