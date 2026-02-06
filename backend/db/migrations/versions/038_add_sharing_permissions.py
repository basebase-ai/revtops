"""add sharing permissions and user team graph

Revision ID: 038_add_sharing_permissions
Revises: 037_fix_users_rls_policy
Create Date: 2026-02-06
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "038_add_sharing_permissions"
down_revision = "037_fix_users_rls_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("team_member_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")))

    for table, default_tier in (
        ("conversations", "me"),
        ("workflows", "me"),
        ("integrations", "team"),
    ):
        op.add_column(table, sa.Column("access_tier", sa.String(length=20), nullable=False, server_default=default_tier))
        op.add_column(table, sa.Column("access_level", sa.String(length=20), nullable=False, server_default="edit"))


def downgrade() -> None:
    for table in ("integrations", "workflows", "conversations"):
        op.drop_column(table, "access_level")
        op.drop_column(table, "access_tier")

    op.drop_column("users", "team_member_ids")
