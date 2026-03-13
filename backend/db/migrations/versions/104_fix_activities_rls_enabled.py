"""Re-enable RLS on activities table.

Migration 014 originally enabled RLS on activities, but it was found
disabled in production (relrowsecurity=false). The RLS policy
(activity_isolation) existed but was inactive, causing cross-org and
cross-user data leakage for all activities (emails, meetings, etc.).

This migration re-enables RLS to restore tenant isolation.

Revision ID: 104_fix_activities_rls_enabled
Revises: 103_drop_slack_bot_installs
Create Date: 2026-03-13
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

revision: str = "104_fix_activities_rls_enabled"
down_revision: Union[str, None] = "103_drop_slack_bot_installs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("ALTER TABLE activities ENABLE ROW LEVEL SECURITY"))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("ALTER TABLE activities DISABLE ROW LEVEL SECURITY"))
