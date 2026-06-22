from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base
from app.domain.voice_calls.enums import NormalizedVoiceCallEventType, VoiceCallStatus

if TYPE_CHECKING:
    from app.models.conversations import Conversation


def voice_call_status_values(enum_class: type[VoiceCallStatus]) -> list[str]:
    return [item.value for item in enum_class]


def normalized_voice_call_event_type_values(
    enum_class: type[NormalizedVoiceCallEventType],
) -> list[str]:
    return [item.value for item in enum_class]


class VoiceCall(Base):
    __tablename__ = "voice_calls"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="retell")
    provider_call_id: Mapped[str] = mapped_column(String(120), nullable=False)
    conversation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[VoiceCallStatus] = mapped_column(
        SAEnum(
            VoiceCallStatus,
            values_callable=voice_call_status_values,
            name="voice_call_status",
        ),
        nullable=False,
        default=VoiceCallStatus.CREATED,
    )
    direction: Mapped[str | None] = mapped_column(String(32), nullable=True)
    from_number_redacted: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_number_redacted: Mapped[str | None] = mapped_column(String(32), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    event_metadata: Mapped[dict[str, Any]] = mapped_column(
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

    events: Mapped[list[VoiceCallEvent]] = relationship(
        back_populates="voice_call",
        cascade="all, delete-orphan",
    )
    conversation: Mapped[Conversation | None] = relationship(
        "Conversation",
    )

    __table_args__ = (
        UniqueConstraint("provider", "provider_call_id", name="uq_voice_calls_provider_call_id"),
        Index("ix_voice_calls_conversation_id", "conversation_id"),
        Index("ix_voice_calls_status_created_at", "status", "created_at"),
    )


class VoiceCallEvent(Base):
    __tablename__ = "voice_call_events"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    voice_call_id: Mapped[UUID] = mapped_column(
        ForeignKey("voice_calls.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="retell")
    provider_call_id: Mapped[str] = mapped_column(String(120), nullable=False)
    provider_event_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    normalized_event_type: Mapped[NormalizedVoiceCallEventType | None] = mapped_column(
        SAEnum(
            NormalizedVoiceCallEventType,
            values_callable=normalized_voice_call_event_type_values,
            name="normalized_voice_call_event_type",
        ),
        nullable=True,
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sequence_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    event_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False,
        default=dict,
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    voice_call: Mapped[VoiceCall] = relationship(back_populates="events")

    __table_args__ = (
        Index("ix_voice_call_events_voice_call_occurred_at", "voice_call_id", "occurred_at"),
        Index("ix_voice_call_events_provider_call_id", "provider", "provider_call_id"),
    )
