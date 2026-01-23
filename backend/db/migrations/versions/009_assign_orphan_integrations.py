"""Assign orphaned user-scoped integrations to org users

Revision ID: 009_assign_orphan_integrations
Revises: 008_fix_integration_scopes
Create Date: 2026-01-23

The previous migration set scope='user' but couldn't set user_id if
connected_by_user_id was NULL. This migration assigns those orphaned
integrations to the first user in each organization.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '009_assign_orphan_integrations'
down_revision: Union[str, None] = '008_fix_integration_scopes'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Assign orphaned user-scoped integrations (where user_id is NULL)
    # to the first user in each organization
    op.execute("""
        UPDATE integrations i
        SET user_id = (
            SELECT u.id FROM users u 
            WHERE u.organization_id = i.organization_id 
            ORDER BY u.created_at ASC NULLS LAST
            LIMIT 1
        )
        WHERE i.scope = 'user'
          AND i.user_id IS NULL
    """)


def downgrade() -> None:
    # Can't reliably undo this - would need to know which were originally NULL
    pass
