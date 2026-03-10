"""Add slack_bot_installs table for Add-to-Slack (bot install) OAuth flow.

Stores bot tokens for workspaces that add Basebase via the public "Add to Slack"
link, so we can route events to the correct org without going through Nango.

Revision ID: 078_slack_bot_installs
Revises: 077_usermap_read_grant
Create Date: 2026-02-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "078_slack_bot_installs"
down_revision: Union[str, None] = "077_usermap_read_grant"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "slack_bot_installs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("team_id", sa.String(32), nullable=False),
        sa.Column("access_token_encrypted", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_slack_bot_installs_team_id",
        "slack_bot_installs",
        ["team_id"],
        unique=True,
    )
    op.create_index(
        "ix_slack_bot_installs_organization_id",
        "slack_bot_installs",
        ["organization_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_slack_bot_installs_organization_id", table_name="slack_bot_installs")
    op.drop_index("ix_slack_bot_installs_team_id", table_name="slack_bot_installs")
    op.drop_table("slack_bot_installs")
