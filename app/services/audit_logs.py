from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from app.domain.audit.enums import AuditActorType, AuditEventOutcome, AuditEventType
from app.models.audit import AuditLog
from app.repositories.audit_logs import AuditLogRepository
from app.services.audit_log_pagination import (
    AuditLogCursor,
    decode_audit_log_cursor,
    encode_audit_log_cursor,
)


class InvalidAuditLogLimitError(ValueError):
    """Raised when an audit log page size is invalid."""


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
    event_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AuditLogListFilters:
    event_type: AuditEventType | None = None
    outcome: AuditEventOutcome | None = None
    actor_type: AuditActorType | None = None
    source: str | None = None
    patient_id: UUID | None = None
    appointment_id: UUID | None = None
    call_id: str | None = None
    conversation_id: str | None = None


@dataclass(frozen=True, slots=True)
class AuditLogListResult:
    items: Sequence[AuditLog]
    next_cursor: str | None


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
            event_metadata=payload.event_metadata,
        )

        return self.repository.add(audit_log)

    def record_best_effort(self, payload: AuditLogCreate) -> None:
        try:
            self.record(payload)
        except Exception:
            return

    def list_logs(
        self,
        *,
        limit: int,
        cursor: str | None = None,
        filters: AuditLogListFilters | None = None,
    ) -> AuditLogListResult:
        if limit < 1 or limit > 100:
            raise InvalidAuditLogLimitError("limit must be between 1 and 100")

        decoded_cursor = decode_audit_log_cursor(cursor) if cursor is not None else None
        normalized_filters = filters or AuditLogListFilters()
        fetched_items = list(
            self.repository.list_recent(
                limit=limit + 1,
                cursor=decoded_cursor,
                event_type=normalized_filters.event_type,
                outcome=normalized_filters.outcome,
                actor_type=normalized_filters.actor_type,
                source=normalized_filters.source,
                patient_id=normalized_filters.patient_id,
                appointment_id=normalized_filters.appointment_id,
                call_id=normalized_filters.call_id,
                conversation_id=normalized_filters.conversation_id,
            ),
        )

        items = fetched_items[:limit]
        next_cursor = None

        if len(fetched_items) > limit and items:
            last_item = items[-1]
            next_cursor = encode_audit_log_cursor(
                AuditLogCursor(
                    created_at=last_item.created_at,
                    id=last_item.id,
                ),
            )

        return AuditLogListResult(
            items=items,
            next_cursor=next_cursor,
        )