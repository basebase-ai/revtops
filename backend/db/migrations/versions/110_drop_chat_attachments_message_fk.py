"""Drop FK constraint on chat_attachments.message_id.

Revision ID: 110_drop_chat_attachments_message_fk
Revises: 109_grant_chat_attachments
Create Date: 2026-03-19

Attachments are persisted before the chat_message row exists (message save
is fire-and-forget). The FK causes an IntegrityError. conversation_id FK
still handles cascade deletes; message_id is kept as an indexed column for
lookup only.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "110_drop_chat_attachments_message_fk"
down_revision: Union[str, None] = "109_grant_chat_attachments"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(
        "chat_attachments_message_id_fkey",
        "chat_attachments",
        type_="foreignkey",
    )


def downgrade() -> None:
    op.create_foreign_key(
        "chat_attachments_message_id_fkey",
        "chat_attachments",
        "chat_messages",
        ["message_id"],
        ["id"],
        ondelete="CASCADE",
    )
