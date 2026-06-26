"""add human escalation handoff_context column

Revision ID: 20260621_0006
Revises: 20260620_0005
Create Date: 2026-06-21 00:06:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260621_0006"
down_revision: str | None = "20260620_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_handoff_context_column() -> bool:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("human_escalations")}
    return "handoff_context" in columns


def upgrade() -> None:
    # Older databases created human_escalations without handoff_context, while the
    # current 0005 migration already includes it. Guard against duplicate columns so
    # this migration is idempotent on both fresh and pre-existing schemas.
    if _has_handoff_context_column():
        return

    op.add_column(
        "human_escalations",
        sa.Column(
            "handoff_context",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    if not _has_handoff_context_column():
        return

    op.drop_column("human_escalations", "handoff_context")
