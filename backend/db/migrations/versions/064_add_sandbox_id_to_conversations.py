"""Add sandbox_id column to conversations for E2B sandbox persistence.

Stores the E2B sandbox ID so any worker/process can reconnect to a
conversation's sandbox across requests, deploys, and worker restarts.

Revision ID: 064_add_sandbox_id_to_conversations
Revises: 063_add_temp_data
Create Date: 2026-02-16
"""

from alembic import op
import sqlalchemy as sa

revision = "064_add_conv_sandbox_id"
down_revision = "063_add_temp_data"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("sandbox_id", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("conversations", "sandbox_id")
