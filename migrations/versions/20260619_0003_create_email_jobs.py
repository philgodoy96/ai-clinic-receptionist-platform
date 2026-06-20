"""create email jobs table

Revision ID: 20260619_0003
Revises: 20260619_0002
Create Date: 2026-06-19 00:03:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260619_0003"
down_revision: str | None = "20260619_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


EMAIL_JOB_TYPE_VALUES = (
    "appointment_confirmation",
)

EMAIL_JOB_STATUS_VALUES = (
    "pending",
    "processing",
    "sent",
    "failed",
    "dead_letter",
)


def upgrade() -> None:
    email_job_type = postgresql.ENUM(
        *EMAIL_JOB_TYPE_VALUES,
        name="email_job_type",
    )
    email_job_status = postgresql.ENUM(
        *EMAIL_JOB_STATUS_VALUES,
        name="email_job_status",
    )

    email_job_type.create(op.get_bind(), checkfirst=True)
    email_job_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "email_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "job_type",
            sa.Enum(*EMAIL_JOB_TYPE_VALUES, name="email_job_type"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(*EMAIL_JOB_STATUS_VALUES, name="email_job_status"),
            nullable=False,
        ),
        sa.Column("appointment_id", sa.Uuid(), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("recipient_email", sa.String(length=255), nullable=True),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("locked_by", sa.String(length=120), nullable=True),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_email_jobs_status_scheduled_for",
        "email_jobs",
        ["status", "scheduled_for"],
        unique=False,
    )
    op.create_index(
        "ix_email_jobs_locked_until",
        "email_jobs",
        ["locked_until"],
        unique=False,
    )
    op.create_index(
        "ix_email_jobs_appointment_id",
        "email_jobs",
        ["appointment_id"],
        unique=False,
    )
    op.create_index(
        "ix_email_jobs_patient_id",
        "email_jobs",
        ["patient_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_email_jobs_patient_id", table_name="email_jobs")
    op.drop_index("ix_email_jobs_appointment_id", table_name="email_jobs")
    op.drop_index("ix_email_jobs_locked_until", table_name="email_jobs")
    op.drop_index("ix_email_jobs_status_scheduled_for", table_name="email_jobs")
    op.drop_table("email_jobs")

    postgresql.ENUM(name="email_job_status").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="email_job_type").drop(op.get_bind(), checkfirst=True)