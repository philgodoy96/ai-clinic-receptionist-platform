"""add appointment lifecycle audit event types

Revision ID: 20260630_0018
Revises: 20260630_0017
Create Date: 2026-06-30 00:18:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260630_0018"
down_revision: str | None = "20260630_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TYPE audit_event_type ADD VALUE IF NOT EXISTS "
        "'appointment_cancellation_confirmed'",
    )
    op.execute(
        "ALTER TYPE audit_event_type ADD VALUE IF NOT EXISTS "
        "'appointment_cancellation_failed'",
    )
    op.execute(
        "ALTER TYPE audit_event_type ADD VALUE IF NOT EXISTS "
        "'appointment_reschedule_requested'",
    )
    op.execute(
        "ALTER TYPE audit_event_type ADD VALUE IF NOT EXISTS "
        "'appointment_reschedule_succeeded'",
    )
    op.execute(
        "ALTER TYPE audit_event_type ADD VALUE IF NOT EXISTS "
        "'appointment_reschedule_rejected'",
    )
    op.execute(
        "ALTER TYPE audit_event_type ADD VALUE IF NOT EXISTS "
        "'appointment_reschedule_duplicate'",
    )


def downgrade() -> None:
    pass
