"""Make crm_operations.user_id nullable for Slack conversations.

Slack DM conversations don't have a RevTops user, so we need to allow
CRM operations to be created without a user_id.

Revision ID: 038
Revises: 037
Create Date: 2026-02-05
"""
from alembic import op

revision = '038'
down_revision = '037'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column('crm_operations', 'user_id', nullable=True)


def downgrade() -> None:
    op.alter_column('crm_operations', 'user_id', nullable=False)
