"""create voice call tables

Revision ID: 20260624_0011
Revises: 20260623_0010
Create Date: 2026-06-24 00:11:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260624_0011"
down_revision: str | None = "20260623_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

VOICE_CALL_STATUS_VALUES = (
    "created",
    "in_progress",
    "ended",
    "failed",
    "unknown",
)

NORMALIZED_VOICE_CALL_EVENT_TYPE_VALUES = (
    "call_started",
    "call_updated",
    "call_ended",
    "call_failed",
    "unknown",
)

voice_call_status_enum = postgresql.ENUM(
    *VOICE_CALL_STATUS_VALUES,
    name="voice_call_status",
    create_type=False,
)
normalized_voice_call_event_type_enum = postgresql.ENUM(
    *NORMALIZED_VOICE_CALL_EVENT_TYPE_VALUES,
    name="normalized_voice_call_event_type",
    create_type=False,
)


def upgrade() -> None:
    voice_call_status_enum.create(op.get_bind(), checkfirst=True)
    normalized_voice_call_event_type_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "voice_calls",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False, server_default="retell"),
        sa.Column("provider_call_id", sa.String(length=120), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("status", voice_call_status_enum, nullable=False),
        sa.Column("direction", sa.String(length=32), nullable=True),
        sa.Column("from_number_redacted", sa.String(length=32), nullable=True),
        sa.Column("to_number_redacted", sa.String(length=32), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "event_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "provider_call_id", name="uq_voice_calls_provider_call_id"),
    )
    op.create_index(
        "ix_voice_calls_status_created_at",
        "voice_calls",
        ["status", "created_at"],
        unique=False,
    )

    op.create_table(
        "voice_call_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("voice_call_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False, server_default="retell"),
        sa.Column("provider_call_id", sa.String(length=120), nullable=False),
        sa.Column("provider_event_id", sa.String(length=120), nullable=True),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("normalized_event_type", normalized_voice_call_event_type_enum, nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=True),
        sa.Column(
            "event_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["voice_call_id"],
            ["voice_calls.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_voice_call_events_idempotency_key"),
    )
    op.create_index(
        "ix_voice_call_events_voice_call_occurred_at",
        "voice_call_events",
        ["voice_call_id", "occurred_at"],
        unique=False,
    )
    op.create_index(
        "ix_voice_call_events_provider_call_id",
        "voice_call_events",
        ["provider", "provider_call_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_voice_call_events_provider_call_id", table_name="voice_call_events")
    op.drop_index(
        "ix_voice_call_events_voice_call_occurred_at",
        table_name="voice_call_events",
    )
    op.drop_table("voice_call_events")

    op.drop_index("ix_voice_calls_status_created_at", table_name="voice_calls")
    op.drop_table("voice_calls")

    normalized_voice_call_event_type_enum.drop(op.get_bind(), checkfirst=True)
    voice_call_status_enum.drop(op.get_bind(), checkfirst=True)
