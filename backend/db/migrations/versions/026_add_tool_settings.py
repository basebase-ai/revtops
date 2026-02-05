"""Add user_tool_settings table and conversation type column.

Part of the unified tools architecture:
- user_tool_settings: Stores per-user auto-approve settings for tools
- conversations.type: Distinguishes agent chats from workflow conversations

Revision ID: 026
Revises: 025
Create Date: 2026-02-02
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '026'
down_revision = '025'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create user_tool_settings table and add conversation type."""
    
    # Create user_tool_settings table
    op.create_table(
        'user_tool_settings',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('user_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('users.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('tool_name', sa.String(50), nullable=False),
        sa.Column('auto_approve', sa.Boolean, nullable=False, default=False),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime, nullable=False, server_default=sa.func.now(),
                  onupdate=sa.func.now()),
        # Unique constraint: one setting per user per tool
        sa.UniqueConstraint('user_id', 'tool_name', name='uq_user_tool_settings_user_tool'),
    )
    
    # Index for looking up user's settings
    op.create_index(
        'ix_user_tool_settings_user',
        'user_tool_settings',
        ['user_id']
    )
    
    # Add type column to conversations (agent vs workflow)
    op.add_column(
        'conversations',
        sa.Column('type', sa.String(20), nullable=False, server_default='agent')
    )
    
    # Add workflow_id to track which workflow triggered this conversation
    op.add_column(
        'conversations',
        sa.Column('workflow_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('workflows.id', ondelete='SET NULL'),
                  nullable=True)
    )
    
    # Index for finding workflow conversations
    op.create_index(
        'ix_conversations_type',
        'conversations',
        ['type']
    )
    
    op.create_index(
        'ix_conversations_workflow',
        'conversations',
        ['workflow_id'],
        postgresql_where=sa.text("workflow_id IS NOT NULL")
    )


def downgrade() -> None:
    """Remove user_tool_settings table and conversation type."""
    
    # Drop conversation columns and indexes
    op.drop_index('ix_conversations_workflow', table_name='conversations')
    op.drop_index('ix_conversations_type', table_name='conversations')
    op.drop_column('conversations', 'workflow_id')
    op.drop_column('conversations', 'type')
    
    # Drop user_tool_settings table
    op.drop_index('ix_user_tool_settings_user', table_name='user_tool_settings')
    op.drop_table('user_tool_settings')
