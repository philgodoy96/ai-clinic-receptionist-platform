"""create voice booking attempts table

Revision ID: 20260626_0013
Revises: 20260625_0012
Create Date: 2026-06-26 00:13:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260626_0013"
down_revision: str | None = "20260625_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

voice_booking_attempt_status_enum = postgresql.ENUM(
    "pending",
    "succeeded",
    "failed",
    name="voice_booking_attempt_status",
    create_type=False,
)


def upgrade() -> None:
    voice_booking_attempt_status_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "voice_booking_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("provider_call_id", sa.String(length=120), nullable=False),
        sa.Column("tool_call_id", sa.String(length=120), nullable=True),
        sa.Column("voice_call_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("hold_id", sa.String(length=120), nullable=True),
        sa.Column("availability_slot_id", sa.Uuid(), nullable=True),
        sa.Column("patient_id", sa.Uuid(), nullable=True),
        sa.Column("appointment_id", sa.Uuid(), nullable=True),
        sa.Column("status", voice_booking_attempt_status_enum, nullable=False),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column(
            "attempt_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["voice_call_id"], ["voice_calls.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_voice_booking_attempts_idempotency_key"),
    )
    op.create_index(
        "ix_voice_booking_attempts_voice_call_id",
        "voice_booking_attempts",
        ["voice_call_id"],
        unique=False,
    )
    op.create_index(
        "ix_voice_booking_attempts_conversation_id",
        "voice_booking_attempts",
        ["conversation_id"],
        unique=False,
    )
    op.create_index(
        "ix_voice_booking_attempts_appointment_id",
        "voice_booking_attempts",
        ["appointment_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_voice_booking_attempts_appointment_id", table_name="voice_booking_attempts")
    op.drop_index("ix_voice_booking_attempts_conversation_id", table_name="voice_booking_attempts")
    op.drop_index("ix_voice_booking_attempts_voice_call_id", table_name="voice_booking_attempts")
    op.drop_table("voice_booking_attempts")
    voice_booking_attempt_status_enum.drop(op.get_bind(), checkfirst=True)
