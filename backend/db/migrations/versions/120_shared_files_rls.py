"""Enable RLS on shared_files with org + owner/team visibility.

Revision ID: 120_shared_files_rls
Revises: 119_audit_retention
Create Date: 2026-03-31
"""
from alembic import op
from sqlalchemy import text


revision = "120_shared_files_rls"
down_revision = "119_audit_retention"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    conn.execute(text("ALTER TABLE shared_files ENABLE ROW LEVEL SECURITY"))
    conn.execute(text("ALTER TABLE shared_files FORCE ROW LEVEL SECURITY"))

    conn.execute(text("DROP POLICY IF EXISTS shared_files_select_access ON shared_files"))
    conn.execute(text("DROP POLICY IF EXISTS shared_files_owner_write ON shared_files"))

    # SELECT policy:
    # - Always constrained to current org
    # - Owner can read their rows
    # - Teammates can read rows only when the corresponding integration has
    #   share_synced_data=true (preserves connector sharing behavior)
    conn.execute(
        text(
            """
            CREATE POLICY shared_files_select_access ON shared_files
            FOR SELECT
            USING (
                organization_id::text = COALESCE(
                    NULLIF(current_setting('app.current_org_id', true), ''),
                    '00000000-0000-0000-0000-000000000000'
                )
                AND (
                    user_id::text = COALESCE(
                        NULLIF(current_setting('app.current_user_id', true), ''),
                        '00000000-0000-0000-0000-000000000000'
                    )
                    OR EXISTS (
                        SELECT 1
                        FROM integrations i
                        WHERE i.organization_id = shared_files.organization_id
                          AND i.user_id = shared_files.user_id
                          AND i.connector = shared_files.source
                          AND i.is_active = true
                          AND i.share_synced_data = true
                    )
                )
            )
            """
        )
    )

    # Write policy:
    # - Only file owner in current org can insert/update/delete their rows
    conn.execute(
        text(
            """
            CREATE POLICY shared_files_owner_write ON shared_files
            FOR ALL
            USING (
                organization_id::text = COALESCE(
                    NULLIF(current_setting('app.current_org_id', true), ''),
                    '00000000-0000-0000-0000-000000000000'
                )
                AND user_id::text = COALESCE(
                    NULLIF(current_setting('app.current_user_id', true), ''),
                    '00000000-0000-0000-0000-000000000000'
                )
            )
            WITH CHECK (
                organization_id::text = COALESCE(
                    NULLIF(current_setting('app.current_org_id', true), ''),
                    '00000000-0000-0000-0000-000000000000'
                )
                AND user_id::text = COALESCE(
                    NULLIF(current_setting('app.current_user_id', true), ''),
                    '00000000-0000-0000-0000-000000000000'
                )
            )
            """
        )
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("DROP POLICY IF EXISTS shared_files_owner_write ON shared_files"))
    conn.execute(text("DROP POLICY IF EXISTS shared_files_select_access ON shared_files"))
    conn.execute(text("ALTER TABLE shared_files DISABLE ROW LEVEL SECURITY"))
