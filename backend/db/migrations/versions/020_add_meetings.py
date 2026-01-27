"""Add meetings table as canonical meeting entity.

Meetings are real-world events that may have multiple calendar entries,
transcripts, and notes linked to them. This provides deduplication across
data sources.

Revision ID: 020_add_meetings
Revises: 019_fix_integration_unique_constraint
Create Date: 2026-01-27
"""
from alembic import op
from sqlalchemy import text
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = '020_add_meetings'
down_revision = '019_fix_int_unique'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create meetings table - canonical representation of real-world meetings
    op.create_table(
        'meetings',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('organization_id', UUID(as_uuid=True), sa.ForeignKey('organizations.id'), nullable=False),
        
        # Core meeting info (normalized from best available source)
        sa.Column('title', sa.String(500), nullable=True),
        sa.Column('scheduled_start', sa.DateTime, nullable=False),
        sa.Column('scheduled_end', sa.DateTime, nullable=True),
        sa.Column('duration_minutes', sa.Integer, nullable=True),
        
        # Participants (deduplicated list)
        # Format: [{email, name, is_organizer, rsvp_status}]
        sa.Column('participants', JSONB, nullable=True),
        sa.Column('organizer_email', sa.String(255), nullable=True),
        sa.Column('participant_count', sa.Integer, nullable=True),
        
        # Status
        sa.Column('status', sa.String(50), default='scheduled', nullable=False),
        
        # Aggregated content from transcripts/notes
        sa.Column('summary', sa.Text, nullable=True),
        sa.Column('action_items', JSONB, nullable=True),  # [{text, assignee, due_date}]
        sa.Column('key_topics', JSONB, nullable=True),    # extracted keywords
        
        # Full transcript (optional, can be large)
        sa.Column('transcript', sa.Text, nullable=True),
        
        # Links to related entities
        sa.Column('account_id', UUID(as_uuid=True), sa.ForeignKey('accounts.id'), nullable=True),
        sa.Column('deal_id', UUID(as_uuid=True), sa.ForeignKey('deals.id'), nullable=True),
        
        # Metadata
        sa.Column('created_at', sa.DateTime, server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime, server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
    )

    # Add meeting_id FK to activities table
    op.add_column('activities', sa.Column('meeting_id', UUID(as_uuid=True), sa.ForeignKey('meetings.id'), nullable=True))

    # Create indexes
    op.create_index('ix_meetings_org_id', 'meetings', ['organization_id'])
    op.create_index('ix_meetings_scheduled_start', 'meetings', ['scheduled_start'])
    op.create_index('ix_meetings_org_start', 'meetings', ['organization_id', 'scheduled_start'])
    op.create_index('ix_activities_meeting_id', 'activities', ['meeting_id'])

    # Enable RLS on meetings table
    conn = op.get_bind()

    conn.execute(text('ALTER TABLE meetings ENABLE ROW LEVEL SECURITY'))
    conn.execute(text('ALTER TABLE meetings FORCE ROW LEVEL SECURITY'))
    conn.execute(text('''
        CREATE POLICY org_isolation ON meetings
        FOR ALL
        USING (
            organization_id::text = COALESCE(
                NULLIF(current_setting('app.current_org_id', true), ''),
                '00000000-0000-0000-0000-000000000000'
            )
        )
    '''))

    print("Created meetings table with RLS and added meeting_id to activities")


def downgrade() -> None:
    conn = op.get_bind()

    # Remove RLS
    conn.execute(text('DROP POLICY IF EXISTS org_isolation ON meetings'))
    conn.execute(text('ALTER TABLE meetings DISABLE ROW LEVEL SECURITY'))

    # Drop indexes
    op.drop_index('ix_activities_meeting_id', 'activities')
    op.drop_index('ix_meetings_org_start', 'meetings')
    op.drop_index('ix_meetings_scheduled_start', 'meetings')
    op.drop_index('ix_meetings_org_id', 'meetings')

    # Remove meeting_id from activities
    op.drop_column('activities', 'meeting_id')

    # Drop meetings table
    op.drop_table('meetings')
