"""Require unique account email addresses and track verification.

Revision ID: 20261102_000013
Revises: 20250204_000012
Create Date: 2026-11-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261102_000013"
down_revision: str | None = "20250204_000012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    duplicate_emails = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM ("
            "SELECT lower(email) FROM users "
            "WHERE email IS NOT NULL GROUP BY lower(email) HAVING COUNT(*) > 1"
            ") AS duplicate_email_groups"
        )
    ).scalar_one()
    if duplicate_emails:
        raise RuntimeError(
            "Cannot enforce unique account emails: duplicate email addresses exist. "
            "Resolve duplicate users before rerunning migrations."
        )

    op.add_column(
        "users",
        sa.Column("email_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute(
        "UPDATE users SET email_verified = TRUE "
        "WHERE google_subject IS NOT NULL AND email IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX ix_users_email_lower_unique "
        "ON users (lower(email)) WHERE email IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX ix_users_email_lower_unique")
    op.drop_column("users", "email_verified")
