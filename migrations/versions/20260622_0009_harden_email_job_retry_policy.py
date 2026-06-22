"""harden email job retry policy

Revision ID: 20260622_0009
Revises: 20260621_0008
Create Date: 2026-06-22 00:09:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260622_0009"
down_revision: str | None = "20260621_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("email_jobs", "attempts", new_column_name="attempt_count")
    op.alter_column(
        "email_jobs",
        "scheduled_for",
        new_column_name="next_attempt_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=True,
    )

    op.add_column(
        "email_jobs",
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "email_jobs",
        sa.Column("provider_message_id", sa.String(length=255), nullable=True),
    )

    op.execute(
        """
        UPDATE email_jobs
        SET idempotency_key = payload->>'idempotency_key'
        WHERE payload ? 'idempotency_key'
          AND payload->>'idempotency_key' IS NOT NULL
        """,
    )
    op.execute(
        """
        UPDATE email_jobs
        SET status = 'failed'
        WHERE status = 'dead_letter'
        """,
    )

    op.drop_index("ix_email_jobs_status_scheduled_for", table_name="email_jobs")
    op.create_index(
        "ix_email_jobs_status_next_attempt_at",
        "email_jobs",
        ["status", "next_attempt_at"],
        unique=False,
    )
    op.create_index(
        "ix_email_jobs_idempotency_key",
        "email_jobs",
        ["idempotency_key"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_email_jobs_idempotency_key", table_name="email_jobs")
    op.drop_index("ix_email_jobs_status_next_attempt_at", table_name="email_jobs")
    op.create_index(
        "ix_email_jobs_status_scheduled_for",
        "email_jobs",
        ["status", "next_attempt_at"],
        unique=False,
    )

    op.drop_column("email_jobs", "provider_message_id")
    op.drop_column("email_jobs", "idempotency_key")

    op.execute(
        """
        UPDATE email_jobs
        SET next_attempt_at = COALESCE(next_attempt_at, created_at)
        WHERE next_attempt_at IS NULL
        """,
    )
    op.alter_column(
        "email_jobs",
        "next_attempt_at",
        new_column_name="scheduled_for",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
    )
    op.alter_column("email_jobs", "attempt_count", new_column_name="attempts")
