"""Add sheet_imports table for Google Sheets import tracking.

Revision ID: 025
Revises: 024
Create Date: 2026-01-30
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '025'
down_revision = '024'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create sheet_imports table."""
    op.create_table(
        'sheet_imports',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), 
                  sa.ForeignKey('organizations.id', ondelete='CASCADE'), 
                  nullable=False, index=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('users.id', ondelete='SET NULL'),
                  nullable=True, index=True),
        sa.Column('spreadsheet_id', sa.String(255), nullable=False),
        sa.Column('spreadsheet_name', sa.String(500), nullable=True),
        sa.Column('config', postgresql.JSONB, nullable=False, server_default='{}'),
        sa.Column('status', sa.String(50), nullable=False, default='pending', index=True),
        sa.Column('results', postgresql.JSONB, nullable=True),
        sa.Column('errors', postgresql.JSONB, nullable=True),
        sa.Column('error_message', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column('started_at', sa.DateTime, nullable=True),
        sa.Column('completed_at', sa.DateTime, nullable=True),
    )
    
    # Create composite index for org + status queries
    op.create_index(
        'ix_sheet_imports_org_status',
        'sheet_imports',
        ['organization_id', 'status']
    )


def downgrade() -> None:
    """Drop sheet_imports table."""
    op.drop_index('ix_sheet_imports_org_status', table_name='sheet_imports')
    op.drop_table('sheet_imports')
