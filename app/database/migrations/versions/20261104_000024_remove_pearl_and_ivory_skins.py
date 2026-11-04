"""Retire Pearl Carbon and Ivory Glow from the player catalog."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261104_000024"
down_revision: str | None = "20261104_000023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text(
        "UPDATE user_orb_skin_selections SET skin_id = 'classic' "
        "WHERE skin_id IN ('matte-pearl', 'ivory-glow')"
    ))
    bind.execute(sa.text(
        "DELETE FROM user_orb_skins WHERE skin_id IN ('matte-pearl', 'ivory-glow')"
    ))
    bind.execute(sa.text(
        "DELETE FROM achievement_orb_skin_rewards "
        "WHERE skin_id IN ('matte-pearl', 'ivory-glow')"
    ))
    bind.execute(sa.text(
        "UPDATE skin_bundles SET enabled = FALSE "
        "WHERE id IN ('pearl-carbon', 'ivory-solar')"
    ))
    bind.execute(sa.text(
        "UPDATE orb_skins SET enabled = FALSE, price_gems = NULL "
        "WHERE id IN ('matte-pearl', 'ivory-glow')"
    ))


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text(
        "UPDATE orb_skins SET enabled = TRUE, price_gems = NULL "
        "WHERE id IN ('matte-pearl', 'ivory-glow')"
    ))
    bind.execute(sa.text(
        "UPDATE skin_bundles SET enabled = TRUE "
        "WHERE id IN ('pearl-carbon', 'ivory-solar')"
    ))
