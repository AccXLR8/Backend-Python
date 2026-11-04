"""Refresh Ocean Glass assets and board catalog metadata."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261104_000023"
down_revision: str | None = "20261104_000022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text(
        "UPDATE board_skins SET name = 'Ocean Glass Grid', rarity = 'rare', "
        "palette = 'ocean', description = 'A luminous cyan glass grid with flowing water caustics.', "
        "enabled = TRUE WHERE id = 'ocean-grid'"
    ))
    bind.execute(sa.text(
        "UPDATE skin_bundles SET board_skin_id = 'ocean-grid' WHERE id = 'ocean-glass'"
    ))
    bind.execute(sa.text(
        "UPDATE orb_skins SET asset_version = '2' WHERE id = 'cool-blue-glass'"
    ))


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text(
        "UPDATE board_skins SET name = 'Ocean Grid', rarity = 'rare', palette = 'ocean', "
        "description = 'A cool blue surface with subtle water-like patterns.', enabled = TRUE "
        "WHERE id = 'ocean-grid'"
    ))
    bind.execute(sa.text(
        "UPDATE orb_skins SET asset_version = '1' WHERE id = 'cool-blue-glass'"
    ))
