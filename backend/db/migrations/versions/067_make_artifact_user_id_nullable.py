"""Make artifacts.user_id nullable for Slack conversations.

Slack @mention and DM conversations may not have a linked RevTops user,
so artifacts created during those conversations need to allow NULL user_id.

Revision ID: 067_artifact_user_id_nullable
Revises: 067_add_home_app_id
Create Date: 2026-02-17
"""

from alembic import op

revision = "067_artifact_user_id_nullable"
down_revision = "067_add_home_app_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("artifacts", "user_id", nullable=True)


def downgrade() -> None:
    op.alter_column("artifacts", "user_id", nullable=False)
