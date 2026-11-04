"""Retire the Crimson Impact skin bundle."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20261104_000020"
down_revision: str | None = "20261104_000019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE skin_bundles SET enabled = FALSE WHERE id = 'crimson-impact'")


def downgrade() -> None:
    op.execute("UPDATE skin_bundles SET enabled = TRUE WHERE id = 'crimson-impact'")
