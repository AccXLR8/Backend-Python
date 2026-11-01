"""Add nationality to user records.

Revision ID: 20250203_000011
Revises: 20250202_000010
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20250203_000011"
down_revision: str | None = "20250202_000010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("nationality", sa.String(length=8), server_default="global", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("users", "nationality")
