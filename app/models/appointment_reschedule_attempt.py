from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class AppointmentRescheduleAttempt(Base):
    __tablename__ = "appointment_reschedule_attempts"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    appointment_id: Mapped[UUID] = mapped_column(
        ForeignKey("appointments.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    new_appointment_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("appointments.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "idempotency_key",
            name="uq_appointment_reschedule_attempts_idempotency_key",
        ),
    )
