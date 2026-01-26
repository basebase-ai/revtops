"""Add content_blocks column to chat_messages.

This migrates from the legacy format (content + tool_calls) to a unified
content_blocks array following the Anthropic API pattern.

Revision ID: 012
Revises: 011
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "012_add_content_blocks"
down_revision = "010_add_org_logo_url"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add content_blocks column
    op.add_column(
        "chat_messages",
        sa.Column("content_blocks", JSONB, nullable=True),
    )
    
    # Migrate existing data: convert content + tool_calls to content_blocks
    # This is done in Python via the model's _legacy_to_blocks() method on read,
    # so we don't need to migrate data here - it's handled automatically.
    # 
    # If you want to migrate data in SQL (optional, for cleanup):
    # UPDATE chat_messages 
    # SET content_blocks = (
    #   SELECT jsonb_agg(block) FROM (
    #     SELECT jsonb_build_object('type', 'text', 'text', content) as block
    #     WHERE content IS NOT NULL AND content != ''
    #     UNION ALL
    #     SELECT jsonb_array_elements(tool_calls) as block
    #     WHERE tool_calls IS NOT NULL
    #   ) blocks
    # )
    # WHERE content_blocks IS NULL;


def downgrade() -> None:
    op.drop_column("chat_messages", "content_blocks")
