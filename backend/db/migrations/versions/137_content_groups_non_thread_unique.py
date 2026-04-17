"""Enforce non-thread content_group uniqueness.

Revision ID: 137_group_non_thread_uq
Revises: 136_group_summaries
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "137_group_non_thread_uq"
down_revision: Union[str, Sequence[str], None] = "136_group_summaries"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

assert len(revision) <= 32
if isinstance(down_revision, str):
    assert len(down_revision) <= 32


def upgrade() -> None:
    op.execute(
        """
        WITH ranked AS (
            SELECT
                id,
                ROW_NUMBER() OVER (
                    PARTITION BY organization_id, platform, workspace_id, external_group_id
                    ORDER BY updated_at DESC, created_at DESC, id DESC
                ) AS rn
            FROM content_groups
            WHERE external_thread_id IS NULL
        )
        DELETE FROM content_groups cg
        USING ranked r
        WHERE cg.id = r.id
          AND r.rn > 1
        """
    )

    op.create_index(
        "uq_content_groups_channel_non_thread",
        "content_groups",
        ["organization_id", "platform", "workspace_id", "external_group_id"],
        unique=True,
        postgresql_where=sa.text("external_thread_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_content_groups_channel_non_thread", table_name="content_groups")
