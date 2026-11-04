"""Give Cosmic Void its nebula board and asset set, separate from Void Prism."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261104_000022"
down_revision: str | None = "20261104_000021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text(
        "INSERT INTO board_skins (id, name, rarity, palette, description, enabled) "
        "VALUES ('cosmic-grid', 'Cosmic Grid', 'legendary', 'cosmic', "
        "'A violet nebula grid scattered with bright cosmic stars.', TRUE) "
        "ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, rarity = EXCLUDED.rarity, "
        "palette = EXCLUDED.palette, description = EXCLUDED.description, enabled = TRUE"
    ))
    bind.execute(sa.text(
        "UPDATE skin_bundles SET board_skin_id = 'cosmic-grid' WHERE id = 'void-cosmic'"
    ))
    bind.execute(sa.text(
        "INSERT INTO user_board_skins (user_id, skin_id) "
        "SELECT user_id, 'cosmic-grid' FROM user_orb_skins WHERE skin_id = 'cosmic-core' "
        "ON CONFLICT DO NOTHING"
    ))
    bind.execute(sa.text(
        "UPDATE user_board_skin_selections AS board_selection "
        "SET skin_id = 'cosmic-grid' "
        "FROM user_orb_skin_selections AS orb_selection "
        "WHERE board_selection.user_id = orb_selection.user_id "
        "AND board_selection.skin_id = 'void-matrix' "
        "AND orb_selection.skin_id = 'cosmic-core'"
    ))
    bind.execute(sa.text(
        "UPDATE orb_skins SET asset_version = '2' WHERE id = 'cosmic-core'"
    ))


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text(
        "UPDATE skin_bundles SET board_skin_id = 'void-matrix' WHERE id = 'void-cosmic'"
    ))
    bind.execute(sa.text(
        "UPDATE user_board_skin_selections AS board_selection "
        "SET skin_id = 'void-matrix' "
        "FROM user_orb_skin_selections AS orb_selection "
        "WHERE board_selection.user_id = orb_selection.user_id "
        "AND board_selection.skin_id = 'cosmic-grid' "
        "AND orb_selection.skin_id = 'cosmic-core'"
    ))
    bind.execute(sa.text(
        "UPDATE orb_skins SET asset_version = '1' WHERE id = 'cosmic-core'"
    ))
    # Keep the board and its grants intact so rollback never erases player inventory.
