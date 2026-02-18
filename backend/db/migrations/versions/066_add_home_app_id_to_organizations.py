"""Add home_app_id to organizations for customizable Home tab.

Revision ID: 066_add_home_app_id
Revises: 065_create_apps_table
Create Date: 2026-02-17
"""

from alembic import op

revision = "066_add_home_app_id"
down_revision = "065_create_apps_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # This migration may be replayed in environments where the column was
    # already created; keep it idempotent.
    op.execute(
        """
        ALTER TABLE organizations
        ADD COLUMN IF NOT EXISTS home_app_id UUID
        """
    )

    # Ensure FK exists even when the column was created outside this migration.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'organizations_home_app_id_fkey'
                  AND conrelid = 'organizations'::regclass
            ) THEN
                ALTER TABLE organizations
                ADD CONSTRAINT organizations_home_app_id_fkey
                FOREIGN KEY (home_app_id)
                REFERENCES apps(id)
                ON DELETE SET NULL;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE organizations DROP CONSTRAINT IF EXISTS organizations_home_app_id_fkey")
    op.execute("ALTER TABLE organizations DROP COLUMN IF EXISTS home_app_id")
