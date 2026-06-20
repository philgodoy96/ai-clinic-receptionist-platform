from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.domain.jobs.enums import EmailJobStatus, EmailJobType


def email_job_type_values(enum_class: type[EmailJobType]) -> list[str]:
    return [item.value for item in enum_class]


def email_job_status_values(enum_class: type[EmailJobStatus]) -> list[str]:
    return [item.value for item in enum_class]


class EmailJob(Base):
    __tablename__ = "email_jobs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    job_type: Mapped[EmailJobType] = mapped_column(
        SAEnum(
            EmailJobType,
            values_callable=email_job_type_values,
            name="email_job_type",
        ),
        nullable=False,
    )
    status: Mapped[EmailJobStatus] = mapped_column(
        SAEnum(
            EmailJobStatus,
            values_callable=email_job_status_values,
            name="email_job_status",
        ),
        nullable=False,
        default=EmailJobStatus.PENDING,
    )
    appointment_id: Mapped[UUID] = mapped_column(nullable=False)
    patient_id: Mapped[UUID] = mapped_column(nullable=False)
    recipient_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    locked_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    scheduled_for: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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
        Index("ix_email_jobs_status_scheduled_for", "status", "scheduled_for"),
        Index("ix_email_jobs_locked_until", "locked_until"),
        Index("ix_email_jobs_appointment_id", "appointment_id"),
        Index("ix_email_jobs_patient_id", "patient_id"),
    )