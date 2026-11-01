"""Add Google identity fields to users.

Revision ID: 20250120_000005
Revises: 20250116_000004
Create Date: 2025-01-20 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20250120_000005"
down_revision: Union[str, None] = "20250116_000004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("google_subject", sa.String(length=255), nullable=True))
    op.add_column("users", sa.Column("email", sa.String(length=320), nullable=True))
    op.create_index(
        "ix_users_google_subject_unique",
        "users",
        ["google_subject"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_users_google_subject_unique", table_name="users")
    op.drop_column("users", "email")
    op.drop_column("users", "google_subject")
