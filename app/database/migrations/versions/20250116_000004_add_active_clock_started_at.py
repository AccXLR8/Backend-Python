"""Add active_clock_started_at column

Revision ID: 20250116_000004
Revises: 20250110_000003
Create Date: 2025-01-16 00:00:04.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20250116_000004"
down_revision: Union[str, None] = "20250110_000003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("games", sa.Column("active_clock_started_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("games", "active_clock_started_at")
