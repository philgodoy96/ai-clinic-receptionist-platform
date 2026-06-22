"""create appointment cancellation attempts table

Revision ID: 20260627_0014
Revises: 20260626_0013
Create Date: 2026-06-27 00:14:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260627_0014"
down_revision: str | None = "20260626_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "appointment_cancellation_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("appointment_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["appointment_id"],
            ["appointments.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_appointment_cancellation_attempts_idempotency_key",
        ),
    )
    op.create_index(
        "ix_appointment_cancellation_attempts_appointment_id",
        "appointment_cancellation_attempts",
        ["appointment_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_appointment_cancellation_attempts_appointment_id",
        table_name="appointment_cancellation_attempts",
    )
    op.drop_table("appointment_cancellation_attempts")
