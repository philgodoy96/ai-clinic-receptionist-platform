from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, desc, or_, select
from sqlalchemy.orm import Session

from app.domain.human_escalations import (
    HumanEscalationPriority,
    HumanEscalationReason,
    HumanEscalationStatus,
)
from app.models.human_escalation import HumanEscalation
from app.services.human_escalation_pagination import HumanEscalationCursor

_ACTIVE_STATUSES = (
    HumanEscalationStatus.OPEN,
    HumanEscalationStatus.ACKNOWLEDGED,
)


class SQLAlchemyHumanEscalationRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, escalation: HumanEscalation) -> HumanEscalation:
        self.session.add(escalation)
        self.session.flush()

        return escalation

    def get_by_id(self, escalation_id: UUID) -> HumanEscalation | None:
        return self.session.get(HumanEscalation, escalation_id)

    def get_active_by_conversation_id(
        self,
        conversation_id: UUID,
    ) -> HumanEscalation | None:
        statement = select(HumanEscalation).where(
            HumanEscalation.conversation_id == conversation_id,
            HumanEscalation.status.in_(_ACTIVE_STATUSES),
        )

        return self.session.scalars(statement).first()

    def list(
        self,
        *,
        limit: int,
        cursor: HumanEscalationCursor | None = None,
        status: HumanEscalationStatus | None = None,
        reason: HumanEscalationReason | None = None,
        priority: HumanEscalationPriority | None = None,
        conversation_id: UUID | None = None,
        patient_id: UUID | None = None,
        appointment_id: UUID | None = None,
        assigned_to: str | None = None,
        unassigned: bool | None = None,
        overdue: bool | None = None,
        now: datetime | None = None,
    ) -> Sequence[HumanEscalation]:
        statement = select(HumanEscalation)

        if cursor is not None:
            statement = statement.where(
                or_(
                    HumanEscalation.created_at < cursor.created_at,
                    and_(
                        HumanEscalation.created_at == cursor.created_at,
                        HumanEscalation.id < cursor.id,
                    ),
                ),
            )

        if status is not None:
            statement = statement.where(HumanEscalation.status == status)

        if reason is not None:
            statement = statement.where(HumanEscalation.reason == reason)

        if priority is not None:
            statement = statement.where(HumanEscalation.priority == priority)

        if conversation_id is not None:
            statement = statement.where(
                HumanEscalation.conversation_id == conversation_id,
            )

        if patient_id is not None:
            statement = statement.where(HumanEscalation.patient_id == patient_id)

        if appointment_id is not None:
            statement = statement.where(
                HumanEscalation.appointment_id == appointment_id,
            )

        if assigned_to is not None:
            statement = statement.where(HumanEscalation.assigned_to == assigned_to)

        if unassigned is True:
            statement = statement.where(HumanEscalation.assigned_to.is_(None))
        elif unassigned is False:
            statement = statement.where(HumanEscalation.assigned_to.isnot(None))

        if overdue is not None:
            if now is None:
                msg = "now is required when the overdue filter is set"
                raise ValueError(msg)

            if overdue:
                statement = statement.where(
                    HumanEscalation.due_at.isnot(None),
                    HumanEscalation.due_at < now,
                    HumanEscalation.status.in_(_ACTIVE_STATUSES),
                )
            else:
                statement = statement.where(
                    or_(
                        HumanEscalation.due_at.is_(None),
                        HumanEscalation.due_at >= now,
                        HumanEscalation.status.notin_(_ACTIVE_STATUSES),
                    ),
                )

        statement = statement.order_by(
            desc(HumanEscalation.created_at),
            desc(HumanEscalation.id),
        ).limit(limit)

        return list(self.session.scalars(statement).all())

    def update(self, escalation: HumanEscalation) -> HumanEscalation:
        self.session.add(escalation)
        self.session.flush()

        return escalation
