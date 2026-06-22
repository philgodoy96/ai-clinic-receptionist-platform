"""create appointment reschedule attempts table

Revision ID: 20260628_0015
Revises: 20260627_0014
Create Date: 2026-06-28 00:15:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260628_0015"
down_revision: str | None = "20260627_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "appointment_reschedule_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("appointment_id", sa.Uuid(), nullable=False),
        sa.Column("new_appointment_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["appointment_id"],
            ["appointments.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["new_appointment_id"],
            ["appointments.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_appointment_reschedule_attempts_idempotency_key",
        ),
    )
    op.create_index(
        "ix_appointment_reschedule_attempts_appointment_id",
        "appointment_reschedule_attempts",
        ["appointment_id"],
        unique=False,
    )
    op.create_index(
        "ix_appointment_reschedule_attempts_new_appointment_id",
        "appointment_reschedule_attempts",
        ["new_appointment_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_appointment_reschedule_attempts_new_appointment_id",
        table_name="appointment_reschedule_attempts",
    )
    op.drop_index(
        "ix_appointment_reschedule_attempts_appointment_id",
        table_name="appointment_reschedule_attempts",
    )
    op.drop_table("appointment_reschedule_attempts")
