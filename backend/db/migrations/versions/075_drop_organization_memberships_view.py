"""Drop the organization_memberships backward-compat view.

The view was created in migration 060 when renaming the table to org_members.
It's confusing for developers who see both and think they're separate tables.

Revision ID: 075_drop_org_memberships_view
Revises: 074_fix_slack_scope
Create Date: 2026-02-23
"""

from alembic import op
from sqlalchemy import text


revision = "075_drop_org_memberships_view"
down_revision = "074_fix_slack_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("DROP VIEW IF EXISTS organization_memberships"))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text(
        "CREATE VIEW organization_memberships AS SELECT * FROM org_members"
    ))
