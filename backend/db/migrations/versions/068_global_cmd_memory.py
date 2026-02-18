"""Move user global commands into memories.

Revision ID: 068_global_cmd_memory
Revises: 067_artifact_user_id_nullable
Create Date: 2026-02-18
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision = "068_global_cmd_memory"
down_revision = "067_artifact_user_id_nullable"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    conn.execute(
        text(
            """
            UPDATE memories AS m
            SET
                content = u.agent_global_commands,
                created_by_user_id = u.id,
                updated_at = NOW()
            FROM users AS u
            WHERE u.organization_id IS NOT NULL
              AND u.agent_global_commands IS NOT NULL
              AND btrim(u.agent_global_commands) <> ''
              AND m.organization_id = u.organization_id
              AND m.entity_type = 'user'
              AND m.entity_id = u.id
              AND m.category = 'global_commands'
            """
        )
    )

    conn.execute(
        text(
            """
            INSERT INTO memories (
                id,
                entity_type,
                entity_id,
                organization_id,
                category,
                content,
                created_by_user_id,
                created_at,
                updated_at
            )
            SELECT
                gen_random_uuid(),
                'user',
                u.id,
                u.organization_id,
                'global_commands',
                u.agent_global_commands,
                u.id,
                NOW(),
                NOW()
            FROM users AS u
            WHERE u.organization_id IS NOT NULL
              AND u.agent_global_commands IS NOT NULL
              AND btrim(u.agent_global_commands) <> ''
              AND NOT EXISTS (
                  SELECT 1
                  FROM memories AS m
                  WHERE m.organization_id = u.organization_id
                    AND m.entity_type = 'user'
                    AND m.entity_id = u.id
                    AND m.category = 'global_commands'
              )
            """
        )
    )

    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS agent_global_commands")


def downgrade() -> None:
    op.add_column("users", sa.Column("agent_global_commands", sa.String(length=4000), nullable=True))

    conn = op.get_bind()
    conn.execute(
        text(
            """
            WITH latest_commands AS (
                SELECT DISTINCT ON (m.entity_id)
                    m.entity_id,
                    m.content
                FROM memories AS m
                WHERE m.entity_type = 'user'
                  AND m.category = 'global_commands'
                ORDER BY m.entity_id, m.updated_at DESC NULLS LAST, m.created_at DESC NULLS LAST
            )
            UPDATE users AS u
            SET agent_global_commands = lc.content
            FROM latest_commands AS lc
            WHERE u.id = lc.entity_id
            """
        )
    )
