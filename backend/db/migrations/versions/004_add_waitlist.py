"""Add waitlist fields to users

Revision ID: 004_add_waitlist
Revises: 003_add_embeddings
Create Date: 2026-01-22

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '004_add_waitlist'
down_revision: Union[str, None] = '003_add_embeddings'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add status column with default 'active' for existing users
    op.add_column(
        'users',
        sa.Column(
            'status',
            sa.String(20),
            nullable=False,
            server_default='active'
        )
    )
    
    # Add waitlist data as JSONB
    op.add_column(
        'users',
        sa.Column(
            'waitlist_data',
            postgresql.JSONB(),
            nullable=True
        )
    )
    
    # Add timestamp fields
    op.add_column(
        'users',
        sa.Column('waitlisted_at', sa.DateTime(), nullable=True)
    )
    op.add_column(
        'users',
        sa.Column('invited_at', sa.DateTime(), nullable=True)
    )
    
    # Index for filtering by status
    op.create_index('idx_users_status', 'users', ['status'])


def downgrade() -> None:
    op.drop_index('idx_users_status', table_name='users')
    op.drop_column('users', 'invited_at')
    op.drop_column('users', 'waitlisted_at')
    op.drop_column('users', 'waitlist_data')
    op.drop_column('users', 'status')
