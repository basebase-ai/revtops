"""Add home_app_id to organizations for customizable Home tab.

Revision ID: 067_add_home_app_id
Revises: 066_embeddings_to_pgvector
Create Date: 2026-02-17
"""

from alembic import op

revision = "067_add_home_app_id"
down_revision = "066_embeddings_to_pgvector"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Compatibility-safe for environments that already applied
    # 066_add_home_app_id_to_organizations.
    op.execute(
        """
        ALTER TABLE organizations
        ADD COLUMN IF NOT EXISTS home_app_id UUID REFERENCES apps(id) ON DELETE SET NULL
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE organizations DROP COLUMN IF EXISTS home_app_id")
