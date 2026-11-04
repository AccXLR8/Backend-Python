"""Publish the updated Void Prism and Circuit Pulse asset versions."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20261104_000021"
down_revision: str | None = "20261104_000020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE orb_skins SET asset_version = '2' "
        "WHERE id IN ('void-prism', 'neon-circuit')"
    )
    op.execute(
        "UPDATE board_skins "
        "SET description = 'A deep-space grid threaded with violet light and luminous cyan prism nodes.' "
        "WHERE id = 'void-matrix'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE orb_skins SET asset_version = '1' "
        "WHERE id IN ('void-prism', 'neon-circuit')"
    )
    op.execute(
        "UPDATE board_skins "
        "SET description = 'A cosmic grid with deep space color and violet light.' "
        "WHERE id = 'void-matrix'"
    )
