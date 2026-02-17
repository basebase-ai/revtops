"""Migrate activities.embedding from bytea to pgvector vector(1536).

Existing bytea embeddings are dropped (they will be regenerated on next sync).
Adds an HNSW index for fast cosine similarity search.

Revision ID: 065_migrate_embeddings_to_pgvector
Revises: 064_add_sandbox_id_to_conversations
Create Date: 2026-02-17
"""

from alembic import op
import sqlalchemy as sa

revision = "066_embeddings_to_pgvector"
down_revision = "065_create_apps_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Enable the pgvector extension (idempotent)
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # Drop the old bytea column (data will be regenerated on next sync)
    op.drop_column("activities", "embedding")

    # Re-create as a native pgvector column
    op.execute("ALTER TABLE activities ADD COLUMN embedding vector(1536)")

    # Add HNSW index for fast cosine similarity search
    op.execute(
        "CREATE INDEX ix_activities_embedding_hnsw ON activities "
        "USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_activities_embedding_hnsw")
    op.drop_column("activities", "embedding")
    op.add_column("activities", sa.Column("embedding", sa.LargeBinary(), nullable=True))
