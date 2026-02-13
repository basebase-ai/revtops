"""Add goals table for revenue goals and quotas.

Revision ID: 053
Revises: 052
Create Date: 2026-02-12

Stores CRM goals/quotas/targets synced from HubSpot (and later Salesforce).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "053"
down_revision = "052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "goals",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("source_system", sa.String(50), nullable=False, server_default="hubspot"),
        sa.Column("source_id", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("target_amount", sa.Numeric(15, 2), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("owner_id", UUID(as_uuid=True), sa.ForeignKey("users.id", onupdate="CASCADE"), nullable=True),
        sa.Column("pipeline_id", UUID(as_uuid=True), sa.ForeignKey("pipelines.id"), nullable=True),
        sa.Column("goal_type", sa.String(50), nullable=True),
        sa.Column("custom_fields", JSONB, nullable=True),
        sa.Column("synced_at", sa.DateTime(), nullable=True),
        sa.Column("sync_status", sa.String(20), nullable=False, server_default="synced"),
    )
    op.create_index("idx_goals_organization", "goals", ["organization_id"])
    op.create_index("idx_goals_owner", "goals", ["owner_id"])
    op.create_index(
        "uq_goals_org_source",
        "goals",
        ["organization_id", "source_system", "source_id"],
        unique=True,
        postgresql_where=sa.text("source_id IS NOT NULL"),
    )

    # Enable RLS (matching the pattern from other tables)
    op.execute("ALTER TABLE goals ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY goals_org_isolation ON goals
        USING (organization_id::text = current_setting('app.current_org_id', true))
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS goals_org_isolation ON goals")
    op.drop_table("goals")
