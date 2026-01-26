"""Add Row-Level Security (RLS) for multi-tenant isolation.

This migration enables PostgreSQL RLS to enforce organization isolation
at the database level, replacing fragile application-level SQL filtering.

How it works:
1. Enable RLS on all multi-tenant tables
2. Create policies that filter by organization_id
3. The application sets `app.current_org_id` session variable before queries
4. PostgreSQL automatically filters all queries to only show matching rows

Revision ID: 014_add_row_level_security
Revises: 013_add_agent_tasks
Create Date: 2026-01-26
"""
from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision = '014_add_row_level_security'
down_revision = '013_add_agent_tasks'
branch_labels = None
depends_on = None

# Tables that have organization_id column and need RLS
# Note: conversations and chat_messages use user_id instead - they're
# protected via the user relationship, not directly via RLS
MULTI_TENANT_TABLES = [
    'deals',
    'accounts', 
    'contacts',
    'activities',
    'integrations',
    'artifacts',
    'crm_operations',
    'agent_tasks',
]

# Users table is special - it has organization_id but we also need
# to allow users without an org (during onboarding)
USER_TABLES = ['users']


def upgrade() -> None:
    conn = op.get_bind()
    
    # Enable RLS on all multi-tenant tables
    for table in MULTI_TENANT_TABLES:
        # Check if table exists AND has organization_id column
        result = conn.execute(text(f"""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name = '{table}' AND column_name = 'organization_id'
            )
        """))
        if not result.scalar():
            print(f"Skipping {table}: table doesn't exist or lacks organization_id column")
            continue
            
        # Enable RLS (FORCE ensures it applies even to table owner)
        conn.execute(text(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY'))
        conn.execute(text(f'ALTER TABLE {table} FORCE ROW LEVEL SECURITY'))
        
        # Drop existing policy if it exists (for idempotency)
        conn.execute(text(f'DROP POLICY IF EXISTS org_isolation ON {table}'))
        
        # Create policy: only allow access when org_id matches session variable
        # If session variable is not set, current_setting returns empty string,
        # which won't match any UUID, so queries return no rows (safe default)
        conn.execute(text(f'''
            CREATE POLICY org_isolation ON {table}
            FOR ALL
            USING (
                organization_id::text = COALESCE(
                    NULLIF(current_setting('app.current_org_id', true), ''),
                    '00000000-0000-0000-0000-000000000000'
                )
            )
        '''))
        print(f"Enabled RLS on {table}")
    
    # Handle users table specially - allow access to own org OR users without org
    for table in USER_TABLES:
        result = conn.execute(text(f"""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name = '{table}' AND column_name = 'organization_id'
            )
        """))
        if not result.scalar():
            print(f"Skipping {table}: table doesn't exist or lacks organization_id column")
            continue
            
        conn.execute(text(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY'))
        conn.execute(text(f'ALTER TABLE {table} FORCE ROW LEVEL SECURITY'))
        
        conn.execute(text(f'DROP POLICY IF EXISTS org_isolation ON {table}'))
        
        # Users: allow if org matches OR user has no org (onboarding/waitlist)
        conn.execute(text(f'''
            CREATE POLICY org_isolation ON {table}
            FOR ALL
            USING (
                organization_id IS NULL 
                OR organization_id::text = COALESCE(
                    NULLIF(current_setting('app.current_org_id', true), ''),
                    '00000000-0000-0000-0000-000000000000'
                )
            )
        '''))
        print(f"Enabled RLS on {table} (with NULL org allowed)")


def downgrade() -> None:
    conn = op.get_bind()
    
    all_tables = MULTI_TENANT_TABLES + USER_TABLES
    
    for table in all_tables:
        result = conn.execute(
            text(f"SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = '{table}')")
        )
        if result.scalar():
            # Drop policy (ignore if doesn't exist)
            conn.execute(text(f'DROP POLICY IF EXISTS org_isolation ON {table}'))
            
            # Disable RLS
            try:
                conn.execute(text(f'ALTER TABLE {table} DISABLE ROW LEVEL SECURITY'))
            except Exception:
                pass  # Table might not have RLS enabled
