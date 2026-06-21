"""create human escalations table

Revision ID: 20260620_0005
Revises: 20260619_0004
Create Date: 2026-06-20 00:05:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260620_0005"
down_revision: str | None = "20260619_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

HUMAN_ESCALATION_STATUS_VALUES = (
    "open",
    "acknowledged",
    "resolved",
    "cancelled",
)

HUMAN_ESCALATION_REASON_VALUES = (
    "user_requested_human",
    "medical_emergency",
    "repeated_fallback",
    "repeated_slot_filling_rejection",
    "repeated_low_confidence",
    "repeated_booking_conflict",
    "no_progress",
    "unknown",
)

HUMAN_ESCALATION_PRIORITY_VALUES = (
    "normal",
    "high",
    "urgent",
)

HUMAN_ESCALATION_SOURCE_VALUES = (
    "chat",
    "retell_voice",
    "system",
)

human_escalation_status_enum = postgresql.ENUM(
    *HUMAN_ESCALATION_STATUS_VALUES,
    name="human_escalation_status",
    create_type=False,
)
human_escalation_reason_enum = postgresql.ENUM(
    *HUMAN_ESCALATION_REASON_VALUES,
    name="human_escalation_reason",
    create_type=False,
)
human_escalation_priority_enum = postgresql.ENUM(
    *HUMAN_ESCALATION_PRIORITY_VALUES,
    name="human_escalation_priority",
    create_type=False,
)
human_escalation_source_enum = postgresql.ENUM(
    *HUMAN_ESCALATION_SOURCE_VALUES,
    name="human_escalation_source",
    create_type=False,
)


def upgrade() -> None:
    human_escalation_status_enum.create(op.get_bind(), checkfirst=True)
    human_escalation_reason_enum.create(op.get_bind(), checkfirst=True)
    human_escalation_priority_enum.create(op.get_bind(), checkfirst=True)
    human_escalation_source_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "human_escalations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=True),
        sa.Column("appointment_id", sa.Uuid(), nullable=True),
        sa.Column(
            "status",
            human_escalation_status_enum,
            nullable=False,
            server_default="open",
        ),
        sa.Column("reason", human_escalation_reason_enum, nullable=False),
        sa.Column(
            "priority",
            human_escalation_priority_enum,
            nullable=False,
            server_default="normal",
        ),
        sa.Column(
            "source",
            human_escalation_source_enum,
            nullable=False,
            server_default="chat",
        ),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column(
            "handoff_context",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("created_by", sa.String(length=160), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by", sa.String(length=160), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.String(length=160), nullable=True),
        sa.Column("resolution_notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patients.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["appointment_id"],
            ["appointments.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_human_escalations_status_created_at",
        "human_escalations",
        ["status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_human_escalations_conversation_id",
        "human_escalations",
        ["conversation_id"],
        unique=False,
    )
    op.create_index(
        "ix_human_escalations_patient_id",
        "human_escalations",
        ["patient_id"],
        unique=False,
    )
    op.create_index(
        "ix_human_escalations_appointment_id",
        "human_escalations",
        ["appointment_id"],
        unique=False,
    )
    op.create_index(
        "ix_human_escalations_reason_created_at",
        "human_escalations",
        ["reason", "created_at"],
        unique=False,
    )
    op.create_index(
        "uq_human_escalations_active_conversation_id",
        "human_escalations",
        ["conversation_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('open', 'acknowledged')"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_human_escalations_active_conversation_id",
        table_name="human_escalations",
    )
    op.drop_index(
        "ix_human_escalations_reason_created_at",
        table_name="human_escalations",
    )
    op.drop_index(
        "ix_human_escalations_appointment_id",
        table_name="human_escalations",
    )
    op.drop_index(
        "ix_human_escalations_patient_id",
        table_name="human_escalations",
    )
    op.drop_index(
        "ix_human_escalations_conversation_id",
        table_name="human_escalations",
    )
    op.drop_index(
        "ix_human_escalations_status_created_at",
        table_name="human_escalations",
    )
    op.drop_table("human_escalations")

    human_escalation_source_enum.drop(op.get_bind(), checkfirst=True)
    human_escalation_priority_enum.drop(op.get_bind(), checkfirst=True)
    human_escalation_reason_enum.drop(op.get_bind(), checkfirst=True)
    human_escalation_status_enum.drop(op.get_bind(), checkfirst=True)
