"""Add embedding and embedding_message_count to conversations for workstream clustering.

Revision ID: 111_add_conversation_embeddings
Revises: 110_drop_chat_attachments_message_fk
Create Date: 2026-03-19

Semantic workstream Home: conversations get a vector representation of their
content (title + summary + recent messages) and a counter for staleness.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "111_add_conversation_embeddings"
down_revision: Union[str, None] = "110_drop_chat_attachments_message_fk"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("ALTER TABLE conversations ADD COLUMN embedding vector(1536)")
    op.add_column(
        "conversations",
        sa.Column("embedding_message_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_conversations_embedding_hnsw ON conversations "
        "USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_conversations_embedding_hnsw")
    op.drop_column("conversations", "embedding_message_count")
    op.drop_column("conversations", "embedding")
