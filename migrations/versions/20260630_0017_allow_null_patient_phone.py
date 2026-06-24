"""allow null patient phone for voice intake without invented contact data

Revision ID: 20260630_0017
Revises: 20260629_0016
Create Date: 2026-06-30 00:17:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260630_0017"
down_revision: str | None = "20260629_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "patients",
        "phone_number",
        existing_type=sa.String(length=40),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "patients",
        "phone_number",
        existing_type=sa.String(length=40),
        nullable=False,
    )
