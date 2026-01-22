"""Add conversations table and conversation_id to chat_messages.

Revision ID: 002_add_conversations
Revises: 001_initial
Create Date: 2026-01-21
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = '002_add_conversations'
down_revision = '001_initial'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Get connection to check existing tables/columns
    conn = op.get_bind()
    inspector = inspect(conn)
    existing_tables = inspector.get_table_names()
    
    # Create conversations table if it doesn't exist
    if 'conversations' not in existing_tables:
        op.create_table(
            'conversations',
            sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column('title', sa.String(255), nullable=True),
            sa.Column('summary', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id')
        )
        op.create_index('ix_conversations_user_id', 'conversations', ['user_id'])
        op.create_index('ix_conversations_updated_at', 'conversations', ['updated_at'])

    # Check if conversation_id column already exists in chat_messages
    chat_columns = [col['name'] for col in inspector.get_columns('chat_messages')]
    
    if 'conversation_id' not in chat_columns:
        # Add conversation_id column to chat_messages
        op.add_column(
            'chat_messages',
            sa.Column('conversation_id', postgresql.UUID(as_uuid=True), nullable=True)
        )
        op.create_foreign_key(
            'fk_chat_messages_conversation_id',
            'chat_messages',
            'conversations',
            ['conversation_id'],
            ['id'],
            ondelete='CASCADE'
        )
        op.create_index('ix_chat_messages_conversation_id', 'chat_messages', ['conversation_id'])


def downgrade() -> None:
    # Get connection to check existing tables/columns
    conn = op.get_bind()
    inspector = inspect(conn)
    
    # Check if conversation_id column exists before trying to remove it
    chat_columns = [col['name'] for col in inspector.get_columns('chat_messages')]
    
    if 'conversation_id' in chat_columns:
        op.drop_index('ix_chat_messages_conversation_id', table_name='chat_messages')
        op.drop_constraint('fk_chat_messages_conversation_id', 'chat_messages', type_='foreignkey')
        op.drop_column('chat_messages', 'conversation_id')

    # Drop conversations table if it exists
    existing_tables = inspector.get_table_names()
    if 'conversations' in existing_tables:
        op.drop_index('ix_conversations_updated_at', table_name='conversations')
        op.drop_index('ix_conversations_user_id', table_name='conversations')
        op.drop_table('conversations')
