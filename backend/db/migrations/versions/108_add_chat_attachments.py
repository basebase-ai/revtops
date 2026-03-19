"""Add chat_attachments table for persisted message file content.

Revision ID: 108_chat_attachments
Revises: 107_external_notes
Create Date: 2026-03-18

Stores uploaded file bytes keyed by message so attachments can be viewed
after the message is sent (GET /api/chat/attachments/:id).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "108_chat_attachments"
down_revision: Union[str, None] = "107_external_notes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chat_attachments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.String(512), nullable=False),
        sa.Column("mime_type", sa.String(255), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["chat_messages.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_chat_attachments_conversation_id",
        "chat_attachments",
        ["conversation_id"],
        unique=False,
    )
    op.create_index(
        "ix_chat_attachments_message_id",
        "chat_attachments",
        ["message_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_chat_attachments_message_id", table_name="chat_attachments")
    op.drop_index("ix_chat_attachments_conversation_id", table_name="chat_attachments")
    op.drop_table("chat_attachments")
