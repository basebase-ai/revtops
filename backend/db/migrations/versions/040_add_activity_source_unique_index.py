"""Add unique partial index on activities(organization_id, source_system, source_id).

Revision ID: 040
Revises: 039
Create Date: 2026-02-06

This enables INSERT ... ON CONFLICT DO NOTHING for real-time Slack message
persistence and prevents the hourly sync from creating duplicate rows.

Steps:
1. Remove duplicate activities (keep the earliest row per source key)
2. Drop the old non-unique index on the same columns
3. Create a unique partial index (WHERE source_id IS NOT NULL)
"""
from alembic import op

revision = "040"
down_revision = "039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Deduplicate existing rows: keep the row with the smallest id per
    #    (organization_id, source_system, source_id) group.
    op.execute("""
        DELETE FROM activities a
        USING activities b
        WHERE a.organization_id = b.organization_id
          AND a.source_system   = b.source_system
          AND a.source_id       = b.source_id
          AND a.source_id IS NOT NULL
          AND a.id > b.id
    """)

    # 2. Drop the old non-unique index (same columns, now redundant)
    op.drop_index("ix_activities_org_source_id", table_name="activities")

    # 3. Create unique partial index (NULLs in source_id are excluded)
    op.create_index(
        "uq_activities_org_source",
        "activities",
        ["organization_id", "source_system", "source_id"],
        unique=True,
        postgresql_where="source_id IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_index("uq_activities_org_source", table_name="activities")
    op.create_index(
        "ix_activities_org_source_id",
        "activities",
        ["organization_id", "source_system", "source_id"],
    )
