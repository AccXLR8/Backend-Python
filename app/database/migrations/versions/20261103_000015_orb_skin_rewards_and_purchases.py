"""Add achievement rewards and Razorpay purchase ledger for orb skins."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261103_000015"
down_revision: str | None = "20261103_000014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "orb_skins",
        sa.Column("rarity", sa.String(length=16), nullable=False, server_default="common"),
    )
    op.add_column("orb_skins", sa.Column("price_amount_minor", sa.Integer(), nullable=True))
    op.add_column(
        "orb_skins",
        sa.Column("price_currency", sa.String(length=3), nullable=False, server_default="INR"),
    )
    op.create_check_constraint(
        "ck_orb_skins_rarity",
        "orb_skins",
        "rarity IN ('common', 'rare', 'epic', 'legendary')",
    )
    op.create_check_constraint(
        "ck_orb_skins_price_positive",
        "orb_skins",
        "price_amount_minor IS NULL OR price_amount_minor > 0",
    )
    op.create_check_constraint(
        "ck_orb_skins_price_currency",
        "orb_skins",
        "length(price_currency) = 3 AND price_currency = upper(price_currency)",
    )

    op.create_table(
        "achievement_orb_skin_rewards",
        sa.Column(
            "achievement_id",
            sa.Integer(),
            sa.ForeignKey("achievements.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "skin_id",
            sa.String(length=64),
            sa.ForeignKey("orb_skins.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )

    op.create_table(
        "orb_skin_purchases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("skin_id", sa.String(length=64), sa.ForeignKey("orb_skins.id"), nullable=False),
        sa.Column("razorpay_order_id", sa.String(length=64), nullable=False, unique=True),
        sa.Column("razorpay_payment_id", sa.String(length=64), nullable=True),
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="created"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("amount_minor > 0", name="ck_orb_skin_purchase_amount_positive"),
        sa.CheckConstraint("status IN ('created', 'paid', 'failed')", name="ck_orb_skin_purchase_status"),
        sa.UniqueConstraint("razorpay_payment_id", name="uq_orb_skin_purchase_payment"),
    )
    op.create_index(
        "ix_orb_skin_purchases_user_created",
        "orb_skin_purchases",
        ["user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_orb_skin_purchases_user_created", table_name="orb_skin_purchases")
    op.drop_table("orb_skin_purchases")
    op.drop_table("achievement_orb_skin_rewards")
    op.drop_constraint("ck_orb_skins_price_currency", "orb_skins", type_="check")
    op.drop_constraint("ck_orb_skins_price_positive", "orb_skins", type_="check")
    op.drop_constraint("ck_orb_skins_rarity", "orb_skins", type_="check")
    op.drop_column("orb_skins", "price_currency")
    op.drop_column("orb_skins", "price_amount_minor")
    op.drop_column("orb_skins", "rarity")
