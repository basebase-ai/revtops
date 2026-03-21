"""Fix notifications RLS policy to allow cross-user INSERTs.

The original policy required both org_id AND user_id for ALL operations,
which prevented the system from creating notifications for mentioned users
(the session is authenticated as the sender, not the recipient).

Split into:
- SELECT/UPDATE/DELETE: org_id AND user_id (users see only their own)
- INSERT: org_id only (system can notify any user in the org)

Revision ID: 115_fix_notifications_rls_policy
Revises: 114_add_agent_responding_and_notifications
Create Date: 2026-03-21
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "115_fix_notifications_rls_policy"
down_revision: Union[str, None] = "114_add_agent_responding_and_notifications"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DROP POLICY IF EXISTS notifications_user_org ON notifications")

    op.execute("""
        CREATE POLICY notifications_select ON notifications
        FOR SELECT
        USING (
            organization_id::text = COALESCE(NULLIF(current_setting('app.current_org_id', true), ''), '00000000-0000-0000-0000-000000000000')
            AND user_id::text = COALESCE(NULLIF(current_setting('app.current_user_id', true), ''), '00000000-0000-0000-0000-000000000000')
        )
    """)

    op.execute("""
        CREATE POLICY notifications_insert ON notifications
        FOR INSERT
        WITH CHECK (
            organization_id::text = COALESCE(NULLIF(current_setting('app.current_org_id', true), ''), '00000000-0000-0000-0000-000000000000')
        )
    """)

    op.execute("""
        CREATE POLICY notifications_update ON notifications
        FOR UPDATE
        USING (
            organization_id::text = COALESCE(NULLIF(current_setting('app.current_org_id', true), ''), '00000000-0000-0000-0000-000000000000')
            AND user_id::text = COALESCE(NULLIF(current_setting('app.current_user_id', true), ''), '00000000-0000-0000-0000-000000000000')
        )
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS notifications_select ON notifications")
    op.execute("DROP POLICY IF EXISTS notifications_insert ON notifications")
    op.execute("DROP POLICY IF EXISTS notifications_update ON notifications")

    op.execute("""
        CREATE POLICY notifications_user_org ON notifications
        FOR ALL
        USING (
            organization_id::text = COALESCE(NULLIF(current_setting('app.current_org_id', true), ''), '00000000-0000-0000-0000-000000000000')
            AND user_id::text = COALESCE(NULLIF(current_setting('app.current_user_id', true), ''), '00000000-0000-0000-0000-000000000000')
        )
    """)
