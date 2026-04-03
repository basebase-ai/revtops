"""Re-enable RLS on users/organizations; add RLS to remaining tenant tables.

Revision ID: 121_fix_rls_gaps
Revises: 120_shared_files_rls
Create Date: 2026-03-31
"""
from __future__ import annotations

from typing import Final, Sequence, Union

from alembic import op

revision: str = "121_fix_rls_gaps"
down_revision: Union[str, None] = "120_shared_files_rls"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ORG_MATCH_SQL: Final[str] = """
    organization_id::text = COALESCE(
        NULLIF(current_setting('app.current_org_id', true), ''),
        '00000000-0000-0000-0000-000000000000'
    )
"""

# Tables with organization_id; shared_files already covered by 120_shared_files_rls.
_ORG_SCOPED_TABLES: Final[tuple[str, ...]] = (
    "workflows",
    "workstreams",
    "workstream_snapshots",
    "change_sessions",
    "credit_transactions",
    "github_repositories",
    "github_commits",
    "github_pull_requests",
    "tracker_teams",
    "tracker_projects",
    "tracker_issues",
    "sheet_imports",
    "messenger_bot_installs",
    "messenger_user_mappings",
    "user_mappings_for_identity",
)


def upgrade() -> None:
    # Policies already exist from earlier migrations; RLS was off in production.
    op.execute("ALTER TABLE users ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE organizations ENABLE ROW LEVEL SECURITY")

    for table_name in _ORG_SCOPED_TABLES:
        op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS org_isolation ON {table_name}")
        op.execute(
            f"""
            CREATE POLICY org_isolation ON {table_name}
            FOR ALL
            USING (
                {_ORG_MATCH_SQL.strip()}
            )
            """
        )

    # Indirect: conversation must belong to current org (change_sessions RLS above).
    op.execute("ALTER TABLE chat_attachments ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE chat_attachments FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS org_isolation ON chat_attachments")
    op.execute(
        """
        CREATE POLICY org_isolation ON chat_attachments
        FOR ALL
        USING (
            conversation_id IN (
                SELECT id FROM conversations
                WHERE organization_id::text = COALESCE(
                    NULLIF(current_setting('app.current_org_id', true), ''),
                    '00000000-0000-0000-0000-000000000000'
                )
            )
        )
        """
    )

    op.execute("ALTER TABLE record_snapshots ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE record_snapshots FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS org_isolation ON record_snapshots")
    op.execute(
        """
        CREATE POLICY org_isolation ON record_snapshots
        FOR ALL
        USING (
            change_session_id IN (
                SELECT id FROM change_sessions
                WHERE organization_id::text = COALESCE(
                    NULLIF(current_setting('app.current_org_id', true), ''),
                    '00000000-0000-0000-0000-000000000000'
                )
            )
        )
        """
    )

    op.execute("ALTER TABLE user_tool_settings ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE user_tool_settings FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS user_isolation ON user_tool_settings")
    op.execute(
        """
        CREATE POLICY user_isolation ON user_tool_settings
        FOR ALL
        USING (
            user_id::text = COALESCE(
                NULLIF(current_setting('app.current_user_id', true), ''),
                '00000000-0000-0000-0000-000000000000'
            )
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS user_isolation ON user_tool_settings")
    op.execute("ALTER TABLE user_tool_settings DISABLE ROW LEVEL SECURITY")

    op.execute("DROP POLICY IF EXISTS org_isolation ON record_snapshots")
    op.execute("ALTER TABLE record_snapshots DISABLE ROW LEVEL SECURITY")

    op.execute("DROP POLICY IF EXISTS org_isolation ON chat_attachments")
    op.execute("ALTER TABLE chat_attachments DISABLE ROW LEVEL SECURITY")

    for table_name in reversed(_ORG_SCOPED_TABLES):
        op.execute(f"DROP POLICY IF EXISTS org_isolation ON {table_name}")
        op.execute(f"ALTER TABLE {table_name} DISABLE ROW LEVEL SECURITY")

    # Do not disable RLS on users / organizations (avoid restoring broken state).
