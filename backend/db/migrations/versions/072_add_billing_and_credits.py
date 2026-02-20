"""Add billing and credit tracking to organizations.

Revision ID: 072_add_billing_and_credits
Revises: 071_rename_open_web
Create Date: 2026-02-19

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "072_add_billing_and_credits"
down_revision: Union[str, None] = "071_rename_open_web"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- Organization billing columns ---
    op.add_column(
        "organizations",
        sa.Column("stripe_customer_id", sa.String(255), nullable=True),
    )
    op.add_column(
        "organizations",
        sa.Column("stripe_subscription_id", sa.String(255), nullable=True),
    )
    op.add_column(
        "organizations",
        sa.Column("subscription_tier", sa.String(32), nullable=True),
    )
    op.add_column(
        "organizations",
        sa.Column("subscription_status", sa.String(32), nullable=True),
    )
    op.add_column(
        "organizations",
        sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "organizations",
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "organizations",
        sa.Column("credits_balance", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "organizations",
        sa.Column("credits_included", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_organizations_stripe_customer_id",
        "organizations",
        ["stripe_customer_id"],
        unique=True,
    )

    # --- credit_transactions ---
    op.create_table(
        "credit_transactions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL", onupdate="CASCADE"),
            nullable=True,
        ),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("balance_after", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(64), nullable=False),
        sa.Column("reference_type", sa.String(32), nullable=True),
        sa.Column("reference_id", sa.String(255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_credit_transactions_organization_id",
        "credit_transactions",
        ["organization_id"],
    )
    op.create_index(
        "ix_credit_transactions_created_at",
        "credit_transactions",
        ["organization_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_credit_transactions_created_at",
        table_name="credit_transactions",
    )
    op.drop_index(
        "ix_credit_transactions_organization_id",
        table_name="credit_transactions",
    )
    op.drop_table("credit_transactions")

    op.drop_index(
        "ix_organizations_stripe_customer_id",
        table_name="organizations",
    )
    op.drop_column("organizations", "credits_included")
    op.drop_column("organizations", "credits_balance")
    op.drop_column("organizations", "current_period_end")
    op.drop_column("organizations", "current_period_start")
    op.drop_column("organizations", "subscription_status")
    op.drop_column("organizations", "subscription_tier")
    op.drop_column("organizations", "stripe_subscription_id")
    op.drop_column("organizations", "stripe_customer_id")
