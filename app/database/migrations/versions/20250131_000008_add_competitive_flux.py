"""Add ranked-game marker, Flux records, and per-game rating history.

Revision ID: 20250131_000008
Revises: 20250128_000007
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20250131_000008"
down_revision: str | None = "20250128_000007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("games", sa.Column("is_ranked", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table(
        "competitive_ratings",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("wins", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("losses", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("flux", sa.Numeric(3, 2), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "game_rating_changes",
        sa.Column("game_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("games.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("flux_before", sa.Numeric(3, 2), nullable=True),
        sa.Column("flux_after", sa.Numeric(3, 2), nullable=True),
        sa.Column("wins_after", sa.Integer(), nullable=False),
        sa.Column("losses_after", sa.Integer(), nullable=False),
        sa.Column("result", sa.String(8), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("game_rating_changes")
    op.drop_table("competitive_ratings")
    op.drop_column("games", "is_ranked")
