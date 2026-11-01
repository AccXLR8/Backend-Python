"""Persist queue mode and clock deadlines; enforce participant uniqueness.

Revision ID: 20250202_000009
Revises: 20250131_000008
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20250202_000009"
down_revision: str | None = "20250131_000008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("games", sa.Column("matchmaking_mode", sa.String(length=16), nullable=True))
    op.add_column("games", sa.Column("turn_deadline_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_games_turn_deadline_at", "games", ["turn_deadline_at"])
    op.create_index(
        "ix_games_player1_matchmaking_status",
        "games",
        ["player_1_id", "matchmaking_mode", "status"],
    )
    op.create_index(
        "ix_games_player2_matchmaking_status",
        "games",
        ["player_2_id", "matchmaking_mode", "status"],
    )

    # Ranked state is unambiguous on old rows. Casual mode is left null because
    # custom games can use the same dimensions as a matchmaking preset.
    op.execute("UPDATE games SET matchmaking_mode = 'ranked' WHERE is_ranked IS TRUE")
    op.execute(
        """
        UPDATE games AS game
        SET turn_deadline_at = game.active_clock_started_at
            + (participant.remaining_time_ms * INTERVAL '1 millisecond')
        FROM game_participants AS participant
        WHERE participant.game_id = game.id
          AND participant.player_slot = game.current_player_slot
          AND game.status IN ('READY', 'ACTIVE')
          AND game.active_clock_started_at IS NOT NULL
        """
    )

    # Keep the participant row that agrees with the game roster when cleaning
    # any duplicate rows left by pre-existing concurrent joins.
    op.execute(
        """
        WITH ranked AS (
            SELECT participant.id,
                   ROW_NUMBER() OVER (
                       PARTITION BY participant.game_id, participant.user_id
                       ORDER BY CASE
                           WHEN participant.player_slot = 0 AND game.player_1_id = participant.user_id THEN 0
                           WHEN participant.player_slot = 1 AND game.player_2_id = participant.user_id THEN 0
                           ELSE 1
                       END, participant.id
                   ) AS row_number
            FROM game_participants AS participant
            JOIN games AS game ON game.id = participant.game_id
        )
        DELETE FROM game_participants AS participant
        USING ranked
        WHERE participant.id = ranked.id AND ranked.row_number > 1
        """
    )
    op.execute(
        """
        WITH ranked AS (
            SELECT participant.id,
                   ROW_NUMBER() OVER (
                       PARTITION BY participant.game_id, participant.player_slot
                       ORDER BY CASE
                           WHEN participant.player_slot = 0 AND game.player_1_id = participant.user_id THEN 0
                           WHEN participant.player_slot = 1 AND game.player_2_id = participant.user_id THEN 0
                           ELSE 1
                       END, participant.id
                   ) AS row_number
            FROM game_participants AS participant
            JOIN games AS game ON game.id = participant.game_id
        )
        DELETE FROM game_participants AS participant
        USING ranked
        WHERE participant.id = ranked.id AND ranked.row_number > 1
        """
    )

    inspector = sa.inspect(op.get_bind())
    unique_sets = {
        frozenset(item["column_names"])
        for item in inspector.get_unique_constraints("game_participants")
    }
    unique_sets.update(
        frozenset(item["column_names"])
        for item in inspector.get_indexes("game_participants")
        if item.get("unique")
    )
    if frozenset({"game_id", "player_slot"}) not in unique_sets:
        op.create_unique_constraint(
            "uq_participant_game_slot_safety", "game_participants", ["game_id", "player_slot"]
        )
    if frozenset({"game_id", "user_id"}) not in unique_sets:
        op.create_unique_constraint(
            "uq_participant_game_user", "game_participants", ["game_id", "user_id"]
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    constraint_names = {item["name"] for item in inspector.get_unique_constraints("game_participants")}
    if "uq_participant_game_user" in constraint_names:
        op.drop_constraint("uq_participant_game_user", "game_participants", type_="unique")
    if "uq_participant_game_slot_safety" in constraint_names:
        op.drop_constraint("uq_participant_game_slot_safety", "game_participants", type_="unique")
    op.drop_index("ix_games_player2_matchmaking_status", table_name="games")
    op.drop_index("ix_games_player1_matchmaking_status", table_name="games")
    op.drop_index("ix_games_turn_deadline_at", table_name="games")
    op.drop_column("games", "turn_deadline_at")
    op.drop_column("games", "matchmaking_mode")
