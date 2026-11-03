"""Add the Auric Comet legendary orb and ranked win-streak reward."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261103_000017"
down_revision: str | None = "20261103_000016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SKIN_ID = "auric-comet"
ACHIEVEMENT_CODE = "RANKED_WIN_STREAK_20"


def upgrade() -> None:
    op.add_column(
        "player_stats",
        sa.Column("current_ranked_win_streak", sa.Integer(), nullable=False, server_default="0"),
    )

    bind = op.get_bind()
    bind.execute(sa.text(
        """
        INSERT INTO orb_skins (
            id, name, asset_key, preview_asset_key, asset_version,
            rarity, price_gems, price_amount_minor, price_currency, enabled
        ) VALUES (
            :id, :name, :asset_key, :preview_asset_key, :asset_version,
            :rarity, NULL, NULL, 'INR', TRUE
        )
        ON CONFLICT (id) DO UPDATE SET
            name = EXCLUDED.name,
            asset_key = EXCLUDED.asset_key,
            preview_asset_key = EXCLUDED.preview_asset_key,
            asset_version = EXCLUDED.asset_version,
            rarity = EXCLUDED.rarity,
            price_gems = NULL,
            enabled = TRUE
        """
    ), {
        "id": SKIN_ID,
        "name": "Auric Comet",
        "asset_key": SKIN_ID,
        "preview_asset_key": SKIN_ID,
        "asset_version": "1",
        "rarity": "legendary",
    })

    achievement_id = bind.execute(
        sa.text("SELECT id FROM achievements WHERE code = :code"),
        {"code": ACHIEVEMENT_CODE},
    ).scalar_one_or_none()
    if achievement_id is None:
        achievement_id = (bind.execute(sa.text("SELECT COALESCE(MAX(id), 0) + 1 FROM achievements")).scalar_one())
        bind.execute(sa.text(
            """
            INSERT INTO achievements (id, code, name, description, badge_image_url, enabled)
            VALUES (:id, :code, :name, :description, :badge_image_url, TRUE)
            """
        ), {
            "id": achievement_id,
            "code": ACHIEVEMENT_CODE,
            "name": "Golden Momentum",
            "description": "Win 20 ranked matches in a row to unlock the Auric Comet orb.",
            "badge_image_url": "/badges/auric-comet.svg",
        })
    else:
        bind.execute(sa.text(
            """
            UPDATE achievements
            SET name = :name, description = :description,
                badge_image_url = :badge_image_url, enabled = TRUE
            WHERE id = :id
            """
        ), {
            "id": achievement_id,
            "name": "Golden Momentum",
            "description": "Win 20 ranked matches in a row to unlock the Auric Comet orb.",
            "badge_image_url": "/badges/auric-comet.svg",
        })

    bind.execute(sa.text(
        """
        INSERT INTO achievement_orb_skin_rewards (achievement_id, skin_id)
        VALUES (:achievement_id, :skin_id)
        ON CONFLICT (achievement_id, skin_id) DO NOTHING
        """
    ), {"achievement_id": achievement_id, "skin_id": SKIN_ID})

    # Seed an honest current streak from completed ranked games so this feature
    # does not reset a player's existing ranked progress at deployment.
    ranked_streaks: dict[object, int] = {}
    ranked_games = bind.execute(sa.text(
        """
        SELECT winner_id, loser_id
        FROM games
        WHERE is_ranked = TRUE AND status = 'FINISHED'
          AND winner_id IS NOT NULL AND loser_id IS NOT NULL
        ORDER BY COALESCE(finished_at, created_at), created_at, id
        """
    ))
    for row in ranked_games:
        winner_id, loser_id = row
        ranked_streaks[winner_id] = ranked_streaks.get(winner_id, 0) + 1
        ranked_streaks[loser_id] = 0

    for user_id, streak in ranked_streaks.items():
        bind.execute(sa.text(
            "UPDATE player_stats SET current_ranked_win_streak = :streak WHERE user_id = :user_id"
        ), {"user_id": user_id, "streak": streak})
        if streak >= 20:
            bind.execute(sa.text(
                """
                INSERT INTO user_achievements (user_id, achievement_id)
                VALUES (:user_id, :achievement_id)
                ON CONFLICT (user_id, achievement_id) DO NOTHING
                """
            ), {
                "user_id": user_id,
                "achievement_id": achievement_id,
            })
            bind.execute(sa.text(
                """
                INSERT INTO user_orb_skins (user_id, skin_id)
                VALUES (:user_id, :skin_id)
                ON CONFLICT (user_id, skin_id) DO NOTHING
                """
            ), {"user_id": user_id, "skin_id": SKIN_ID})


def downgrade() -> None:
    bind = op.get_bind()
    achievement_id = bind.execute(
        sa.text("SELECT id FROM achievements WHERE code = :code"),
        {"code": ACHIEVEMENT_CODE},
    ).scalar_one_or_none()
    if achievement_id is not None:
        bind.execute(sa.text("DELETE FROM achievement_orb_skin_rewards WHERE achievement_id = :id"), {"id": achievement_id})
        bind.execute(sa.text("DELETE FROM user_achievements WHERE achievement_id = :id"), {"id": achievement_id})
        bind.execute(sa.text("DELETE FROM achievements WHERE id = :id"), {"id": achievement_id})
    bind.execute(sa.text("DELETE FROM user_orb_skins WHERE skin_id = :id"), {"id": SKIN_ID})
    bind.execute(sa.text("DELETE FROM user_orb_skin_selections WHERE skin_id = :id"), {"id": SKIN_ID})
    bind.execute(sa.text("DELETE FROM orb_skins WHERE id = :id"), {"id": SKIN_ID})
    op.drop_column("player_stats", "current_ranked_win_streak")
