"""Enforce single global command memory per user.

Revision ID: 069_global_cmd_unique
Revises: 068_global_cmd_memory
Create Date: 2026-02-18
"""

from alembic import op
from sqlalchemy import text

revision = "069_global_cmd_unique"
down_revision = "068_global_cmd_memory"
branch_labels = None
depends_on = None


# Migration safety preflight checks
assert len(revision) <= 32
assert isinstance(down_revision, str) and len(down_revision) <= 32


def upgrade() -> None:
    conn = op.get_bind()

    # Keep only the latest command per (organization_id, entity_id) pair.
    conn.execute(
        text(
            """
            DELETE FROM memories AS m
            USING (
                SELECT id
                FROM (
                    SELECT
                        id,
                        row_number() OVER (
                            PARTITION BY organization_id, entity_id
                            ORDER BY updated_at DESC NULLS LAST, created_at DESC NULLS LAST, id DESC
                        ) AS row_num
                    FROM memories
                    WHERE entity_type = 'user'
                      AND category = 'global_commands'
                ) AS ranked
                WHERE ranked.row_num > 1
            ) AS duplicates
            WHERE m.id = duplicates.id
            """
        )
    )

    op.create_index(
        "ux_memories_user_global_commands",
        "memories",
        ["organization_id", "entity_id"],
        unique=True,
        postgresql_where=text("entity_type = 'user' AND category = 'global_commands'"),
    )


def downgrade() -> None:
    op.drop_index("ux_memories_user_global_commands", table_name="memories")
