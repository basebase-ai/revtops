"""Add context_tokens column to conversations table.

Revision ID: 093_add_context_tokens_to_conversations
Revises: 092_org_handle_not_null
Create Date: 2026-03-06

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "093_add_context_tokens_to_conversations"
down_revision: Union[str, None] = "092_org_handle_not_null"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("context_tokens", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("conversations", "context_tokens")
