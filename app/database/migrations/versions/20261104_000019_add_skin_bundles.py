"""Add board-skin inventory and themed board/orb shop bundles."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261104_000019"
down_revision: str | None = "20261103_000018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


BOARDS = (
    ("classic-grid", "Classic Grid", "common", "classic", "A clean, familiar arena grid."),
    ("solar-grid", "Solar Grid", "rare", "solar", "A radiant grid with golden energy and illuminated tiles."),
    ("void-matrix", "Void Matrix", "legendary", "void", "A cosmic grid with deep space color and violet light."),
    ("crimson-grid", "Crimson Grid", "epic", "crimson", "A fiery surface threaded with molten seams."),
    ("neon-circuit", "Neon Circuit", "epic", "circuit", "A futuristic board traced with bright circuit lines."),
    ("frost-grid", "Frost Grid", "rare", "frost", "A crystalline grid with an icy, glass-smooth finish."),
    ("ocean-grid", "Ocean Grid", "rare", "ocean", "A cool blue surface with subtle water-like patterns."),
    ("carbon-grid", "Carbon Grid", "common", "carbon", "A dark modern finish with a restrained woven texture."),
)

# Each offer grants one board and one orb. Prices are draft Gem prices by tier.
BUNDLES = (
    ("solar-halo", "Solar Halo", "solar-grid", "electric-halo", "rare", 2200),
    ("void-cosmic", "Cosmic Void", "void-matrix", "cosmic-core", "legendary", 7000),
    ("crimson-impact", "Crimson Impact", "crimson-grid", "crimson-glass", "epic", 4200),
    ("circuit-pulse", "Circuit Pulse", "neon-circuit", "neon-circuit", "epic", 4200),
    ("void-prism", "Void Prism", "void-matrix", "void-prism", "epic", 4200),
    ("frost-core", "Frost Core", "frost-grid", "frosted-core", "rare", 2200),
    ("ocean-glass", "Ocean Glass", "ocean-grid", "cool-blue-glass", "rare", 2200),
    ("pearl-carbon", "Pearl Carbon", "carbon-grid", "matte-pearl", "common", 900),
    ("carbon-matte", "Carbon Matte", "carbon-grid", "graphite", "common", 900),
    ("ivory-solar", "Ivory Solar", "solar-grid", "ivory-glow", "common", 900),
)


def upgrade() -> None:
    op.create_table(
        "board_skins",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(96), nullable=False),
        sa.Column("rarity", sa.String(16), nullable=False, server_default="common"),
        sa.Column("palette", sa.String(32), nullable=False),
        sa.Column("description", sa.String(255), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.CheckConstraint("rarity IN ('common', 'rare', 'epic', 'legendary')", name="ck_board_skins_rarity"),
    )
    op.create_table(
        "user_board_skins",
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("skin_id", sa.String(64), sa.ForeignKey("board_skins.id"), primary_key=True),
    )
    op.create_table(
        "user_board_skin_selections",
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("skin_id", sa.String(64), sa.ForeignKey("board_skins.id"), nullable=False),
    )
    op.create_table(
        "skin_bundles",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(96), nullable=False),
        sa.Column("board_skin_id", sa.String(64), sa.ForeignKey("board_skins.id"), nullable=False),
        sa.Column("orb_skin_id", sa.String(64), sa.ForeignKey("orb_skins.id"), nullable=False),
        sa.Column("rarity", sa.String(16), nullable=False),
        sa.Column("price_gems", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.CheckConstraint("price_gems > 0", name="ck_skin_bundles_price_gems_positive"),
        sa.CheckConstraint("rarity IN ('common', 'rare', 'epic', 'legendary')", name="ck_skin_bundles_rarity"),
        sa.UniqueConstraint("orb_skin_id", name="uq_skin_bundles_orb_skin"),
    )

    bind = op.get_bind()
    for skin_id, name, rarity, palette, description in BOARDS:
        bind.execute(sa.text(
            "INSERT INTO board_skins (id, name, rarity, palette, description, enabled) "
            "VALUES (:id, :name, :rarity, :palette, :description, TRUE)"
        ), {"id": skin_id, "name": name, "rarity": rarity, "palette": palette, "description": description})
    # Classic remains the default board for all existing accounts.
    bind.execute(sa.text(
        "INSERT INTO user_board_skins (user_id, skin_id) "
        "SELECT id, 'classic-grid' FROM users ON CONFLICT DO NOTHING"
    ))
    bind.execute(sa.text(
        "INSERT INTO user_board_skin_selections (user_id, skin_id) "
        "SELECT id, 'classic-grid' FROM users ON CONFLICT DO NOTHING"
    ))

    for bundle_id, name, board_id, orb_id, rarity, price in BUNDLES:
        bind.execute(sa.text(
            "INSERT INTO skin_bundles (id, name, board_skin_id, orb_skin_id, rarity, price_gems, enabled) "
            "VALUES (:id, :name, :board, :orb, :rarity, :price, TRUE)"
        ), {"id": bundle_id, "name": name, "board": board_id, "orb": orb_id, "rarity": rarity, "price": price})
        # If a player already owns the orb from the old standalone store, grant
        # the paired board as a grandfathered bundle entitlement.
        bind.execute(sa.text(
            "INSERT INTO user_board_skins (user_id, skin_id) "
            "SELECT user_id, :board FROM user_orb_skins WHERE skin_id = :orb ON CONFLICT DO NOTHING"
        ), {"board": board_id, "orb": orb_id})

    # Preserve the ranked reward as a paired theme without making it purchasable.
    bind.execute(sa.text(
        "INSERT INTO user_board_skins (user_id, skin_id) "
        "SELECT user_id, 'solar-grid' FROM user_orb_skins WHERE skin_id = 'auric-comet' ON CONFLICT DO NOTHING"
    ))

    # Skin catalog prices are no longer used for individual sales.
    bind.execute(sa.text("UPDATE orb_skins SET price_gems = NULL WHERE id <> 'classic'"))


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text(
        "UPDATE orb_skins SET price_gems = CASE id "
        "WHEN 'electric-halo' THEN 1200 WHEN 'cosmic-core' THEN 4500 "
        "WHEN 'crimson-glass' THEN 2500 WHEN 'neon-circuit' THEN 2500 WHEN 'void-prism' THEN 2500 "
        "WHEN 'frosted-core' THEN 1200 WHEN 'cool-blue-glass' THEN 1200 "
        "WHEN 'matte-pearl' THEN 500 WHEN 'graphite' THEN 500 WHEN 'ivory-glow' THEN 500 ELSE price_gems END"
    ))
    op.drop_table("skin_bundles")
    op.drop_table("user_board_skin_selections")
    op.drop_table("user_board_skins")
    op.drop_table("board_skins")
