"""Add agent_tasks table for background task tracking.

Revision ID: 013_add_agent_tasks
Revises: 012_add_content_blocks
Create Date: 2026-01-23
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = '013_add_agent_tasks'
down_revision = '012_add_content_blocks'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Get connection to check existing tables
    conn = op.get_bind()
    inspector = inspect(conn)
    existing_tables = inspector.get_table_names()

    # Create agent_tasks table if it doesn't exist
    if 'agent_tasks' not in existing_tables:
        op.create_table(
            'agent_tasks',
            sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column('conversation_id', postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column('status', sa.String(20), nullable=False, server_default='running'),
            sa.Column('user_message', sa.Text(), nullable=False),
            sa.Column('output_chunks', postgresql.JSONB(), nullable=False, server_default='[]'),
            sa.Column('error_message', sa.Text(), nullable=True),
            sa.Column('started_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column('completed_at', sa.DateTime(), nullable=True),
            sa.Column('last_activity_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id')
        )
        # Create indexes for common queries
        op.create_index('ix_agent_tasks_user_status', 'agent_tasks', ['user_id', 'status'])
        op.create_index('ix_agent_tasks_conversation', 'agent_tasks', ['conversation_id'])
        op.create_index('ix_agent_tasks_org_status', 'agent_tasks', ['organization_id', 'status'])


def downgrade() -> None:
    # Get connection to check existing tables
    conn = op.get_bind()
    inspector = inspect(conn)
    existing_tables = inspector.get_table_names()

    if 'agent_tasks' in existing_tables:
        op.drop_index('ix_agent_tasks_org_status', table_name='agent_tasks')
        op.drop_index('ix_agent_tasks_conversation', table_name='agent_tasks')
        op.drop_index('ix_agent_tasks_user_status', table_name='agent_tasks')
        op.drop_table('agent_tasks')
