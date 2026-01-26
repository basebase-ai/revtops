"""Add workflows and workflow_runs tables.

Revision ID: 018_add_workflows
Revises: 017_add_pipelines
Create Date: 2026-01-26
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "018_add_workflows"
down_revision: Union[str, None] = "017_add_pipelines"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create workflows table
    op.create_table(
        "workflows",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column(
            "created_by_user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("trigger_type", sa.String(50), nullable=False),
        sa.Column(
            "trigger_config",
            sa.dialects.postgresql.JSONB,
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "steps",
            sa.dialects.postgresql.JSONB,
            nullable=False,
            server_default="[]",
        ),
        sa.Column("output_config", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("is_enabled", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("last_run_at", sa.DateTime, nullable=True),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column(
            "created_at", sa.DateTime, nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()
        ),
    )

    # Create indexes for workflows
    op.create_index(
        "ix_workflows_org_enabled",
        "workflows",
        ["organization_id", "is_enabled"],
    )
    op.create_index(
        "ix_workflows_trigger_type",
        "workflows",
        ["trigger_type"],
    )

    # Create workflow_runs table
    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workflow_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workflows.id"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column("triggered_by", sa.String(100), nullable=False),
        sa.Column("trigger_data", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column("steps_completed", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("output", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column(
            "started_at", sa.DateTime, nullable=False, server_default=sa.func.now()
        ),
        sa.Column("completed_at", sa.DateTime, nullable=True),
    )

    # Create indexes for workflow_runs
    op.create_index(
        "ix_workflow_runs_workflow_id",
        "workflow_runs",
        ["workflow_id"],
    )
    op.create_index(
        "ix_workflow_runs_org_status",
        "workflow_runs",
        ["organization_id", "status"],
    )
    op.create_index(
        "ix_workflow_runs_started_at",
        "workflow_runs",
        ["started_at"],
    )


def downgrade() -> None:
    # Drop indexes
    op.drop_index("ix_workflow_runs_started_at", table_name="workflow_runs")
    op.drop_index("ix_workflow_runs_org_status", table_name="workflow_runs")
    op.drop_index("ix_workflow_runs_workflow_id", table_name="workflow_runs")
    op.drop_index("ix_workflows_trigger_type", table_name="workflows")
    op.drop_index("ix_workflows_org_enabled", table_name="workflows")

    # Drop tables
    op.drop_table("workflow_runs")
    op.drop_table("workflows")
