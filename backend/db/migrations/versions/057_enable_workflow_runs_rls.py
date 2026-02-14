"""Enable RLS on workflow_runs for tenant-safe workflow execution history reads.

Revision ID: 057_enable_workflow_runs_rls
Revises: 056_workflow_runs_read_access
Create Date: 2026-02-13
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = "057_enable_workflow_runs_rls"
down_revision: Union[str, None] = "056_workflow_runs_read_access"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # Match existing multi-tenant tables: enforce org isolation via app.current_org_id.
    conn.execute(text("ALTER TABLE workflow_runs ENABLE ROW LEVEL SECURITY"))
    conn.execute(text("ALTER TABLE workflow_runs FORCE ROW LEVEL SECURITY"))
    conn.execute(text("DROP POLICY IF EXISTS workflow_runs_org_isolation ON workflow_runs"))
    conn.execute(
        text(
            """
            CREATE POLICY workflow_runs_org_isolation ON workflow_runs
            FOR ALL
            USING (
                organization_id::text = COALESCE(
                    NULLIF(current_setting('app.current_org_id', true), ''),
                    '00000000-0000-0000-0000-000000000000'
                )
            )
            """
        )
    )


def downgrade() -> None:
    conn = op.get_bind()

    conn.execute(text("DROP POLICY IF EXISTS workflow_runs_org_isolation ON workflow_runs"))
    conn.execute(text("ALTER TABLE workflow_runs DISABLE ROW LEVEL SECURITY"))
