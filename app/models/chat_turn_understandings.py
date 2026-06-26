from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class ChatTurnUnderstanding(Base):
    __tablename__ = "chat_turn_understandings"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "conversations.id",
            name="fk_chat_turn_understandings_conversation_id_conversations",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    user_message_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "conversation_messages.id",
            name="fk_chat_turn_understandings_user_message_id_conversation_messages",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    assistant_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "conversation_messages.id",
            name="fk_chat_turn_understandings_assistant_message_id_conversation_messages",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    request_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    primary_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fallback_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    used_fallback_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(120), nullable=True)
    schema_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    schema_version: Mapped[str | None] = mapped_column(String(120), nullable=True)
    llm_intent: Mapped[str | None] = mapped_column(String(120), nullable=True)
    llm_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    llm_urgency: Mapped[str | None] = mapped_column(String(120), nullable=True)
    requires_human: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    safety_flags: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False,
        default=dict,
    )
    extracted_fields: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False,
        default=dict,
    )
    normalized_fields: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False,
        default=dict,
    )
    applied_fields: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False,
        default=dict,
    )
    rejected_fields: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False,
        default=dict,
    )
    validation_outcome: Mapped[str | None] = mapped_column(String(120), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_cost_micros: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempt_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    primary_attempt_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fallback_attempt_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    used_fallback: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    used_repair: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    failure_category: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "user_message_id",
            name="uq_chat_turn_understandings_user_message_id",
        ),
        Index(
            "ix_chat_turn_understandings_conversation_created_at",
            "conversation_id",
            "created_at",
        ),
        Index("ix_chat_turn_understandings_user_message_id", "user_message_id"),
        Index("ix_chat_turn_understandings_assistant_message_id", "assistant_message_id"),
        Index(
            "ix_chat_turn_understandings_prompt_version_created_at",
            "prompt_version",
            "created_at",
        ),
        Index("ix_chat_turn_understandings_failure_category", "failure_category"),
    )
