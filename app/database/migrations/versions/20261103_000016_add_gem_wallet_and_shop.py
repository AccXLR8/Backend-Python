"""Replace direct orb-skin cash prices with a Gems wallet and store offers."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261103_000016"
down_revision: str | None = "20261103_000015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Keep old direct-cash columns as legacy data. The new shop ignores them.
    op.add_column("orb_skins", sa.Column("price_gems", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_orb_skins_price_gems_positive",
        "orb_skins",
        "price_gems IS NULL OR price_gems > 0",
    )

    op.create_table(
        "user_gem_wallets",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("gem_balance", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("gem_balance >= 0", name="ck_user_gem_wallet_nonnegative"),
    )

    op.create_table(
        "gem_packs",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("name", sa.String(length=96), nullable=False),
        sa.Column("gems_amount", sa.Integer(), nullable=False),
        sa.Column("price_amount_minor", sa.Integer(), nullable=False),
        sa.Column("price_currency", sa.String(length=3), nullable=False, server_default="INR"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint("gems_amount > 0", name="ck_gem_pack_amount_positive"),
        sa.CheckConstraint("price_amount_minor > 0", name="ck_gem_pack_price_positive"),
        sa.CheckConstraint(
            "length(price_currency) = 3 AND price_currency = upper(price_currency)",
            name="ck_gem_pack_currency",
        ),
    )

    op.create_table(
        "gem_pack_purchases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("pack_id", sa.String(length=64), sa.ForeignKey("gem_packs.id"), nullable=False),
        sa.Column("razorpay_order_id", sa.String(length=64), nullable=False, unique=True),
        sa.Column("razorpay_payment_id", sa.String(length=64), nullable=True),
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("gems_amount", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="created"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("amount_minor > 0", name="ck_gem_pack_purchase_price_positive"),
        sa.CheckConstraint("gems_amount > 0", name="ck_gem_pack_purchase_gems_positive"),
        sa.CheckConstraint("status IN ('created', 'paid')", name="ck_gem_pack_purchase_status"),
        sa.UniqueConstraint("razorpay_payment_id", name="uq_gem_pack_purchase_payment"),
    )
    op.create_index(
        "ix_gem_pack_purchases_user_created",
        "gem_pack_purchases",
        ["user_id", "created_at"],
    )

    op.create_table(
        "wallet_transactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("amount_delta", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=16), nullable=False),
        sa.Column("reference_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "reason", "reference_id", name="uq_wallet_transaction_reference"),
        sa.CheckConstraint("amount_delta <> 0", name="ck_wallet_transaction_nonzero"),
        sa.CheckConstraint("reason IN ('gem_pack', 'orb_skin')", name="ck_wallet_transaction_reason"),
    )
    op.create_index(
        "ix_wallet_transactions_user_created",
        "wallet_transactions",
        ["user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_wallet_transactions_user_created", table_name="wallet_transactions")
    op.drop_table("wallet_transactions")
    op.drop_index("ix_gem_pack_purchases_user_created", table_name="gem_pack_purchases")
    op.drop_table("gem_pack_purchases")
    op.drop_table("gem_packs")
    op.drop_table("user_gem_wallets")
    op.drop_constraint("ck_orb_skins_price_gems_positive", "orb_skins", type_="check")
    op.drop_column("orb_skins", "price_gems")
