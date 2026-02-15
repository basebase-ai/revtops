"""Rename organization_memberships to org_members with backward-compat view.

Revision ID: 060_rename_org_memberships_to_org_members
Revises: 059_multi_level_agent_context
Create Date: 2026-02-15
"""

from alembic import op
from sqlalchemy import text


revision = "060_rename_to_org_members"
down_revision = "059_multi_level_agent_context"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # 1. Drop the existing RLS policy (references old table name)
    conn.execute(text("DROP POLICY IF EXISTS org_isolation ON organization_memberships"))

    # 2. Rename the table
    op.rename_table("organization_memberships", "org_members")

    # 3. Re-create the RLS policy on the new table name
    conn.execute(text("ALTER TABLE org_members ENABLE ROW LEVEL SECURITY"))
    conn.execute(text("ALTER TABLE org_members FORCE ROW LEVEL SECURITY"))
    conn.execute(text("""
        CREATE POLICY org_isolation ON org_members
        FOR ALL
        USING (
            organization_id::text = COALESCE(
                NULLIF(current_setting('app.current_org_id', true), ''),
                '00000000-0000-0000-0000-000000000000'
            )
        )
    """))

    # 4. Create backward-compatibility view so any missed references still work
    conn.execute(text(
        "CREATE VIEW organization_memberships AS SELECT * FROM org_members"
    ))


def downgrade() -> None:
    conn = op.get_bind()

    # 1. Drop the compat view
    conn.execute(text("DROP VIEW IF EXISTS organization_memberships"))

    # 2. Drop RLS policy on new name
    conn.execute(text("DROP POLICY IF EXISTS org_isolation ON org_members"))

    # 3. Rename back
    op.rename_table("org_members", "organization_memberships")

    # 4. Restore RLS on original name
    conn.execute(text("ALTER TABLE organization_memberships ENABLE ROW LEVEL SECURITY"))
    conn.execute(text("ALTER TABLE organization_memberships FORCE ROW LEVEL SECURITY"))
    conn.execute(text("""
        CREATE POLICY org_isolation ON organization_memberships
        FOR ALL
        USING (
            organization_id::text = COALESCE(
                NULLIF(current_setting('app.current_org_id', true), ''),
                '00000000-0000-0000-0000-000000000000'
            )
        )
    """))
