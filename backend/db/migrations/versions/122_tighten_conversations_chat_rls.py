"""Backfill NULL org on conversations/chat_messages; tighten RLS (no NULL org leak).

Revision ID: 122_tighten_conversations_chat_rls
Revises: 121_fix_rls_gaps
Create Date: 2026-03-31
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "122_tighten_conversations_chat_rls"
down_revision: Union[str, None] = "121_fix_rls_gaps"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ORG_MATCH: str = """
organization_id::text = COALESCE(
    NULLIF(current_setting('app.current_org_id', true), ''),
    '00000000-0000-0000-0000-000000000000'
)
"""


def upgrade() -> None:
    bind = op.get_bind()

    # 1) Assign org to conversations from user's first active membership
    bind.execute(
        sa.text(
            """
            UPDATE conversations c
            SET organization_id = m.organization_id
            FROM (
                SELECT DISTINCT ON (om.user_id) om.user_id, om.organization_id
                FROM org_members om
                WHERE om.status IN ('active', 'onboarding', 'invited')
                ORDER BY om.user_id, om.joined_at NULLS LAST, om.created_at NULLS LAST
            ) m
            WHERE c.organization_id IS NULL
              AND c.user_id IS NOT NULL
              AND c.user_id = m.user_id
            """
        )
    )

    # 2) Propagate org from conversation to messages
    bind.execute(
        sa.text(
            """
            UPDATE chat_messages msg
            SET organization_id = c.organization_id
            FROM conversations c
            WHERE msg.conversation_id = c.id
              AND msg.organization_id IS NULL
              AND c.organization_id IS NOT NULL
            """
        )
    )

    # 3) Messages still NULL: use author's org membership
    bind.execute(
        sa.text(
            """
            UPDATE chat_messages msg
            SET organization_id = m.organization_id
            FROM (
                SELECT DISTINCT ON (om.user_id) om.user_id, om.organization_id
                FROM org_members om
                WHERE om.status IN ('active', 'onboarding', 'invited')
                ORDER BY om.user_id, om.joined_at NULLS LAST, om.created_at NULLS LAST
            ) m
            WHERE msg.organization_id IS NULL
              AND msg.user_id IS NOT NULL
              AND msg.user_id = m.user_id
            """
        )
    )

    # 4) Remove rows that cannot be scoped (messages first; include msgs tied to unscoped convs)
    bind.execute(
        sa.text(
            """
            DELETE FROM chat_messages
            WHERE organization_id IS NULL
               OR conversation_id IN (
                   SELECT id FROM conversations WHERE organization_id IS NULL
               )
            """
        )
    )
    bind.execute(sa.text("DELETE FROM conversations WHERE organization_id IS NULL"))

    bind.execute(sa.text("DROP POLICY IF EXISTS org_isolation ON conversations"))
    bind.execute(
        sa.text(
            f"""
            CREATE POLICY org_isolation ON conversations
            FOR ALL
            USING ({_ORG_MATCH.strip()})
            WITH CHECK ({_ORG_MATCH.strip()})
            """
        )
    )

    bind.execute(sa.text("DROP POLICY IF EXISTS org_isolation ON chat_messages"))
    bind.execute(
        sa.text(
            f"""
            CREATE POLICY org_isolation ON chat_messages
            FOR ALL
            USING ({_ORG_MATCH.strip()})
            WITH CHECK ({_ORG_MATCH.strip()})
            """
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("DROP POLICY IF EXISTS org_isolation ON chat_messages"))
    bind.execute(
        sa.text(
            """
            CREATE POLICY org_isolation ON chat_messages
            FOR ALL
            USING (
                (organization_id IS NULL)
                OR (organization_id::text = COALESCE(
                    NULLIF(current_setting('app.current_org_id', true), ''),
                    '00000000-0000-0000-0000-000000000000'
                ))
            )
            """
        )
    )

    bind.execute(sa.text("DROP POLICY IF EXISTS org_isolation ON conversations"))
    bind.execute(
        sa.text(
            """
            CREATE POLICY org_isolation ON conversations
            FOR ALL
            USING (
                (organization_id IS NULL)
                OR (organization_id::text = COALESCE(
                    NULLIF(current_setting('app.current_org_id', true), ''),
                    '00000000-0000-0000-0000-000000000000'
                ))
            )
            """
        )
    )
