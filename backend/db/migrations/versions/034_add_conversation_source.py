"""Add source tracking fields to conversations for Slack DM support.

Revision ID: 034
Revises: 033
Create Date: 2026-02-04

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '034'
down_revision = '033'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Make user_id nullable for Slack conversations (where we don't know the RevTops user)
    op.alter_column('conversations', 'user_id', nullable=True)
    
    # Also make user_id nullable on chat_messages for consistency
    op.alter_column('chat_messages', 'user_id', nullable=True)
    
    # Add source tracking fields
    op.add_column(
        'conversations',
        sa.Column('source', sa.String(20), server_default='web', nullable=False)
    )
    op.add_column(
        'conversations',
        sa.Column('source_channel_id', sa.String(100), nullable=True)
    )
    op.add_column(
        'conversations',
        sa.Column('source_user_id', sa.String(100), nullable=True)
    )
    
    # Index for finding conversations by Slack channel
    op.create_index(
        'ix_conversations_source_channel',
        'conversations',
        ['source', 'source_channel_id']
    )


def downgrade() -> None:
    op.drop_index('ix_conversations_source_channel', table_name='conversations')
    op.drop_column('conversations', 'source_user_id')
    op.drop_column('conversations', 'source_channel_id')
    op.drop_column('conversations', 'source')
    op.alter_column('conversations', 'user_id', nullable=False)
    op.alter_column('chat_messages', 'user_id', nullable=False)
