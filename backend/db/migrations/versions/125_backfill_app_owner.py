"""Backfill apps.user_id to the user who initiated the creating turn.

Revision ID: 125_backfill_app_owner
Revises: 124_convo_msg_visibility
Create Date: 2026-04-05
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "125_backfill_app_owner"
down_revision: Union[str, None] = "124_convo_msg_visibility"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    # Prefer the user message that immediately preceded the assistant message
    # associated with app creation. If unavailable, fall back to assistant user_id.
    bind.execute(
        sa.text(
            """
            WITH candidate AS (
                SELECT
                    a.id AS app_id,
                    COALESCE(
                        (
                            SELECT um.user_id
                            FROM chat_messages um
                            WHERE um.conversation_id = a.conversation_id
                              AND um.role = 'user'
                              AND um.user_id IS NOT NULL
                              AND (
                                  am.created_at IS NULL
                                  OR um.created_at IS NULL
                                  OR um.created_at <= am.created_at
                              )
                            ORDER BY um.created_at DESC NULLS LAST, um.id DESC
                            LIMIT 1
                        ),
                        am.user_id
                    ) AS resolved_user_id
                FROM apps a
                LEFT JOIN chat_messages am
                    ON a.message_id ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
                   AND am.id = a.message_id::uuid
                WHERE a.message_id IS NOT NULL
            )
            UPDATE apps a
            SET user_id = candidate.resolved_user_id
            FROM candidate
            WHERE a.id = candidate.app_id
              AND candidate.resolved_user_id IS NOT NULL
              AND a.user_id IS DISTINCT FROM candidate.resolved_user_id
            """
        )
    )


def downgrade() -> None:
    # Irreversible data backfill.
    pass
