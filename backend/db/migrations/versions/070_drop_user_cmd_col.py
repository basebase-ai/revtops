"""Drop legacy users.agent_global_commands column if it still exists.

Revision ID: 070_drop_user_cmd_col
Revises: 069_conv_participants
Create Date: 2026-02-18
"""

from alembic import op
import sqlalchemy as sa

revision = "070_drop_user_cmd_col"
down_revision = "069_conv_participants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Defensive cleanup for environments where legacy column still exists.
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS agent_global_commands")


def downgrade() -> None:
    op.add_column(
        "users",
        sa.Column("agent_global_commands", sa.String(length=4000), nullable=True),
    )
