"""Fix users RLS policy to not leak NULL organization_id users.

The original policy allowed access to users with NULL organization_id
(for onboarding), but this caused data leakage when the agent queries
users - it would return users from all organizations who haven't been
assigned to an org yet.

This migration tightens the policy to ONLY allow access to users
that match the current organization context.

Revision ID: 037
Revises: 036
Create Date: 2026-02-05
"""
from alembic import op
from sqlalchemy import text

revision = '037'
down_revision = '036'
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    
    # Drop existing policy
    conn.execute(text('DROP POLICY IF EXISTS org_isolation ON users'))
    
    # Create stricter policy - only users matching current org context
    # No longer allows NULL organization_id access
    conn.execute(text('''
        CREATE POLICY org_isolation ON users
        FOR ALL
        USING (
            organization_id::text = COALESCE(
                NULLIF(current_setting('app.current_org_id', true), ''),
                '00000000-0000-0000-0000-000000000000'
            )
        )
    '''))
    print("Updated users RLS policy (removed NULL org_id exception)")


def downgrade() -> None:
    conn = op.get_bind()
    
    # Restore original policy that allowed NULL org_id
    conn.execute(text('DROP POLICY IF EXISTS org_isolation ON users'))
    
    conn.execute(text('''
        CREATE POLICY org_isolation ON users
        FOR ALL
        USING (
            organization_id IS NULL 
            OR organization_id::text = COALESCE(
                NULLIF(current_setting('app.current_org_id', true), ''),
                '00000000-0000-0000-0000-000000000000'
            )
        )
    '''))
