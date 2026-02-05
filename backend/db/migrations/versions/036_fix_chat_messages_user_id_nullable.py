"""Fix chat_messages.user_id to be nullable for Slack DM support.

This was supposed to be in migration 034 but was added after it had already run.

Revision ID: 036
Revises: 035
Create Date: 2026-02-05

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '036'
down_revision = '035'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Make user_id nullable on chat_messages for Slack conversations
    op.alter_column('chat_messages', 'user_id', nullable=True)


def downgrade() -> None:
    op.alter_column('chat_messages', 'user_id', nullable=False)
