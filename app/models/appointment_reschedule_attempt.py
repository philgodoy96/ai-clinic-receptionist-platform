from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.domain.appointment_rescheduling_enums import AppointmentRescheduleAttemptStatus


def appointment_reschedule_attempt_status_values(
    enum_class: type[AppointmentRescheduleAttemptStatus],
) -> list[str]:
    return [item.value for item in enum_class]


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
    status: Mapped[AppointmentRescheduleAttemptStatus] = mapped_column(
        SAEnum(
            AppointmentRescheduleAttemptStatus,
            values_callable=appointment_reschedule_attempt_status_values,
            name="appointment_reschedule_attempt_status",
        ),
        nullable=False,
        default=AppointmentRescheduleAttemptStatus.PENDING,
    )
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
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
        UniqueConstraint(
            "idempotency_key",
            name="uq_appointment_reschedule_attempts_idempotency_key",
        ),
    )
