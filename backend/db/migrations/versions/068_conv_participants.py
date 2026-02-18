"""Add participating_user_ids to conversations for multi-user Slack threads.

Revision ID: 068_conv_participants
Revises: 067_artifact_user_id_nullable
Create Date: 2026-02-18
"""

from alembic import op
import sqlalchemy as sa

revision = "068_conv_participants"
down_revision = "067_artifact_user_id_nullable"
branch_labels = None
depends_on = None

assert len(revision) <= 32
assert isinstance(down_revision, str) and len(down_revision) <= 32


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column(
            "participating_user_ids",
            sa.ARRAY(sa.String(length=100)),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.create_index(
        "ix_conversations_participating_user_ids",
        "conversations",
        ["participating_user_ids"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_conversations_participating_user_ids", table_name="conversations")
    op.drop_column("conversations", "participating_user_ids")
