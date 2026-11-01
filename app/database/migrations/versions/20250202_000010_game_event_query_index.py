"""Index game events and initialize participant presence as offline.

Revision ID: 20250202_000010
Revises: 20250202_000009
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20250202_000010"
down_revision: str | None = "20250202_000009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Connection state is now maintained from shared Redis presence. Existing
    # booleans cannot be trusted after a process restart, so start them offline.
    op.execute("UPDATE game_participants SET connected = FALSE")
    op.alter_column(
        "game_participants",
        "connected",
        existing_type=sa.Boolean(),
        server_default=sa.false(),
    )
    op.create_index(
        "ix_game_events_game_sequence",
        "game_events",
        ["game_id", "sequence"],
    )


def downgrade() -> None:
    op.drop_index("ix_game_events_game_sequence", table_name="game_events")
    op.alter_column(
        "game_participants",
        "connected",
        existing_type=sa.Boolean(),
        server_default=sa.true(),
    )
