from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, Index, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.domain.audit.enums import AuditActorType, AuditEventOutcome, AuditEventType


def enum_values(
    enum_class: type[AuditEventType] | type[AuditActorType] | type[AuditEventOutcome],
) -> list[str]:
    return [item.value for item in enum_class]


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    event_type: Mapped[AuditEventType] = mapped_column(
        SAEnum(AuditEventType, values_callable=enum_values, name="audit_event_type"),
        nullable=False,
    )
    outcome: Mapped[AuditEventOutcome] = mapped_column(
        SAEnum(
            AuditEventOutcome,
            values_callable=enum_values,
            name="audit_event_outcome",
        ),
        nullable=False,
    )
    actor_type: Mapped[AuditActorType] = mapped_column(
        SAEnum(AuditActorType, values_callable=enum_values, name="audit_actor_type"),
        nullable=False,
    )
    actor_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    source: Mapped[str] = mapped_column(String(80), nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    call_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    conversation_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    patient_id: Mapped[UUID | None] = mapped_column(nullable=True)
    appointment_id: Mapped[UUID | None] = mapped_column(nullable=True)
    availability_slot_id: Mapped[UUID | None] = mapped_column(nullable=True)
    event_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_audit_logs_created_at_id", "created_at", "id"),
        Index("ix_audit_logs_event_type_created_at", "event_type", "created_at"),
        Index("ix_audit_logs_call_id", "call_id"),
        Index("ix_audit_logs_conversation_id", "conversation_id"),
        Index("ix_audit_logs_patient_id", "patient_id"),
        Index("ix_audit_logs_appointment_id", "appointment_id"),
    )