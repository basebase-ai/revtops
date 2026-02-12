"""Add auto_approve_permissions to workflows.

Revision ID: 053_add_workflow_auto_approve_permissions
Revises: 052_add_cascade_on_update_to_user_fks
Create Date: 2026-02-12
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "053_add_workflow_auto_approve_permissions"
down_revision = "052_add_cascade_on_update_to_user_fks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    workflow_columns = {c["name"] for c in inspector.get_columns("workflows")}

    if "auto_approve_permissions" not in workflow_columns:
        op.add_column(
            "workflows",
            sa.Column(
                "auto_approve_permissions",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    workflow_columns = {c["name"] for c in inspector.get_columns("workflows")}

    if "auto_approve_permissions" in workflow_columns:
        op.drop_column("workflows", "auto_approve_permissions")
