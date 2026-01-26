"""Add RLS to organizations table.

The organizations table is special - it doesn't have an organization_id 
column, it IS the organization. So the policy filters on id = current_org_id.

Revision ID: 016_add_organizations_rls
Revises: 015_add_org_id_to_conversations
Create Date: 2026-01-26
"""
from alembic import op
from sqlalchemy import text

revision = '016_add_organizations_rls'
down_revision = '015_add_org_id_to_conversations'
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    
    # Enable RLS on organizations table
    conn.execute(text('ALTER TABLE organizations ENABLE ROW LEVEL SECURITY'))
    conn.execute(text('ALTER TABLE organizations FORCE ROW LEVEL SECURITY'))
    
    conn.execute(text('DROP POLICY IF EXISTS org_isolation ON organizations'))
    
    # Policy: can only see your own organization (id matches session var)
    conn.execute(text('''
        CREATE POLICY org_isolation ON organizations
        FOR ALL
        USING (
            id::text = COALESCE(
                NULLIF(current_setting('app.current_org_id', true), ''),
                '00000000-0000-0000-0000-000000000000'
            )
        )
    '''))
    print("Enabled RLS on organizations")


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text('DROP POLICY IF EXISTS org_isolation ON organizations'))
    conn.execute(text('ALTER TABLE organizations DISABLE ROW LEVEL SECURITY'))
