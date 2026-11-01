"""Add orb skin catalog, inventory, and selection.

Revision ID: 20250128_000007
Revises: 20250124_000006
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20250128_000007"
down_revision: str | None = "20250124_000006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "orb_skins",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(96), nullable=False),
        sa.Column("asset_key", sa.String(64), nullable=False, unique=True),
        sa.Column("preview_asset_key", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.bulk_insert(
        sa.table("orb_skins", sa.column("id", sa.String()), sa.column("name", sa.String()),
                 sa.column("asset_key", sa.String()), sa.column("preview_asset_key", sa.String()),
                 sa.column("enabled", sa.Boolean())),
        [{"id": "classic", "name": "Classic Orb", "asset_key": "classic",
          "preview_asset_key": "classic", "enabled": True}],
    )
    op.create_table(
        "user_orb_skins",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("skin_id", sa.String(64), sa.ForeignKey("orb_skins.id"), primary_key=True),
    )
    op.create_table(
        "user_orb_skin_selections",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("skin_id", sa.String(64), sa.ForeignKey("orb_skins.id"), nullable=False),
    )
    op.execute("INSERT INTO user_orb_skins (user_id, skin_id) SELECT id, 'classic' FROM users")
    op.execute("INSERT INTO user_orb_skin_selections (user_id, skin_id) SELECT id, 'classic' FROM users")


def downgrade() -> None:
    op.drop_table("user_orb_skin_selections")
    op.drop_table("user_orb_skins")
    op.drop_table("orb_skins")
