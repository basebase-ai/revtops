"""Add content fields to artifacts for file-based artifacts.

Revision ID: 035
Revises: 034
Create Date: 2026-02-04

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '035'
down_revision = '034'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add content field - stores text/markdown or base64-encoded PDF/chart JSON
    op.add_column(
        'artifacts',
        sa.Column('content', sa.Text(), nullable=True)
    )
    
    # Add content_type - one of: text, markdown, pdf, chart
    op.add_column(
        'artifacts',
        sa.Column('content_type', sa.String(50), nullable=True)
    )
    
    # Add mime_type for proper content-type headers on download
    op.add_column(
        'artifacts',
        sa.Column('mime_type', sa.String(100), nullable=True)
    )
    
    # Add filename for download
    op.add_column(
        'artifacts',
        sa.Column('filename', sa.String(255), nullable=True)
    )
    
    # Add conversation_id to link artifact to conversation for persistence
    op.add_column(
        'artifacts',
        sa.Column(
            'conversation_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('conversations.id', ondelete='CASCADE'),
            nullable=True,
            index=True
        )
    )
    
    # Add message_id to link artifact to specific message
    op.add_column(
        'artifacts',
        sa.Column(
            'message_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('chat_messages.id', ondelete='SET NULL'),
            nullable=True,
            index=True
        )
    )


def downgrade() -> None:
    op.drop_column('artifacts', 'message_id')
    op.drop_column('artifacts', 'conversation_id')
    op.drop_column('artifacts', 'filename')
    op.drop_column('artifacts', 'mime_type')
    op.drop_column('artifacts', 'content_type')
    op.drop_column('artifacts', 'content')
