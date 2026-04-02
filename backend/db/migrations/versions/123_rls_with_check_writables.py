"""Add explicit WITH CHECK to RLS on agent-writable tenant tables.

Revision ID: 123_rls_with_check_writables
Revises: 122_tighten_conversations_chat_rls
Create Date: 2026-03-31
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "123_rls_with_check_writables"
down_revision: Union[str, None] = "122_tighten_conversations_chat_rls"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_STD_ORG: str = """
organization_id::text = COALESCE(
    NULLIF(current_setting('app.current_org_id', true), ''),
    '00000000-0000-0000-0000-000000000000'
)
"""

# (table_name, policy_name) — policy_name matches existing migrations
_WRITABLE_POLICIES: tuple[tuple[str, str], ...] = (
    ("workflows", "org_isolation"),
    ("artifacts", "org_isolation"),
    ("contacts", "org_isolation"),
    ("deals", "org_isolation"),
    ("accounts", "org_isolation"),
    ("org_members", "org_isolation"),
    ("temp_data", "temp_data_org_isolation"),
)

_TEMP_DATA_EXPR: str = (
    "organization_id = (current_setting('app.current_org_id', true))::uuid"
)


def upgrade() -> None:
    bind = op.get_bind()
    for table_name, policy_name in _WRITABLE_POLICIES:
        bind.execute(sa.text(f"DROP POLICY IF EXISTS {policy_name} ON {table_name}"))
        if table_name == "temp_data":
            bind.execute(
                sa.text(
                    f"""
                    CREATE POLICY {policy_name} ON {table_name}
                    FOR ALL
                    USING ({_TEMP_DATA_EXPR})
                    WITH CHECK ({_TEMP_DATA_EXPR})
                    """
                )
            )
        else:
            bind.execute(
                sa.text(
                    f"""
                    CREATE POLICY {policy_name} ON {table_name}
                    FOR ALL
                    USING ({_STD_ORG.strip()})
                    WITH CHECK ({_STD_ORG.strip()})
                    """
                )
            )


def downgrade() -> None:
    bind = op.get_bind()
    for table_name, policy_name in _WRITABLE_POLICIES:
        bind.execute(sa.text(f"DROP POLICY IF EXISTS {policy_name} ON {table_name}"))
        if table_name == "temp_data":
            bind.execute(
                sa.text(
                    f"""
                    CREATE POLICY {policy_name} ON {table_name}
                    FOR ALL
                    USING ({_TEMP_DATA_EXPR})
                    """
                )
            )
        else:
            bind.execute(
                sa.text(
                    f"""
                    CREATE POLICY {policy_name} ON {table_name}
                    FOR ALL
                    USING ({_STD_ORG.strip()})
                    """
                )
            )
