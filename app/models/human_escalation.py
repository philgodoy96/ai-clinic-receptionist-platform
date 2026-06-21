from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.domain.human_escalations import (
    HumanEscalationPriority,
    HumanEscalationReason,
    HumanEscalationSource,
    HumanEscalationStatus,
)


def human_escalation_status_values(
    enum_class: type[HumanEscalationStatus],
) -> list[str]:
    return [item.value for item in enum_class]


def human_escalation_reason_values(
    enum_class: type[HumanEscalationReason],
) -> list[str]:
    return [item.value for item in enum_class]


def human_escalation_priority_values(
    enum_class: type[HumanEscalationPriority],
) -> list[str]:
    return [item.value for item in enum_class]


def human_escalation_source_values(
    enum_class: type[HumanEscalationSource],
) -> list[str]:
    return [item.value for item in enum_class]


class HumanEscalation(Base):
    __tablename__ = "human_escalations"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    patient_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("patients.id", ondelete="SET NULL"),
        nullable=True,
    )
    appointment_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("appointments.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[HumanEscalationStatus] = mapped_column(
        SAEnum(
            HumanEscalationStatus,
            values_callable=human_escalation_status_values,
            name="human_escalation_status",
        ),
        nullable=False,
        default=HumanEscalationStatus.OPEN,
    )
    reason: Mapped[HumanEscalationReason] = mapped_column(
        SAEnum(
            HumanEscalationReason,
            values_callable=human_escalation_reason_values,
            name="human_escalation_reason",
        ),
        nullable=False,
    )
    priority: Mapped[HumanEscalationPriority] = mapped_column(
        SAEnum(
            HumanEscalationPriority,
            values_callable=human_escalation_priority_values,
            name="human_escalation_priority",
        ),
        nullable=False,
        default=HumanEscalationPriority.NORMAL,
    )
    source: Mapped[HumanEscalationSource] = mapped_column(
        SAEnum(
            HumanEscalationSource,
            values_callable=human_escalation_source_values,
            name="human_escalation_source",
        ),
        nullable=False,
        default=HumanEscalationSource.CHAT,
    )
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    handoff_context: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=True,
    )
    created_by: Mapped[str | None] = mapped_column(String(160), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    acknowledged_by: Mapped[str | None] = mapped_column(String(160), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(160), nullable=True)
    resolution_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    assigned_to: Mapped[str | None] = mapped_column(String(160), nullable=True)
    assigned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_human_escalations_status_created_at", "status", "created_at"),
        Index("ix_human_escalations_conversation_id", "conversation_id"),
        Index("ix_human_escalations_patient_id", "patient_id"),
        Index("ix_human_escalations_appointment_id", "appointment_id"),
        Index("ix_human_escalations_reason_created_at", "reason", "created_at"),
        Index("ix_human_escalations_assigned_to", "assigned_to"),
        Index("ix_human_escalations_due_at", "due_at"),
        Index("ix_human_escalations_status_due_at", "status", "due_at"),
        Index(
            "uq_human_escalations_active_conversation_id",
            "conversation_id",
            unique=True,
            postgresql_where=status.in_(
                (HumanEscalationStatus.OPEN, HumanEscalationStatus.ACKNOWLEDGED),
            ),
        ),
    )
