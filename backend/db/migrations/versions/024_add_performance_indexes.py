"""Add performance indexes for activities and chat_messages.

Addresses slow queries:
- DELETE FROM activities WHERE source_system = $1 (2.27s)
- SELECT activities queries filtering by organization_id + type/source_system

Revision ID: 024_perf_indexes
Revises: 023_add_sync_stats
Create Date: 2026-01-30
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "024"
down_revision: Union[str, None] = "023_add_sync_stats"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add missing indexes for query performance."""
    # Activities table - critical for sync operations and filtering
    op.create_index(
        "ix_activities_source_system",
        "activities",
        ["source_system"],
    )
    op.create_index(
        "ix_activities_org_source_system",
        "activities",
        ["organization_id", "source_system"],
    )
    op.create_index(
        "ix_activities_org_type",
        "activities",
        ["organization_id", "type"],
    )
    # Unique lookup pattern used in sync operations
    op.create_index(
        "ix_activities_org_source_id",
        "activities",
        ["organization_id", "source_system", "source_id"],
    )

    # Chat messages - ordering within conversations
    op.create_index(
        "ix_chat_messages_conv_created",
        "chat_messages",
        ["conversation_id", "created_at"],
    )


def downgrade() -> None:
    """Remove performance indexes."""
    op.drop_index("ix_activities_source_system", table_name="activities")
    op.drop_index("ix_activities_org_source_system", table_name="activities")
    op.drop_index("ix_activities_org_type", table_name="activities")
    op.drop_index("ix_activities_org_source_id", table_name="activities")
    op.drop_index("ix_chat_messages_conv_created", table_name="chat_messages")
