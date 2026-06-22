"""add reschedule attempt status fields

Revision ID: 20260629_0016
Revises: 20260628_0015
Create Date: 2026-06-29 00:16:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260629_0016"
down_revision: str | None = "20260628_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

appointment_reschedule_attempt_status_enum = postgresql.ENUM(
    "pending",
    "succeeded",
    "rejected",
    "failed",
    name="appointment_reschedule_attempt_status",
    create_type=False,
)


def upgrade() -> None:
    appointment_reschedule_attempt_status_enum.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "appointment_reschedule_attempts",
        sa.Column(
            "status",
            appointment_reschedule_attempt_status_enum,
            nullable=False,
            server_default="pending",
        ),
    )
    op.add_column(
        "appointment_reschedule_attempts",
        sa.Column("error_code", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "appointment_reschedule_attempts",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.alter_column("appointment_reschedule_attempts", "status", server_default=None)


def downgrade() -> None:
    op.drop_column("appointment_reschedule_attempts", "updated_at")
    op.drop_column("appointment_reschedule_attempts", "error_code")
    op.drop_column("appointment_reschedule_attempts", "status")
    appointment_reschedule_attempt_status_enum.drop(op.get_bind(), checkfirst=True)
