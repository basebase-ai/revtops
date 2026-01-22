"""Add pgvector extension and embedding column to activities.

Revision ID: 003_add_embeddings
Revises: 002_add_conversations
Create Date: 2026-01-22
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = '003_add_embeddings'
down_revision = '002_add_conversations'
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)
    
    # Check if embedding column already exists
    activity_columns = [col['name'] for col in inspector.get_columns('activities')]
    
    if 'embedding' not in activity_columns:
        # Add embedding column - stored as bytes (will be cast to vector for search)
        op.add_column(
            'activities',
            sa.Column('embedding', sa.LargeBinary(), nullable=True)
        )
        
    # Check if searchable_text column already exists  
    if 'searchable_text' not in activity_columns:
        # Add searchable_text column to store the text that was embedded
        op.add_column(
            'activities',
            sa.Column('searchable_text', sa.Text(), nullable=True)
        )
    
    # Note: Vector index creation is done separately via SQL after enabling pgvector extension
    # Run this manually in your database if you want vector indexing:
    #   CREATE EXTENSION IF NOT EXISTS vector;
    #   CREATE INDEX activities_embedding_idx ON activities 
    #     USING hnsw ((embedding::vector(1536)) vector_cosine_ops);
    print("Migration complete. For vector search, manually run:")
    print("  CREATE EXTENSION IF NOT EXISTS vector;")


def downgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)
    
    # Drop index
    try:
        conn.execute(text("DROP INDEX IF EXISTS activities_embedding_idx"))
    except Exception:
        pass
    
    # Check and drop columns
    activity_columns = [col['name'] for col in inspector.get_columns('activities')]
    
    if 'embedding' in activity_columns:
        op.drop_column('activities', 'embedding')
        
    if 'searchable_text' in activity_columns:
        op.drop_column('activities', 'searchable_text')
    
    # Don't drop the extension as other things might use it
