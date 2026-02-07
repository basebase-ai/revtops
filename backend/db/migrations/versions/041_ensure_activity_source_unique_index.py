"""Ensure unique partial index on activities(organization_id, source_system, source_id).

Revision ID: 041
Revises: 040
Create Date: 2026-02-07
"""
from alembic import op

revision = "041"
down_revision = "040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_activities_org_source_id")
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_activities_org_source
        ON activities (organization_id, source_system, source_id)
        WHERE source_id IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_activities_org_source")
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_activities_org_source_id
        ON activities (organization_id, source_system, source_id)
    """)
