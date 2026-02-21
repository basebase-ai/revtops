"""Add bulk_operations and bulk_operation_results tables.

Supports general-purpose parallel tool execution over large item lists
via Celery fan-out (e.g., enriching 14K contacts with web_search).

Revision ID: 062_add_bulk_operations
Revises: 061_add_unique_phone_number
Create Date: 2026-02-15
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "062_add_bulk_operations"
down_revision = "061_add_unique_phone_number"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- bulk_operations ---
    op.create_table(
        "bulk_operations",
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
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL", onupdate="CASCADE"),
            nullable=True,
        ),
        # Definition
        sa.Column("operation_name", sa.String(255), nullable=False),
        sa.Column("tool_name", sa.String(100), nullable=False),
        sa.Column("params_template", postgresql.JSONB, nullable=False),
        sa.Column("items_query", sa.Text, nullable=True),
        sa.Column("rate_limit_per_minute", sa.Integer, nullable=False, server_default="200"),
        # Context for WebSocket progress
        sa.Column("conversation_id", sa.String(255), nullable=True),
        sa.Column("tool_call_id", sa.String(255), nullable=True),
        # Progress
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("total_items", sa.Integer, nullable=False, server_default="0"),
        sa.Column("completed_items", sa.Integer, nullable=False, server_default="0"),
        sa.Column("succeeded_items", sa.Integer, nullable=False, server_default="0"),
        sa.Column("failed_items", sa.Integer, nullable=False, server_default="0"),
        # Celery
        sa.Column("celery_task_id", sa.String(255), nullable=True),
        # Timestamps
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        # Error
        sa.Column("error", sa.Text, nullable=True),
    )

    op.create_index("ix_bulk_operations_org", "bulk_operations", ["organization_id"])
    op.create_index(
        "ix_bulk_operations_status",
        "bulk_operations",
        ["organization_id", "status"],
    )

    # --- bulk_operation_results ---
    op.create_table(
        "bulk_operation_results",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "bulk_operation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("bulk_operations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("item_index", sa.Integer, nullable=False),
        sa.Column("item_data", postgresql.JSONB, nullable=False),
        sa.Column("result_data", postgresql.JSONB, nullable=True),
        sa.Column("success", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_index(
        "ix_bulk_op_results_operation",
        "bulk_operation_results",
        ["bulk_operation_id"],
    )
    op.create_index(
        "ix_bulk_op_results_operation_index",
        "bulk_operation_results",
        ["bulk_operation_id", "item_index"],
        unique=True,
    )

    # --- RLS policies ---
    op.execute(
        "ALTER TABLE bulk_operations ENABLE ROW LEVEL SECURITY"
    )
    op.execute("""
        CREATE POLICY bulk_operations_org_isolation ON bulk_operations
        FOR ALL
        USING (organization_id = current_setting('app.current_org_id')::uuid)
    """)

    op.execute(
        "ALTER TABLE bulk_operation_results ENABLE ROW LEVEL SECURITY"
    )
    op.execute("""
        CREATE POLICY bulk_operation_results_org_isolation ON bulk_operation_results
        FOR ALL
        USING (
            bulk_operation_id IN (
                SELECT id FROM bulk_operations
                WHERE organization_id = current_setting('app.current_org_id')::uuid
            )
        )
    """)

    # Grant access to the app role
    op.execute("GRANT ALL ON bulk_operations TO revtops_app")
    op.execute("GRANT ALL ON bulk_operation_results TO revtops_app")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS bulk_operation_results_org_isolation ON bulk_operation_results")
    op.execute("DROP POLICY IF EXISTS bulk_operations_org_isolation ON bulk_operations")

    op.drop_index("ix_bulk_op_results_operation_index", table_name="bulk_operation_results")
    op.drop_index("ix_bulk_op_results_operation", table_name="bulk_operation_results")
    op.drop_table("bulk_operation_results")

    op.drop_index("ix_bulk_operations_status", table_name="bulk_operations")
    op.drop_index("ix_bulk_operations_org", table_name="bulk_operations")
    op.drop_table("bulk_operations")
