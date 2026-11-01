"""Add profiles, avatars, statistics, and achievements.

Revision ID: 20250124_000006
Revises: 20250120_000005
Create Date: 2025-01-24 00:00:00.000000
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20250124_000006"
down_revision: str | None = "20250120_000005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


AVATARS = [
    {"id": 1, "code": "green-reactor", "name": "Green Reactor", "image_url": "/avatars/green-reactor.svg", "enabled": True, "sort_order": 1},
    {"id": 2, "code": "red-nova", "name": "Red Nova", "image_url": "/avatars/red-nova.svg", "enabled": True, "sort_order": 2},
    {"id": 3, "code": "atomic-fox", "name": "Atomic Fox", "image_url": "/avatars/atomic-fox.svg", "enabled": True, "sort_order": 3},
    {"id": 4, "code": "quantum-owl", "name": "Quantum Owl", "image_url": "/avatars/quantum-owl.svg", "enabled": True, "sort_order": 4},
    {"id": 5, "code": "plasma-wolf", "name": "Plasma Wolf", "image_url": "/avatars/plasma-wolf.svg", "enabled": True, "sort_order": 5},
    {"id": 6, "code": "chain-master", "name": "Chain Master", "image_url": "/avatars/chain-master.svg", "enabled": True, "sort_order": 6},
]

ACHIEVEMENTS = [
    {
        "id": 1,
        "code": "WIN_STREAK_20",
        "name": "Unstoppable",
        "description": "Win 20 consecutive matches.",
        "badge_image_url": "/badges/unstoppable.svg",
        "enabled": True,
    }
]


def upgrade() -> None:
    op.create_table(
        "avatars",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("code", sa.String(length=64), nullable=False, unique=True),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("image_url", sa.String(length=255), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
    )
    op.bulk_insert(
        sa.table(
            "avatars",
            sa.column("id", sa.Integer()),
            sa.column("code", sa.String()),
            sa.column("name", sa.String()),
            sa.column("image_url", sa.String()),
            sa.column("enabled", sa.Boolean()),
            sa.column("sort_order", sa.Integer()),
        ),
        AVATARS,
    )

    op.create_table(
        "user_profiles",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("avatar_id", sa.Integer(), sa.ForeignKey("avatars.id"), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "player_stats",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("matches_played", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("matches_won", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("current_win_streak", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("longest_win_streak", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("longest_chain", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "achievements",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("code", sa.String(length=64), nullable=False, unique=True),
        sa.Column("name", sa.String(length=96), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=False),
        sa.Column("badge_image_url", sa.String(length=255), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.bulk_insert(
        sa.table(
            "achievements",
            sa.column("id", sa.Integer()),
            sa.column("code", sa.String()),
            sa.column("name", sa.String()),
            sa.column("description", sa.String()),
            sa.column("badge_image_url", sa.String()),
            sa.column("enabled", sa.Boolean()),
        ),
        ACHIEVEMENTS,
    )
    op.create_table(
        "user_achievements",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("achievement_id", sa.Integer(), sa.ForeignKey("achievements.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("earned_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("achievement_metadata", sa.JSON(), nullable=True),
    )

    op.add_column("games", sa.Column("stats_applied_at", sa.DateTime(timezone=True), nullable=True))

    op.execute(
        "INSERT INTO user_profiles (user_id, avatar_id, created_at, updated_at) "
        "SELECT id, 1, created_at, updated_at FROM users"
    )
    op.execute(
        "INSERT INTO player_stats (user_id, matches_played, matches_won, current_win_streak, longest_win_streak, longest_chain, updated_at) "
        "SELECT id, 0, 0, 0, 0, 0, updated_at FROM users"
    )
    _backfill_existing_game_statistics()

    op.drop_index("ix_users_username_non_guest_unique", table_name="users")
    op.execute(
        "CREATE UNIQUE INDEX ix_users_username_non_guest_unique "
        "ON users (lower(username)) WHERE is_guest = false"
    )


def _backfill_existing_game_statistics() -> None:
    """Build profile counters from completed games and stored move events."""
    bind = op.get_bind()
    stats: dict[object, dict[str, int]] = {}

    for row in bind.execute(
        sa.text(
            "SELECT id, winner_id, loser_id, finished_at, created_at "
            "FROM games "
            "WHERE status = 'FINISHED' AND winner_id IS NOT NULL AND loser_id IS NOT NULL "
            "ORDER BY COALESCE(finished_at, created_at), created_at"
        )
    ):
        game = row._mapping
        winner_id = game["winner_id"]
        loser_id = game["loser_id"]
        winner = stats.setdefault(
            winner_id,
            {"matches": 0, "wins": 0, "streak": 0, "longest_streak": 0, "chain": 0},
        )
        loser = stats.setdefault(
            loser_id,
            {"matches": 0, "wins": 0, "streak": 0, "longest_streak": 0, "chain": 0},
        )
        winner["matches"] += 1
        winner["wins"] += 1
        winner["streak"] += 1
        winner["longest_streak"] = max(winner["longest_streak"], winner["streak"])
        loser["matches"] += 1
        loser["streak"] = 0

        completed_at = game["finished_at"] or game["created_at"]
        bind.execute(
            sa.text("UPDATE games SET stats_applied_at = :completed_at WHERE id = :game_id"),
            {"completed_at": completed_at, "game_id": game["id"]},
        )
        bind.execute(
            sa.text(
                "UPDATE game_participants SET result = "
                "CASE WHEN user_id = :winner_id THEN 'WIN' ELSE 'LOSS' END "
                "WHERE game_id = :game_id"
            ),
            {"winner_id": winner_id, "game_id": game["id"]},
        )

    participant_users = {
        (row._mapping["game_id"], row._mapping["player_slot"]): row._mapping["user_id"]
        for row in bind.execute(
            sa.text("SELECT game_id, player_slot, user_id FROM game_participants")
        )
    }
    for row in bind.execute(
        sa.text("SELECT game_id, payload FROM game_events WHERE event_type = 'move'")
    ):
        event = row._mapping
        payload = event["payload"] or {}
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                continue
        if not isinstance(payload, dict):
            continue
        player_slot = payload.get("player_slot")
        user_id = participant_users.get((event["game_id"], player_slot))
        if user_id is None:
            continue
        reaction = payload.get("reaction") or {}
        chain_length = len(reaction.get("steps") or []) if isinstance(reaction, dict) else 0
        user_stats = stats.setdefault(
            user_id,
            {"matches": 0, "wins": 0, "streak": 0, "longest_streak": 0, "chain": 0},
        )
        user_stats["chain"] = max(user_stats["chain"], chain_length)

    for user_id, user_stats in stats.items():
        bind.execute(
            sa.text(
                "UPDATE player_stats SET matches_played = :matches, matches_won = :wins, "
                "current_win_streak = :streak, longest_win_streak = :longest_streak, "
                "longest_chain = :chain WHERE user_id = :user_id"
            ),
            {**user_stats, "user_id": user_id},
        )
        if user_stats["longest_streak"] >= 20:
            bind.execute(
                sa.text(
                    "INSERT INTO user_achievements "
                    "(user_id, achievement_id, earned_at, achievement_metadata) "
                    "VALUES (:user_id, 1, CURRENT_TIMESTAMP, :metadata)"
                ),
                {
                    "user_id": user_id,
                    "metadata": '{"source":"historical_backfill"}',
                },
            )


def downgrade() -> None:
    op.drop_index("ix_users_username_non_guest_unique", table_name="users")
    op.create_index(
        "ix_users_username_non_guest_unique",
        "users",
        ["username"],
        unique=True,
        postgresql_where=sa.text("is_guest = false"),
        sqlite_where=sa.text("is_guest = 0"),
    )
    op.drop_column("games", "stats_applied_at")
    op.drop_table("user_achievements")
    op.drop_table("achievements")
    op.drop_table("player_stats")
    op.drop_table("user_profiles")
    op.drop_table("avatars")
