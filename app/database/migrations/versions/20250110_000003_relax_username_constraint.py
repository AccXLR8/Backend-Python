"""Allow duplicate guest usernames

Revision ID: 20250110_000003
Revises: 20250106_000002
Create Date: 2025-01-10 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20250110_000003"
down_revision: Union[str, None] = "20250106_000002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _users_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=True),
        sa.Column("is_guest", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "sqlite":
        _recreate_users_table_sqlite(add_unique_constraint=False)
        _create_partial_unique_index_sqlite()
    else:
        _drop_username_unique_constraint()
        _create_partial_unique_index_postgres()


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    _ensure_no_duplicate_usernames(bind)

    if dialect == "sqlite":
        op.drop_index("ix_users_username_non_guest_unique", table_name="users")
        _recreate_users_table_sqlite(add_unique_constraint=True)
    else:
        op.drop_index("ix_users_username_non_guest_unique", table_name="users")
        op.create_unique_constraint("users_username_key", "users", ["username"])


def _drop_username_unique_constraint() -> None:
    # Works for PostgreSQL and other dialects that support DROP CONSTRAINT.
    op.drop_constraint("users_username_key", "users", type_="unique")


def _create_partial_unique_index_postgres() -> None:
    op.create_index(
        "ix_users_username_non_guest_unique",
        "users",
        ["username"],
        unique=True,
        postgresql_where=sa.text("is_guest = false"),
    )


def _create_partial_unique_index_sqlite() -> None:
    op.create_index(
        "ix_users_username_non_guest_unique",
        "users",
        ["username"],
        unique=True,
        sqlite_where=sa.text("is_guest = 0"),
    )


def _recreate_users_table_sqlite(add_unique_constraint: bool) -> None:
    op.execute(sa.text("PRAGMA foreign_keys=OFF"))
    try:
        op.execute(sa.text("ALTER TABLE users RENAME TO users__old"))

        constraints: list[sa.schema.Constraint] = []
        if add_unique_constraint:
            constraints.append(sa.UniqueConstraint("username", name="users_username_key"))

        op.create_table("users", *_users_columns(), *constraints)

        column_list = "id, username, password_hash, is_guest, created_at, updated_at"
        op.execute(
            sa.text(
                f"INSERT INTO users ({column_list}) "
                f"SELECT {column_list} FROM users__old"
            )
        )

        op.drop_table("users__old")
    finally:
        op.execute(sa.text("PRAGMA foreign_keys=ON"))


def _ensure_no_duplicate_usernames(bind) -> None:
    duplicates = bind.execute(
        sa.text(
            "SELECT username FROM users GROUP BY username HAVING COUNT(*) > 1"
        )
    ).fetchall()
    if duplicates:
        usernames = ", ".join(row[0] for row in duplicates)
        raise RuntimeError(
            "Cannot downgrade while duplicate usernames exist. "
            "Remove duplicates first. Offending usernames: " + usernames
        )
