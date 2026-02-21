"""Add temp_data table for agent-computed results.

Flexible JSONB storage for interim/computed outputs (deal scores, churn
risk, engagement grades, etc.) that agents and workflows produce.  Rows
are optionally linked to existing entities via soft entity_type/entity_id
references.

Revision ID: 063_add_temp_data
Revises: 062_add_bulk_operations
Create Date: 2026-02-16
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "063_add_temp_data"
down_revision = "062_add_bulk_operations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "temp_data",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Soft entity reference (no FK constraint — can point to any table)
        sa.Column("entity_type", sa.Text, nullable=True),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=True),
        # Namespace + key for structured lookups
        sa.Column("namespace", sa.Text, nullable=False),
        sa.Column("key", sa.Text, nullable=True),
        # Payload
        sa.Column("value", postgresql.JSONB, nullable=False),
        sa.Column("metadata", postgresql.JSONB, nullable=True),
        # Provenance
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        # Optional TTL
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Primary query pattern: "all results in namespace X for entity Y"
    op.create_index(
        "ix_temp_data_ns_entity",
        "temp_data",
        ["organization_id", "namespace", "entity_id"],
    )

    # "All computed data about entity X"
    op.create_index(
        "ix_temp_data_entity",
        "temp_data",
        ["organization_id", "entity_type", "entity_id"],
    )

    # Cleanup queries on expires_at
    op.create_index(
        "ix_temp_data_expires",
        "temp_data",
        ["expires_at"],
    )

    # --- RLS policy ---
    op.execute("ALTER TABLE temp_data ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY temp_data_org_isolation ON temp_data
        FOR ALL
        USING (organization_id = current_setting('app.current_org_id')::uuid)
    """)

    op.execute("GRANT ALL ON temp_data TO revtops_app")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS temp_data_org_isolation ON temp_data")

    op.drop_index("ix_temp_data_expires", table_name="temp_data")
    op.drop_index("ix_temp_data_entity", table_name="temp_data")
    op.drop_index("ix_temp_data_ns_entity", table_name="temp_data")
    op.drop_table("temp_data")
