"""Add cached message_count and last_message_preview to conversations.

Revision ID: 022_add_conversation_message_cache
Revises: 021_add_workflow_cascade_delete
Create Date: 2026-01-29

Denormalizes message count and last message preview onto conversations table
for faster list queries (avoids joins/aggregates at read time).
"""
from alembic import op
import sqlalchemy as sa

revision = '022_conv_msg_cache'
down_revision = '021_wf_cascade'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add cached columns
    op.add_column('conversations', sa.Column('message_count', sa.Integer(), server_default='0', nullable=False))
    op.add_column('conversations', sa.Column('last_message_preview', sa.String(200), nullable=True))

    # Backfill existing data
    op.execute('''
        UPDATE conversations c
        SET 
            message_count = (
                SELECT COUNT(*) FROM chat_messages cm WHERE cm.conversation_id = c.id
            ),
            last_message_preview = (
                SELECT LEFT(cm.content, 200)
                FROM chat_messages cm
                WHERE cm.conversation_id = c.id
                ORDER BY cm.created_at DESC
                LIMIT 1
            )
    ''')


def downgrade() -> None:
    op.drop_column('conversations', 'last_message_preview')
    op.drop_column('conversations', 'message_count')
