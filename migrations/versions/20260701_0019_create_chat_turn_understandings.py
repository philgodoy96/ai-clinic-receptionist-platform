"""create chat turn understandings table

Revision ID: 20260701_0019
Revises: 20260630_0018
Create Date: 2026-07-01 00:19:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260701_0019"
down_revision: str | None = "20260630_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chat_turn_understandings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("user_message_id", sa.Uuid(), nullable=False),
        sa.Column("assistant_message_id", sa.Uuid(), nullable=True),
        sa.Column("request_id", sa.String(length=120), nullable=True),
        sa.Column("correlation_id", sa.String(length=120), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=True),
        sa.Column("model", sa.String(length=120), nullable=True),
        sa.Column("primary_provider", sa.String(length=64), nullable=True),
        sa.Column("fallback_provider", sa.String(length=64), nullable=True),
        sa.Column("used_fallback_provider", sa.String(length=64), nullable=True),
        sa.Column("prompt_name", sa.String(length=120), nullable=True),
        sa.Column("prompt_version", sa.String(length=120), nullable=True),
        sa.Column("schema_name", sa.String(length=120), nullable=True),
        sa.Column("schema_version", sa.String(length=120), nullable=True),
        sa.Column("llm_intent", sa.String(length=120), nullable=True),
        sa.Column("llm_confidence", sa.Float(), nullable=True),
        sa.Column("llm_urgency", sa.String(length=120), nullable=True),
        sa.Column("requires_human", sa.Boolean(), nullable=True),
        sa.Column(
            "safety_flags",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "extracted_fields",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "normalized_fields",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "applied_fields",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "rejected_fields",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("validation_outcome", sa.String(length=120), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("estimated_cost_micros", sa.Integer(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=True),
        sa.Column("primary_attempt_count", sa.Integer(), nullable=True),
        sa.Column("fallback_attempt_count", sa.Integer(), nullable=True),
        sa.Column("used_fallback", sa.Boolean(), nullable=True),
        sa.Column("used_repair", sa.Boolean(), nullable=True),
        sa.Column("failure_reason", sa.String(length=255), nullable=True),
        sa.Column("failure_category", sa.String(length=120), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name="fk_ctu_conversation",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_message_id"],
            ["conversation_messages.id"],
            name="fk_ctu_user_msg",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["assistant_message_id"],
            ["conversation_messages.id"],
            name="fk_ctu_assistant_msg",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ctu"),
        sa.UniqueConstraint(
            "user_message_id",
            name="uq_ctu_user_msg",
        ),
    )
    op.create_index(
        "ix_ctu_conversation_created_at",
        "chat_turn_understandings",
        ["conversation_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_ctu_user_msg_id",
        "chat_turn_understandings",
        ["user_message_id"],
        unique=False,
    )
    op.create_index(
        "ix_ctu_assistant_msg_id",
        "chat_turn_understandings",
        ["assistant_message_id"],
        unique=False,
    )
    op.create_index(
        "ix_ctu_prompt_version_created_at",
        "chat_turn_understandings",
        ["prompt_version", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_ctu_failure_category",
        "chat_turn_understandings",
        ["failure_category"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ctu_failure_category",
        table_name="chat_turn_understandings",
    )
    op.drop_index(
        "ix_ctu_prompt_version_created_at",
        table_name="chat_turn_understandings",
    )
    op.drop_index(
        "ix_ctu_assistant_msg_id",
        table_name="chat_turn_understandings",
    )
    op.drop_index(
        "ix_ctu_user_msg_id",
        table_name="chat_turn_understandings",
    )
    op.drop_index(
        "ix_ctu_conversation_created_at",
        table_name="chat_turn_understandings",
    )
    op.drop_table("chat_turn_understandings")
