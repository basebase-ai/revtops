"""Drop legacy salesforce_user_id and hubspot_user_id from users table.

Revision ID: 046
Revises: 045
Create Date: 2026-02-10

These external identity mappings now live in user_mappings_for_identity.
"""
from alembic import op
import sqlalchemy as sa

revision = "046"
down_revision = "045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("users", "salesforce_user_id")
    # hubspot_user_id may already be gone (dropped via raw SQL); ignore if missing
    op.execute(
        "ALTER TABLE users DROP COLUMN IF EXISTS hubspot_user_id"
    )


def downgrade() -> None:
    op.add_column(
        "users",
        sa.Column("salesforce_user_id", sa.String(255), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("hubspot_user_id", sa.String(255), nullable=True),
    )
