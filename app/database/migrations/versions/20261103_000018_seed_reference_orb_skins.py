"""Seed the reference orb-skin collection and draft Gem prices."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261103_000018"
down_revision: str | None = "20261103_000017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Gem prices are initial balancing values and can be adjusted in the catalog
# without changing ownership or the purchase API.
SKINS = (
    ("classic", "Classic Orb", "common", None),
    ("cosmic-core", "Cosmic Core", "legendary", 4500),
    ("crimson-glass", "Crimson Glass", "epic", 2500),
    ("void-prism", "Void Prism", "epic", 2500),
    ("neon-circuit", "Neon Circuit", "epic", 2500),
    ("cool-blue-glass", "Cool Blue Glass", "rare", 1200),
    ("frosted-core", "Frosted Core", "rare", 1200),
    ("electric-halo", "Electric Halo", "rare", 1200),
    ("matte-pearl", "Matte Pearl", "common", 500),
    ("graphite", "Graphite", "common", 500),
    ("ivory-glow", "Ivory Glow", "common", 500),
)


def upgrade() -> None:
    bind = op.get_bind()
    upsert_skin = sa.text(
        """
        INSERT INTO orb_skins (
            id, name, asset_key, preview_asset_key, asset_version,
            rarity, price_gems, price_amount_minor, price_currency, enabled
        ) VALUES (
            :id, :name, :asset_key, :preview_asset_key, '1',
            :rarity, :price_gems, NULL, 'INR', TRUE
        )
        ON CONFLICT (id) DO UPDATE SET
            name = EXCLUDED.name,
            asset_key = EXCLUDED.asset_key,
            preview_asset_key = EXCLUDED.preview_asset_key,
            asset_version = EXCLUDED.asset_version,
            rarity = EXCLUDED.rarity,
            price_gems = EXCLUDED.price_gems,
            price_amount_minor = NULL,
            price_currency = 'INR',
            enabled = TRUE
        """
    )

    for skin_id, name, rarity, price_gems in SKINS:
        bind.execute(upsert_skin, {
            "id": skin_id,
            "name": name,
            "asset_key": skin_id,
            "preview_asset_key": skin_id,
            "rarity": rarity,
            "price_gems": price_gems,
        })

    # Keep the existing stable skin ID and asset path. Only its public catalog
    # name changes to match the reference; the 20-win reward remains intact.
    bind.execute(sa.text(
        """
        UPDATE orb_skins
        SET name = 'Gilded Glass', rarity = 'legendary', price_gems = NULL,
            price_amount_minor = NULL, price_currency = 'INR', enabled = TRUE
        WHERE id = 'auric-comet'
        """
    ))
    bind.execute(sa.text(
        """
        UPDATE achievements
        SET description = 'Win 20 ranked matches in a row to unlock the Gilded Glass orb.'
        WHERE code = 'RANKED_WIN_STREAK_20'
        """
    ))


def downgrade() -> None:
    bind = op.get_bind()

    # Remove only untouched product rows. Keep any SKU with ownership, active
    # selection, or a purchase record so rollback cannot erase a player's data.
    for skin_id, _name, _rarity, _price in SKINS:
        if skin_id == "classic":
            continue
        has_player_data = bind.execute(sa.text(
            """
            SELECT
                EXISTS (SELECT 1 FROM user_orb_skins WHERE skin_id = :skin_id)
                OR EXISTS (SELECT 1 FROM user_orb_skin_selections WHERE skin_id = :skin_id)
                OR EXISTS (SELECT 1 FROM orb_skin_purchases WHERE skin_id = :skin_id)
            """
        ), {"skin_id": skin_id}).scalar_one()
        if not has_player_data:
            bind.execute(sa.text("DELETE FROM orb_skins WHERE id = :skin_id"), {"skin_id": skin_id})

    bind.execute(sa.text(
        "UPDATE orb_skins SET name = 'Auric Comet' WHERE id = 'auric-comet'"
    ))
    bind.execute(sa.text(
        """
        UPDATE achievements
        SET description = 'Win 20 ranked matches in a row to unlock the Auric Comet orb.'
        WHERE code = 'RANKED_WIN_STREAK_20'
        """
    ))
