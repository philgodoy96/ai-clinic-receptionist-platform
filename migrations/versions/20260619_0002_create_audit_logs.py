"""create audit logs table

Revision ID: 20260619_0002
Revises: 20260619_0001
Create Date: 2026-06-19 00:02:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260619_0002"
down_revision: str | None = "20260619_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

audit_event_type_enum = postgresql.ENUM(
    "appointment_hold_created",
    "appointment_hold_failed",
    "appointment_booking_confirmed",
    "appointment_booking_failed",
    name="audit_event_type",
    create_type=False,
)
audit_event_outcome_enum = postgresql.ENUM(
    "success",
    "failure",
    name="audit_event_outcome",
    create_type=False,
)
audit_actor_type_enum = postgresql.ENUM(
    "system",
    "patient",
    "retell",
    "chat",
    "api",
    name="audit_actor_type",
    create_type=False,
)


def upgrade() -> None:
    audit_event_type_enum.create(op.get_bind(), checkfirst=True)
    audit_event_outcome_enum.create(op.get_bind(), checkfirst=True)
    audit_actor_type_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_type", audit_event_type_enum, nullable=False),
        sa.Column("outcome", audit_event_outcome_enum, nullable=False),
        sa.Column("actor_type", audit_actor_type_enum, nullable=False),
        sa.Column("actor_id", sa.String(length=160), nullable=True),
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.Column("request_id", sa.String(length=120), nullable=True),
        sa.Column("call_id", sa.String(length=120), nullable=True),
        sa.Column("conversation_id", sa.String(length=120), nullable=True),
        sa.Column("patient_id", sa.Uuid(), nullable=True),
        sa.Column("appointment_id", sa.Uuid(), nullable=True),
        sa.Column("availability_slot_id", sa.Uuid(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_audit_logs_created_at_id",
        "audit_logs",
        ["created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_audit_logs_event_type_created_at",
        "audit_logs",
        ["event_type", "created_at"],
        unique=False,
    )
    op.create_index("ix_audit_logs_call_id", "audit_logs", ["call_id"], unique=False)
    op.create_index(
        "ix_audit_logs_conversation_id",
        "audit_logs",
        ["conversation_id"],
        unique=False,
    )
    op.create_index("ix_audit_logs_patient_id", "audit_logs", ["patient_id"], unique=False)
    op.create_index(
        "ix_audit_logs_appointment_id",
        "audit_logs",
        ["appointment_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_audit_logs_appointment_id", table_name="audit_logs")
    op.drop_index("ix_audit_logs_patient_id", table_name="audit_logs")
    op.drop_index("ix_audit_logs_conversation_id", table_name="audit_logs")
    op.drop_index("ix_audit_logs_call_id", table_name="audit_logs")
    op.drop_index("ix_audit_logs_event_type_created_at", table_name="audit_logs")
    op.drop_index("ix_audit_logs_created_at_id", table_name="audit_logs")
    op.drop_table("audit_logs")
    audit_actor_type_enum.drop(op.get_bind(), checkfirst=True)
    audit_event_outcome_enum.drop(op.get_bind(), checkfirst=True)
    audit_event_type_enum.drop(op.get_bind(), checkfirst=True)
