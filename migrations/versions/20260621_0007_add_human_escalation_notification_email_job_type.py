"""add human escalation notification email job type

Revision ID: 20260621_0007
Revises: 20260621_0006
Create Date: 2026-06-21 00:07:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260621_0007"
down_revision: str | None = "20260621_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TYPE email_job_type ADD VALUE IF NOT EXISTS "
        "'human_escalation_notification'",
    )


def downgrade() -> None:
    pass
