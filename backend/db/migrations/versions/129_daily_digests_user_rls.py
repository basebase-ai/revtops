"""Split daily_digests RLS: org-wide SELECT, user-scoped UPDATE/INSERT/DELETE.

Users can read all digests in their org but only modify their own rows.

Revision ID: 129_daily_digests_user_rls
Revises: 128_daily_team_summaries
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "129_daily_digests_user_rls"
down_revision: Union[str, None] = "128_daily_team_summaries"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ORG_MATCH: str = """
organization_id::text = COALESCE(
    NULLIF(current_setting('app.current_org_id', true), ''),
    '00000000-0000-0000-0000-000000000000'
)
""".strip()

_USER_MATCH: str = """
user_id::text = COALESCE(
    NULLIF(current_setting('app.current_user_id', true), ''),
    '00000000-0000-0000-0000-000000000000'
)
""".strip()


def upgrade() -> None:
    op.execute("DROP POLICY IF EXISTS daily_digests_org_isolation ON daily_digests")

    op.execute(f"""
        CREATE POLICY daily_digests_select ON daily_digests
        FOR SELECT
        USING ({_ORG_MATCH})
    """)

    op.execute(f"""
        CREATE POLICY daily_digests_insert ON daily_digests
        FOR INSERT
        WITH CHECK ({_ORG_MATCH} AND {_USER_MATCH})
    """)

    op.execute(f"""
        CREATE POLICY daily_digests_update ON daily_digests
        FOR UPDATE
        USING ({_ORG_MATCH} AND {_USER_MATCH})
    """)

    op.execute(f"""
        CREATE POLICY daily_digests_delete ON daily_digests
        FOR DELETE
        USING ({_ORG_MATCH} AND {_USER_MATCH})
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS daily_digests_select ON daily_digests")
    op.execute("DROP POLICY IF EXISTS daily_digests_insert ON daily_digests")
    op.execute("DROP POLICY IF EXISTS daily_digests_update ON daily_digests")
    op.execute("DROP POLICY IF EXISTS daily_digests_delete ON daily_digests")

    op.execute("""
        CREATE POLICY daily_digests_org_isolation ON daily_digests
        FOR ALL
        USING (organization_id = current_setting('app.current_org_id')::uuid)
    """)
