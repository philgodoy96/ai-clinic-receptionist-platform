"""add human escalation assignment fields

Revision ID: 20260621_0008
Revises: 20260621_0007
Create Date: 2026-06-21 00:08:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260621_0008"
down_revision: str | None = "20260621_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "human_escalations",
        sa.Column("assigned_to", sa.String(length=160), nullable=True),
    )
    op.add_column(
        "human_escalations",
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "human_escalations",
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_human_escalations_assigned_to",
        "human_escalations",
        ["assigned_to"],
        unique=False,
    )
    op.create_index(
        "ix_human_escalations_due_at",
        "human_escalations",
        ["due_at"],
        unique=False,
    )
    op.create_index(
        "ix_human_escalations_status_due_at",
        "human_escalations",
        ["status", "due_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_human_escalations_status_due_at",
        table_name="human_escalations",
    )
    op.drop_index(
        "ix_human_escalations_due_at",
        table_name="human_escalations",
    )
    op.drop_index(
        "ix_human_escalations_assigned_to",
        table_name="human_escalations",
    )
    op.drop_column("human_escalations", "due_at")
    op.drop_column("human_escalations", "assigned_at")
    op.drop_column("human_escalations", "assigned_to")
