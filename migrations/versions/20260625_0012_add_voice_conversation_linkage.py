"""add voice conversation linkage

Revision ID: 20260625_0012
Revises: 20260624_0011
Create Date: 2026-06-25 00:12:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260625_0012"
down_revision: str | None = "20260624_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE conversation_channel ADD VALUE IF NOT EXISTS 'voice'")
    op.create_index(
        "ix_voice_calls_conversation_id",
        "voice_calls",
        ["conversation_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_voice_calls_conversation_id", table_name="voice_calls")
