"""Add parent_conversation_id to conversations for child workflow linking.

Revision ID: 033
Revises: 032
Create Date: 2026-02-04

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '033'
down_revision = '032'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add parent_conversation_id column to conversations table
    # This links child workflow conversations back to their parent
    # Note: index=True in add_column automatically creates the index
    op.add_column(
        'conversations',
        sa.Column(
            'parent_conversation_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('conversations.id', ondelete='SET NULL'),
            nullable=True,
            index=True
        )
    )


def downgrade() -> None:
    op.drop_column('conversations', 'parent_conversation_id')
    # Index is dropped automatically with the column
