"""Add conversation participants list.

Revision ID: 069_conv_participants
Revises: 068_global_cmd_memory
Create Date: 2026-02-18
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "069_conv_participants"
down_revision = "068_global_cmd_memory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column(
            "participating_user_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
            server_default=sa.text("'{}'::uuid[]"),
        ),
    )

    op.execute(
        """
        UPDATE conversations
        SET participating_user_ids = ARRAY[user_id]::uuid[]
        WHERE user_id IS NOT NULL
        """
    )

    op.alter_column(
        "conversations",
        "participating_user_ids",
        server_default=None,
    )

    op.create_index(
        "ix_conversations_participating_user_ids",
        "conversations",
        ["participating_user_ids"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_conversations_participating_user_ids", table_name="conversations")
    op.drop_column("conversations", "participating_user_ids")
