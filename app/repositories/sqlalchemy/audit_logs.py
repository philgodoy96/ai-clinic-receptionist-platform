from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import and_, desc, or_, select
from sqlalchemy.orm import Session

from app.domain.audit.enums import AuditActorType, AuditEventOutcome, AuditEventType
from app.models.audit import AuditLog
from app.services.audit_log_pagination import AuditLogCursor


class SQLAlchemyAuditLogRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, audit_log: AuditLog) -> AuditLog:
        self.session.add(audit_log)
        self.session.flush()

        return audit_log

    def add_best_effort(self, audit_log: AuditLog) -> None:
        try:
            with self.session.begin_nested():
                self.session.add(audit_log)
                self.session.flush()
        except Exception:
            return

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
        statement = select(AuditLog)

        if cursor is not None:
            statement = statement.where(
                or_(
                    AuditLog.created_at < cursor.created_at,
                    and_(
                        AuditLog.created_at == cursor.created_at,
                        AuditLog.id < cursor.id,
                    ),
                ),
            )

        if event_type is not None:
            statement = statement.where(AuditLog.event_type == event_type)

        if outcome is not None:
            statement = statement.where(AuditLog.outcome == outcome)

        if actor_type is not None:
            statement = statement.where(AuditLog.actor_type == actor_type)

        if source is not None:
            statement = statement.where(AuditLog.source == source)

        if patient_id is not None:
            statement = statement.where(AuditLog.patient_id == patient_id)

        if appointment_id is not None:
            statement = statement.where(AuditLog.appointment_id == appointment_id)

        if call_id is not None:
            statement = statement.where(AuditLog.call_id == call_id)

        if conversation_id is not None:
            statement = statement.where(AuditLog.conversation_id == conversation_id)

        statement = statement.order_by(
            desc(AuditLog.created_at),
            desc(AuditLog.id),
        ).limit(limit)

        return list(self.session.scalars(statement).all())
