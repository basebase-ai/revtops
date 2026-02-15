"""Multi-level agent context: evolve user_memories to memories with entity scoping,
add structured job fields to organization_memberships, add phone_number to users.

Revision ID: 059_multi_level_agent_context
Revises: 058_add_command
Create Date: 2026-02-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import UUID

revision: str = "059_multi_level_agent_context"
down_revision: Union[str, None] = "058_add_command"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. users: add phone_number ──────────────────────────────────────
    op.add_column("users", sa.Column("phone_number", sa.String(30), nullable=True))

    # ── 2. organization_memberships: add title + reports_to ─────────────
    op.add_column(
        "organization_memberships",
        sa.Column("title", sa.String(255), nullable=True),
    )
    op.add_column(
        "organization_memberships",
        sa.Column(
            "reports_to_membership_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organization_memberships.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_org_memberships_reports_to",
        "organization_memberships",
        ["reports_to_membership_id"],
    )

    # ── 3. Rename user_memories -> memories and evolve schema ───────────
    op.rename_table("user_memories", "memories")

    # Add new columns with permissive defaults for backfill
    op.add_column("memories", sa.Column("entity_type", sa.String(30), nullable=True))
    op.add_column("memories", sa.Column("entity_id", UUID(as_uuid=True), nullable=True))
    op.add_column("memories", sa.Column("category", sa.String(50), nullable=True))
    op.add_column(
        "memories",
        sa.Column(
            "created_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL", onupdate="CASCADE"),
            nullable=True,
        ),
    )
    op.add_column(
        "memories",
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
    )

    # Backfill existing rows: entity_type='user', entity_id=user_id, created_by_user_id=user_id
    conn = op.get_bind()
    conn.execute(
        text(
            """
            UPDATE memories
            SET entity_type = 'user',
                entity_id = user_id,
                created_by_user_id = user_id,
                updated_at = created_at
            WHERE entity_type IS NULL
            """
        )
    )

    # Now enforce NOT NULL on entity_type and entity_id
    op.alter_column("memories", "entity_type", nullable=False)
    op.alter_column("memories", "entity_id", nullable=False)

    # Drop the old user_id column (replaced by entity_type/entity_id)
    op.drop_index("idx_user_memories_user_id", table_name="memories")
    op.drop_column("memories", "user_id")

    # Add composite index for entity lookups
    op.create_index("ix_memories_entity", "memories", ["entity_type", "entity_id"])

    # ── 4. RLS policy for memories (was on user_memories) ───────────────
    # The table rename carries the existing RLS settings (if any), but
    # user_memories did not have RLS originally. Enable it now.
    conn.execute(text("ALTER TABLE memories ENABLE ROW LEVEL SECURITY"))
    conn.execute(text("ALTER TABLE memories FORCE ROW LEVEL SECURITY"))
    conn.execute(text("DROP POLICY IF EXISTS memories_org_isolation ON memories"))
    conn.execute(
        text(
            """
            CREATE POLICY memories_org_isolation ON memories
            FOR ALL
            USING (
                organization_id::text = COALESCE(
                    NULLIF(current_setting('app.current_org_id', true), ''),
                    '00000000-0000-0000-0000-000000000000'
                )
            )
            """
        )
    )


def downgrade() -> None:
    conn = op.get_bind()

    # ── Reverse RLS on memories ─────────────────────────────────────────
    conn.execute(text("DROP POLICY IF EXISTS memories_org_isolation ON memories"))
    conn.execute(text("ALTER TABLE memories NO FORCE ROW LEVEL SECURITY"))
    conn.execute(text("ALTER TABLE memories DISABLE ROW LEVEL SECURITY"))

    # ── Reverse memories -> user_memories ───────────────────────────────
    op.drop_index("ix_memories_entity", table_name="memories")

    # Re-add user_id column and backfill from entity_id where entity_type='user'
    op.add_column(
        "memories",
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE", onupdate="CASCADE"),
            nullable=True,
        ),
    )
    conn.execute(
        text("UPDATE memories SET user_id = entity_id WHERE entity_type = 'user'")
    )
    # Delete non-user memories that can't fit in old schema
    conn.execute(text("DELETE FROM memories WHERE entity_type != 'user'"))
    op.alter_column("memories", "user_id", nullable=False)
    op.create_index("idx_user_memories_user_id", "memories", ["user_id"])

    op.drop_column("memories", "updated_at")
    op.drop_column("memories", "created_by_user_id")
    op.drop_column("memories", "category")
    op.drop_column("memories", "entity_id")
    op.drop_column("memories", "entity_type")

    op.rename_table("memories", "user_memories")

    # ── Reverse organization_memberships changes ────────────────────────
    op.drop_index("ix_org_memberships_reports_to", table_name="organization_memberships")
    op.drop_column("organization_memberships", "reports_to_membership_id")
    op.drop_column("organization_memberships", "title")

    # ── Reverse users changes ───────────────────────────────────────────
    op.drop_column("users", "phone_number")
