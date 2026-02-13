"""Enable RLS policies on workflow tables.

Revision ID: 056_enable_rls_on_workflow_tables
Revises: 055_add_workflow_notes
Create Date: 2026-02-13
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision: str = "056_enable_rls_on_workflow_tables"
down_revision: Union[str, None] = "055_add_workflow_notes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_WORKFLOW_TABLES = ["workflows", "workflow_runs"]


def _table_has_org_id(conn, table: str) -> bool:
    result = conn.execute(
        text(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = :table_name
                  AND column_name = 'organization_id'
            )
            """
        ),
        {"table_name": table},
    )
    return bool(result.scalar())


def upgrade() -> None:
    conn = op.get_bind()

    for table in _WORKFLOW_TABLES:
        if not _table_has_org_id(conn, table):
            continue

        conn.execute(text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        conn.execute(text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
        conn.execute(text(f"DROP POLICY IF EXISTS org_isolation ON {table}"))

        conn.execute(
            text(
                f"""
                CREATE POLICY org_isolation ON {table}
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

    for table in _WORKFLOW_TABLES:
        conn.execute(text(f"DROP POLICY IF EXISTS org_isolation ON {table}"))
        conn.execute(text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))
