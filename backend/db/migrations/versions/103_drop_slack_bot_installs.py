"""Drop the legacy slack_bot_installs table.

All code now uses ``messenger_bot_installs`` exclusively.
Data was already copied by migration 101.

Revision ID: 103_drop_slack_bot_installs
Revises: 102_summary_doc_id
Create Date: 2026-03-13
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID


revision: str = "103_drop_slack_bot_installs"
down_revision: Union[str, None] = "102_summary_doc_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("slack_bot_installs")


def downgrade() -> None:
    op.create_table(
        "slack_bot_installs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("team_id", sa.String(32), nullable=False, unique=True),
        sa.Column("access_token_encrypted", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_slack_bot_installs_organization_id", "slack_bot_installs", ["organization_id"])
    op.create_index("ix_slack_bot_installs_team_id", "slack_bot_installs", ["team_id"], unique=True)
