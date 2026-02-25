"""Grant read access to identity user mappings for app role.

Revision ID: 077_usermap_read_grant
Revises: 076_user_scoped_connectors
Create Date: 2026-02-25
"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

revision: str = "077_usermap_read_grant"
down_revision: Union[str, None] = "076_user_scoped_connectors"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        text(
            """
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'revtops_app') THEN
                    GRANT SELECT ON TABLE user_mappings_for_identity TO revtops_app;
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
                    REVOKE SELECT ON TABLE user_mappings_for_identity FROM revtops_app;
                END IF;
            END
            $$;
            """
        )
    )
