from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID, uuid4

from app.domain.audit.enums import AuditActorType, AuditEventOutcome, AuditEventType
from app.models.audit import AuditLog
from app.services.audit_log_pagination import AuditLogCursor
from app.services.audit_logs import AuditLogCreate, AuditLogService


def test_audit_log_service_records_booking_event() -> None:
    repository = FakeAuditLogRepository()
    service = AuditLogService(repository=repository)
    patient_id = uuid4()
    appointment_id = uuid4()
    slot_id = uuid4()

    audit_log = service.record(
        AuditLogCreate(
            event_type=AuditEventType.APPOINTMENT_BOOKING_CONFIRMED,
            outcome=AuditEventOutcome.SUCCESS,
            actor_type=AuditActorType.RETELL,
            actor_id="retell-call-123",
            source="retell_tool",
            call_id="retell-call-123",
            patient_id=patient_id,
            appointment_id=appointment_id,
            availability_slot_id=slot_id,
            event_metadata={"hold_id": str(uuid4())},
        ),
    )

    assert audit_log in repository.audit_logs
    assert audit_log.event_type == AuditEventType.APPOINTMENT_BOOKING_CONFIRMED
    assert audit_log.outcome == AuditEventOutcome.SUCCESS
    assert audit_log.actor_type == AuditActorType.RETELL
    assert audit_log.source == "retell_tool"
    assert audit_log.patient_id == patient_id
    assert audit_log.appointment_id == appointment_id
    assert audit_log.availability_slot_id == slot_id


def test_audit_log_service_records_failure_reason_metadata() -> None:
    repository = FakeAuditLogRepository()
    service = AuditLogService(repository=repository)

    audit_log = service.record(
        AuditLogCreate(
            event_type=AuditEventType.APPOINTMENT_HOLD_FAILED,
            outcome=AuditEventOutcome.FAILURE,
            actor_type=AuditActorType.API,
            actor_id="chat-123",
            source="scheduling_api",
            event_metadata={"reason": "slot_already_held"},
        ),
    )

    assert audit_log.outcome == AuditEventOutcome.FAILURE
    assert audit_log.event_metadata == {"reason": "slot_already_held"}


class FakeAuditLogRepository:
    def __init__(self) -> None:
        self.audit_logs: list[AuditLog] = []

    def add(self, audit_log: AuditLog) -> AuditLog:
        self.audit_logs.append(audit_log)

        return audit_log

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
        return self.audit_logs[:limit]