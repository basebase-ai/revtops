"""Add prompt and auto_approve_tools to workflows.

Part of Phase 5: Workflows as Agent Conversations.
Workflows become "scheduled prompts to the agent" instead of rigid step definitions.

Revision ID: 029
Revises: 028
Create Date: 2026-02-02
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "029"
down_revision = "028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from sqlalchemy import inspect
    from alembic import op
    
    conn = op.get_bind()
    inspector = inspect(conn)
    
    # Get existing columns for workflows
    workflow_columns = [c['name'] for c in inspector.get_columns('workflows')]
    
    # Add prompt column - natural language instructions for the agent
    if 'prompt' not in workflow_columns:
        op.add_column(
            "workflows",
            sa.Column("prompt", sa.Text(), nullable=True),
        )
    
    # Add auto_approve_tools - list of tools pre-approved for this workflow
    if 'auto_approve_tools' not in workflow_columns:
        op.add_column(
            "workflows",
            sa.Column(
                "auto_approve_tools",
                JSONB,
                nullable=False,
                server_default="[]",
            ),
        )
    
    # Get existing columns for conversations
    conv_columns = [c['name'] for c in inspector.get_columns('conversations')]
    conv_indexes = [idx['name'] for idx in inspector.get_indexes('conversations')]
    
    # Add conversation type column if not exists (for workflow conversations)
    if 'type' not in conv_columns:
        op.add_column(
            "conversations",
            sa.Column(
                "type",
                sa.String(20),
                nullable=False,
                server_default="agent",
            ),
        )
    
    if 'ix_conversations_type' not in conv_indexes:
        op.create_index(
            "ix_conversations_type",
            "conversations",
            ["type"],
        )
    
    # Add workflow_id to conversations if not exists
    if 'workflow_id' not in conv_columns:
        op.add_column(
            "conversations",
            sa.Column(
                "workflow_id",
                sa.UUID(),
                nullable=True,
            ),
        )
        # Add foreign key separately
        op.create_foreign_key(
            "fk_conversations_workflow_id",
            "conversations",
            "workflows",
            ["workflow_id"],
            ["id"],
            ondelete="SET NULL",
        )
    
    if 'ix_conversations_workflow_id' not in conv_indexes:
        op.create_index(
            "ix_conversations_workflow_id",
            "conversations",
            ["workflow_id"],
        )


def downgrade() -> None:
    from sqlalchemy import inspect
    
    conn = op.get_bind()
    inspector = inspect(conn)
    
    # Get existing columns/indexes
    conv_columns = [c['name'] for c in inspector.get_columns('conversations')]
    conv_indexes = [idx['name'] for idx in inspector.get_indexes('conversations')]
    workflow_columns = [c['name'] for c in inspector.get_columns('workflows')]
    
    # Remove workflow_id from conversations
    if 'ix_conversations_workflow_id' in conv_indexes:
        op.drop_index("ix_conversations_workflow_id", table_name="conversations")
    if 'workflow_id' in conv_columns:
        op.drop_constraint("fk_conversations_workflow_id", "conversations", type_="foreignkey")
        op.drop_column("conversations", "workflow_id")
    
    # Remove type from conversations
    if 'ix_conversations_type' in conv_indexes:
        op.drop_index("ix_conversations_type", table_name="conversations")
    if 'type' in conv_columns:
        op.drop_column("conversations", "type")
    
    # Remove auto_approve_tools from workflows
    if 'auto_approve_tools' in workflow_columns:
        op.drop_column("workflows", "auto_approve_tools")
    
    # Remove prompt from workflows
    if 'prompt' in workflow_columns:
        op.drop_column("workflows", "prompt")
