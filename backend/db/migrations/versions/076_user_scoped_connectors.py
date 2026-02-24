"""Add sharing flags to integrations (Phase 1 - backwards compatible).

This migration adds new sharing columns while keeping the scope column intact.
Old clients continue to work. A future migration (Phase 2) will drop scope
after all clients are updated.

Changes:
- Add share_synced_data, share_query_access, share_write_access boolean columns
- Add pending_sharing_config flag for post-OAuth configuration flow
- Backfill user_id from connected_by_user_id for existing org-scoped integrations
- Backfill sharing flags based on old scope values

NOTE: scope column is NOT dropped - old clients still reference it.

Revision ID: 076_user_scoped_connectors
Revises: 075_drop_org_memberships_view
Create Date: 2026-02-23
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision: str = "076_user_scoped_connectors"
down_revision: Union[str, None] = "075_drop_org_memberships_view"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add new sharing columns with defaults (keeps server_default for new inserts)
    op.add_column(
        "integrations",
        sa.Column("share_synced_data", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "integrations",
        sa.Column("share_query_access", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "integrations",
        sa.Column("share_write_access", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "integrations",
        sa.Column("pending_sharing_config", sa.Boolean(), nullable=False, server_default="false"),
    )

    conn = op.get_bind()

    # 2. Backfill sharing flags based on old scope
    # Org-scoped integrations get share_synced_data=true (preserves existing behavior)
    conn.execute(
        text("""
            UPDATE integrations
            SET share_synced_data = true
            WHERE scope = 'organization'
        """)
    )

    # 3. Backfill user_id from connected_by_user_id for org-scoped integrations
    conn.execute(
        text("""
            UPDATE integrations
            SET user_id = connected_by_user_id
            WHERE scope = 'organization' AND user_id IS NULL AND connected_by_user_id IS NOT NULL
        """)
    )

    # 4. For orphaned integrations (no connected_by_user_id), assign to any user in the same org
    conn.execute(
        text("""
            UPDATE integrations i
            SET user_id = (
                SELECT u.id FROM users u 
                WHERE u.organization_id = i.organization_id 
                ORDER BY u.created_at ASC NULLS LAST
                LIMIT 1
            ),
            is_active = false,
            last_error = 'Migration: requires reconnection (auto-assigned owner)'
            WHERE i.user_id IS NULL AND scope = 'organization'
        """)
    )

    # NOTE: We do NOT drop the scope column or make user_id NOT NULL yet.
    # This keeps old clients working. Phase 2 migration will do cleanup.


def downgrade() -> None:
    conn = op.get_bind()

    # Clear user_id for integrations that were org-scoped (inferred from share_synced_data)
    conn.execute(
        text("""
            UPDATE integrations
            SET user_id = NULL
            WHERE share_synced_data = true AND scope = 'organization'
        """)
    )

    # Drop new columns
    op.drop_column("integrations", "pending_sharing_config")
    op.drop_column("integrations", "share_write_access")
    op.drop_column("integrations", "share_query_access")
    op.drop_column("integrations", "share_synced_data")
