"""Add sync_stats column to integrations table.

Stores object counts from sync operations (e.g., accounts, deals, contacts, emails).

Revision ID: 023_add_sync_stats
Revises: 022_add_conversation_message_cache
Create Date: 2026-01-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision: str = "023_add_sync_stats"
down_revision: Union[str, None] = "022_conv_msg_cache"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add sync_stats JSONB column to integrations table."""
    op.add_column(
        "integrations",
        sa.Column("sync_stats", JSONB, nullable=True),
    )


def downgrade() -> None:
    """Remove sync_stats column."""
    op.drop_column("integrations", "sync_stats")
