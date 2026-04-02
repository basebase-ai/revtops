"""Add shared_files RLS with per-user + team-sharing visibility.

Revision ID: 120_shared_files_rls
Revises: 119_audit_retention
Create Date: 2026-03-31
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = "120_shared_files_rls"
down_revision = "119_audit_retention"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE shared_files ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE shared_files FORCE ROW LEVEL SECURITY")

    op.execute("DROP POLICY IF EXISTS org_isolation ON shared_files")
    op.execute("DROP POLICY IF EXISTS shared_files_access ON shared_files")

    # Read access:
    # 1) same org + owner rows
    # 2) same org + source owner's integration has share_synced_data=true
    op.execute(
        """
        CREATE POLICY shared_files_access ON shared_files
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
                      AND i.is_active = TRUE
                      AND i.share_synced_data = TRUE
                )
            )
        )
        """
    )

    # Write access is org-scoped. Application-layer checks ensure callers only
    # write rows for the correct user owner.
    op.execute(
        """
        CREATE POLICY shared_files_write ON shared_files
        FOR INSERT
        WITH CHECK (
            organization_id::text = COALESCE(
                NULLIF(current_setting('app.current_org_id', true), ''),
                '00000000-0000-0000-0000-000000000000'
            )
        )
        """
    )
    op.execute(
        """
        CREATE POLICY shared_files_update ON shared_files
        FOR UPDATE
        USING (
            organization_id::text = COALESCE(
                NULLIF(current_setting('app.current_org_id', true), ''),
                '00000000-0000-0000-0000-000000000000'
            )
        )
        WITH CHECK (
            organization_id::text = COALESCE(
                NULLIF(current_setting('app.current_org_id', true), ''),
                '00000000-0000-0000-0000-000000000000'
            )
        )
        """
    )
    op.execute(
        """
        CREATE POLICY shared_files_delete ON shared_files
        FOR DELETE
        USING (
            organization_id::text = COALESCE(
                NULLIF(current_setting('app.current_org_id', true), ''),
                '00000000-0000-0000-0000-000000000000'
            )
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS shared_files_delete ON shared_files")
    op.execute("DROP POLICY IF EXISTS shared_files_update ON shared_files")
    op.execute("DROP POLICY IF EXISTS shared_files_write ON shared_files")
    op.execute("DROP POLICY IF EXISTS shared_files_access ON shared_files")

    op.execute(
        """
        CREATE POLICY org_isolation ON shared_files
        FOR ALL
        USING (
            organization_id::text = COALESCE(
                NULLIF(current_setting('app.current_org_id', true), ''),
                '00000000-0000-0000-0000-000000000000'
            )
        )
        """
    )
