"""Add cache version for orb skin assets.

Revision ID: 20250204_000012
Revises: 20250203_000011
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20250204_000012"
down_revision: str | None = "20250203_000011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "orb_skins",
        sa.Column("asset_version", sa.String(length=64), server_default="1", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("orb_skins", "asset_version")
