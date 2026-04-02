"""Restrict conversations/chat_messages RLS to user-visible rows.

Revision ID: 124_convo_msg_visibility
Revises: 123_rls_with_check_writables
Create Date: 2026-04-02
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "124_convo_msg_visibility"
down_revision: Union[str, None] = "123_rls_with_check_writables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ORG_MATCH: str = """
organization_id::text = COALESCE(
    NULLIF(current_setting('app.current_org_id', true), ''),
    '00000000-0000-0000-0000-000000000000'
)
"""

_USER_ID_TEXT: str = """
COALESCE(
    NULLIF(current_setting('app.current_user_id', true), ''),
    '00000000-0000-0000-0000-000000000000'
)
"""

_CONVERSATION_VISIBILITY: str = f"""
(
    scope = 'shared'
    OR user_id::text = ({_USER_ID_TEXT.strip()})
    OR EXISTS (
        SELECT 1
        FROM unnest(COALESCE(participating_user_ids, ARRAY[]::uuid[])) AS pu(uid)
        WHERE pu.uid::text = ({_USER_ID_TEXT.strip()})
    )
)
"""


def upgrade() -> None:
    bind = op.get_bind()

    bind.execute(sa.text("DROP POLICY IF EXISTS org_isolation ON conversations"))
    bind.execute(
        sa.text(
            f"""
            CREATE POLICY org_isolation ON conversations
            FOR ALL
            USING ({_ORG_MATCH.strip()} AND {_CONVERSATION_VISIBILITY.strip()})
            WITH CHECK ({_ORG_MATCH.strip()} AND {_CONVERSATION_VISIBILITY.strip()})
            """
        )
    )

    bind.execute(sa.text("DROP POLICY IF EXISTS org_isolation ON chat_messages"))
    bind.execute(
        sa.text(
            f"""
            CREATE POLICY org_isolation ON chat_messages
            FOR ALL
            USING (
                {_ORG_MATCH.strip()}
                AND EXISTS (
                    SELECT 1
                    FROM conversations c
                    WHERE c.id = chat_messages.conversation_id
                      AND c.organization_id::text = COALESCE(
                        NULLIF(current_setting('app.current_org_id', true), ''),
                        '00000000-0000-0000-0000-000000000000'
                      )
                      AND {_CONVERSATION_VISIBILITY.strip()}
                )
            )
            WITH CHECK (
                {_ORG_MATCH.strip()}
                AND EXISTS (
                    SELECT 1
                    FROM conversations c
                    WHERE c.id = chat_messages.conversation_id
                      AND c.organization_id::text = COALESCE(
                        NULLIF(current_setting('app.current_org_id', true), ''),
                        '00000000-0000-0000-0000-000000000000'
                      )
                      AND {_CONVERSATION_VISIBILITY.strip()}
                )
            )
            """
        )
    )


def downgrade() -> None:
    bind = op.get_bind()

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
