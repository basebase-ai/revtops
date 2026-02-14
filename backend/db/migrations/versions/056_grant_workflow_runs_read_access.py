"""Grant workflow execution tables read access to application role.

Revision ID: 056_workflow_runs_read_access
Revises: 055
Create Date: 2026-02-13
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = "056_workflow_runs_read_access"
down_revision: Union[str, None] = "055"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # Workflows execute under revtops_app (via get_session + SET ROLE).
    # Grant explicit SELECT access only when the role exists to avoid
    # failing local/dev migrations where the role may be absent.
    conn.execute(
        text(
            """
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'revtops_app') THEN
                    GRANT SELECT ON TABLE workflows TO revtops_app;
                    GRANT SELECT ON TABLE workflow_runs TO revtops_app;
                END IF;
            END
            $$;
            """
        )
    )


def downgrade() -> None:
    conn = op.get_bind()

    conn.execute(
        text(
            """
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'revtops_app') THEN
                    REVOKE SELECT ON TABLE workflow_runs FROM revtops_app;
                    REVOKE SELECT ON TABLE workflows FROM revtops_app;
                END IF;
            END
            $$;
            """
        )
    )
