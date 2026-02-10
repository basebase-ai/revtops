"""Add hubspot_user_id column to users table.

Revision ID: 041
Revises: 040
Create Date: 2026-02-09

Stores the HubSpot numeric owner ID so we can map local users to HubSpot
owners (needed for deal assignment, activity attribution, etc.).
"""
from alembic import op
import sqlalchemy as sa

revision = "041"
down_revision = "040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("hubspot_user_id", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "hubspot_user_id")
