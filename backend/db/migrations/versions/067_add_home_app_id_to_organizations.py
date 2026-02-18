"""Add home_app_id to organizations for customizable Home tab.

Revision ID: 066_add_home_app_id
Revises: 065_create_apps_table
Create Date: 2026-02-17
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "067_add_home_app_id"
down_revision = "066_embeddings_to_pgvector"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column(
            "home_app_id",
            UUID(as_uuid=True),
            sa.ForeignKey("apps.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("organizations", "home_app_id")
