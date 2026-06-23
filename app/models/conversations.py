from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base
from app.domain.conversations.enums import (
    ConversationChannel,
    ConversationMessageRole,
    ConversationStatus,
)


def conversation_channel_values(enum_class: type[ConversationChannel]) -> list[str]:
    return [item.value for item in enum_class]


def conversation_status_values(enum_class: type[ConversationStatus]) -> list[str]:
    return [item.value for item in enum_class]


def conversation_message_role_values(
    enum_class: type[ConversationMessageRole],
) -> list[str]:
    return [item.value for item in enum_class]


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    channel: Mapped[ConversationChannel] = mapped_column(
        SAEnum(
            ConversationChannel,
            values_callable=conversation_channel_values,
            name="conversation_channel",
        ),
        nullable=False,
    )
    status: Mapped[ConversationStatus] = mapped_column(
        SAEnum(
            ConversationStatus,
            values_callable=conversation_status_values,
            name="conversation_status",
        ),
        nullable=False,
        default=ConversationStatus.ACTIVE,
    )
    patient_id: Mapped[UUID | None] = mapped_column(nullable=True)
    appointment_id: Mapped[UUID | None] = mapped_column(nullable=True)
    external_conversation_id: Mapped[str | None] = mapped_column(
        String(120),
        nullable=True,
    )
    call_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    conversation_metadata: Mapped[dict[str, Any]] = mapped_column(
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

    messages: Mapped[list[ConversationMessage]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_conversations_channel_status", "channel", "status"),
        Index("ix_conversations_patient_id", "patient_id"),
        Index("ix_conversations_appointment_id", "appointment_id"),
        Index("ix_conversations_call_id", "call_id"),
        Index(
            "uq_conversations_channel_external_conversation_id",
            "channel",
            "external_conversation_id",
            unique=True,
            postgresql_where=external_conversation_id.is_not(None),
        ),
    )


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[ConversationMessageRole] = mapped_column(
        SAEnum(
            ConversationMessageRole,
            values_callable=conversation_message_role_values,
            name="conversation_message_role",
        ),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tool_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    message_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    conversation: Mapped[Conversation] = relationship(back_populates="messages")

    __table_args__ = (
        Index("ix_conversation_messages_conversation_created_at", "conversation_id", "created_at"),
        Index("ix_conversation_messages_role", "role"),
        Index("ix_conversation_messages_tool_name", "tool_name"),
    )
