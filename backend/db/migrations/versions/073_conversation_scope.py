"""Add scope column to conversations for private/shared distinction.

Revision ID: 073_conversation_scope
Revises: 072_add_billing_and_credits
Create Date: 2026-02-22

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "073_conversation_scope"
down_revision: Union[str, None] = "072_add_billing_and_credits"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add scope column with default 'shared' for new conversations
    op.add_column(
        "conversations",
        sa.Column("scope", sa.String(20), nullable=False, server_default="shared"),
    )
    op.create_index("ix_conversations_scope", "conversations", ["scope"])

    # Backfill: existing single-participant web conversations -> private
    # Multi-participant or non-web conversations remain shared
    op.execute("""
        UPDATE conversations
        SET scope = 'private'
        WHERE source = 'web'
          AND (
              array_length(participating_user_ids, 1) = 1
              OR participating_user_ids IS NULL
              OR participating_user_ids = '{}'::uuid[]
          )
    """)


def downgrade() -> None:
    op.drop_index("ix_conversations_scope", table_name="conversations")
    op.drop_column("conversations", "scope")
