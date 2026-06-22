from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db.base_class import Base
from app.domain.voice_booking_enums import VoiceBookingAttemptStatus


def voice_booking_attempt_status_values(
    enum_class: type[VoiceBookingAttemptStatus],
) -> list[str]:
    return [item.value for item in enum_class]


class VoiceBookingAttempt(Base):
    __tablename__ = "voice_booking_attempts"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_call_id: Mapped[str] = mapped_column(String(120), nullable=False)
    tool_call_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    voice_call_id: Mapped[UUID] = mapped_column(
        ForeignKey("voice_calls.id", ondelete="CASCADE"),
        nullable=False,
    )
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    hold_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    availability_slot_id: Mapped[UUID | None] = mapped_column(nullable=True)
    patient_id: Mapped[UUID | None] = mapped_column(nullable=True)
    appointment_id: Mapped[UUID | None] = mapped_column(nullable=True)
    status: Mapped[VoiceBookingAttemptStatus] = mapped_column(
        SAEnum(
            VoiceBookingAttemptStatus,
            values_callable=voice_booking_attempt_status_values,
            name="voice_booking_attempt_status",
        ),
        nullable=False,
        default=VoiceBookingAttemptStatus.PENDING,
    )
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    attempt_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False,
        default=dict,
    )
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
        UniqueConstraint("idempotency_key", name="uq_voice_booking_attempts_idempotency_key"),
        Index("ix_voice_booking_attempts_voice_call_id", "voice_call_id"),
        Index("ix_voice_booking_attempts_conversation_id", "conversation_id"),
        Index("ix_voice_booking_attempts_appointment_id", "appointment_id"),
    )
