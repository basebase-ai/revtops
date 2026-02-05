"""Add change_sessions and record_snapshots tables for rollback capability.

Part of Phase 3: Change Sessions & Rollback
- change_sessions: Groups related changes made by an agent task
- record_snapshots: Stores before/after state for rollback
- Add updated_at/updated_by to synced tables (contacts, deals, accounts, activities)

Revision ID: 027
Revises: 026
Create Date: 2026-02-03
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '027'
down_revision = '026'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create change_sessions, record_snapshots tables and add tracking columns."""
    
    # Create change_sessions table
    op.create_table(
        'change_sessions',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('organizations.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('users.id', ondelete='SET NULL'),
                  nullable=True),
        sa.Column('conversation_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('conversations.id', ondelete='SET NULL'),
                  nullable=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='pending'),
        sa.Column('description', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('resolved_by', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('users.id', ondelete='SET NULL'),
                  nullable=True),
    )
    
    # Indexes for change_sessions
    op.create_index('ix_change_sessions_org', 'change_sessions', ['organization_id'])
    op.create_index('ix_change_sessions_user', 'change_sessions', ['user_id'])
    op.create_index('ix_change_sessions_conversation', 'change_sessions', ['conversation_id'])
    op.create_index('ix_change_sessions_status', 'change_sessions', ['status'],
                    postgresql_where=sa.text("status = 'pending'"))
    
    # Create record_snapshots table
    op.create_table(
        'record_snapshots',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('change_session_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('change_sessions.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('table_name', sa.String(50), nullable=False),
        sa.Column('record_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('operation', sa.String(20), nullable=False),  # create, update, delete
        sa.Column('before_data', postgresql.JSONB, nullable=True),  # null for creates
        sa.Column('after_data', postgresql.JSONB, nullable=True),   # null for deletes
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    
    # Indexes for record_snapshots
    op.create_index('ix_record_snapshots_session', 'record_snapshots', ['change_session_id'])
    op.create_index('ix_record_snapshots_record', 'record_snapshots', ['table_name', 'record_id'])
    
    # Add updated_at and updated_by to contacts
    op.add_column('contacts',
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('contacts',
        sa.Column('updated_by', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True))
    
    # Add updated_at and updated_by to deals
    op.add_column('deals',
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('deals',
        sa.Column('updated_by', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True))
    
    # Add updated_at and updated_by to accounts
    op.add_column('accounts',
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('accounts',
        sa.Column('updated_by', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True))
    
    # Add updated_at and updated_by to activities
    op.add_column('activities',
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('activities',
        sa.Column('updated_by', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True))


def downgrade() -> None:
    """Remove change_sessions, record_snapshots tables and tracking columns."""
    
    # Remove tracking columns from synced tables
    op.drop_column('activities', 'updated_by')
    op.drop_column('activities', 'updated_at')
    
    op.drop_column('accounts', 'updated_by')
    op.drop_column('accounts', 'updated_at')
    
    op.drop_column('deals', 'updated_by')
    op.drop_column('deals', 'updated_at')
    
    op.drop_column('contacts', 'updated_by')
    op.drop_column('contacts', 'updated_at')
    
    # Drop record_snapshots table
    op.drop_index('ix_record_snapshots_record', table_name='record_snapshots')
    op.drop_index('ix_record_snapshots_session', table_name='record_snapshots')
    op.drop_table('record_snapshots')
    
    # Drop change_sessions table
    op.drop_index('ix_change_sessions_status', table_name='change_sessions')
    op.drop_index('ix_change_sessions_conversation', table_name='change_sessions')
    op.drop_index('ix_change_sessions_user', table_name='change_sessions')
    op.drop_index('ix_change_sessions_org', table_name='change_sessions')
    op.drop_table('change_sessions')
