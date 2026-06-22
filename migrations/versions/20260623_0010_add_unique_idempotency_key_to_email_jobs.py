"""add unique idempotency key to email jobs

Revision ID: 20260623_0010
Revises: 20260622_0009
Create Date: 2026-06-23 00:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260623_0010"
down_revision: str | None = "20260622_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE email_jobs
        SET idempotency_key = 'appointment_confirmation:' || appointment_id::text
        WHERE job_type = 'appointment_confirmation'
          AND idempotency_key IS NULL
          AND appointment_id IN (
              SELECT appointment_id
              FROM email_jobs
              WHERE job_type = 'appointment_confirmation'
              GROUP BY appointment_id
              HAVING COUNT(*) = 1
          )
        """,
    )

    op.drop_index("ix_email_jobs_idempotency_key", table_name="email_jobs")
    op.create_index(
        "uq_email_jobs_idempotency_key",
        "email_jobs",
        ["idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_email_jobs_idempotency_key", table_name="email_jobs")
    op.create_index(
        "ix_email_jobs_idempotency_key",
        "email_jobs",
        ["idempotency_key"],
        unique=False,
    )
