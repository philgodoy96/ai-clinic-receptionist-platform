"""create conversation tables

Revision ID: 20260619_0004
Revises: 20260619_0003
Create Date: 2026-06-19 00:04:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260619_0004"
down_revision: str | None = "20260619_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


CONVERSATION_CHANNEL_VALUES = (
    "chat",
    "retell_voice",
    "system",
)

CONVERSATION_STATUS_VALUES = (
    "active",
    "closed",
    "escalated",
    "abandoned",
)

CONVERSATION_MESSAGE_ROLE_VALUES = (
    "user",
    "assistant",
    "system",
    "tool",
)

conversation_channel_enum = postgresql.ENUM(
    *CONVERSATION_CHANNEL_VALUES,
    name="conversation_channel",
    create_type=False,
)
conversation_status_enum = postgresql.ENUM(
    *CONVERSATION_STATUS_VALUES,
    name="conversation_status",
    create_type=False,
)
conversation_message_role_enum = postgresql.ENUM(
    *CONVERSATION_MESSAGE_ROLE_VALUES,
    name="conversation_message_role",
    create_type=False,
)


def upgrade() -> None:
    conversation_channel_enum.create(op.get_bind(), checkfirst=True)
    conversation_status_enum.create(op.get_bind(), checkfirst=True)
    conversation_message_role_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "conversations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("channel", conversation_channel_enum, nullable=False),
        sa.Column("status", conversation_status_enum, nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=True),
        sa.Column("appointment_id", sa.Uuid(), nullable=True),
        sa.Column("external_conversation_id", sa.String(length=120), nullable=True),
        sa.Column("call_id", sa.String(length=120), nullable=True),
        sa.Column("request_id", sa.String(length=120), nullable=True),
        sa.Column("correlation_id", sa.String(length=120), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "conversation_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_conversations_channel_status",
        "conversations",
        ["channel", "status"],
        unique=False,
    )
    op.create_index(
        "ix_conversations_patient_id",
        "conversations",
        ["patient_id"],
        unique=False,
    )
    op.create_index(
        "ix_conversations_appointment_id",
        "conversations",
        ["appointment_id"],
        unique=False,
    )
    op.create_index(
        "ix_conversations_call_id",
        "conversations",
        ["call_id"],
        unique=False,
    )
    op.create_index(
        "uq_conversations_channel_external_conversation_id",
        "conversations",
        ["channel", "external_conversation_id"],
        unique=True,
        postgresql_where=sa.text("external_conversation_id IS NOT NULL"),
    )

    op.create_table(
        "conversation_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("role", conversation_message_role_enum, nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("tool_name", sa.String(length=120), nullable=True),
        sa.Column("tool_call_id", sa.String(length=120), nullable=True),
        sa.Column(
            "message_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_conversation_messages_conversation_created_at",
        "conversation_messages",
        ["conversation_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_conversation_messages_role",
        "conversation_messages",
        ["role"],
        unique=False,
    )
    op.create_index(
        "ix_conversation_messages_tool_name",
        "conversation_messages",
        ["tool_name"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_conversation_messages_tool_name", table_name="conversation_messages")
    op.drop_index("ix_conversation_messages_role", table_name="conversation_messages")
    op.drop_index(
        "ix_conversation_messages_conversation_created_at",
        table_name="conversation_messages",
    )
    op.drop_table("conversation_messages")

    op.drop_index(
        "uq_conversations_channel_external_conversation_id",
        table_name="conversations",
    )
    op.drop_index("ix_conversations_call_id", table_name="conversations")
    op.drop_index("ix_conversations_appointment_id", table_name="conversations")
    op.drop_index("ix_conversations_patient_id", table_name="conversations")
    op.drop_index("ix_conversations_channel_status", table_name="conversations")
    op.drop_table("conversations")

    conversation_message_role_enum.drop(op.get_bind(), checkfirst=True)
    conversation_status_enum.drop(op.get_bind(), checkfirst=True)
    conversation_channel_enum.drop(op.get_bind(), checkfirst=True)
