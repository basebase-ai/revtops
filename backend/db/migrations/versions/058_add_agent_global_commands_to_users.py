"""Add agent_global_commands field to users for persistent per-user prompt instructions.

Revision ID: 058_add_agent_global_commands_to_users
Revises: 057_enable_workflow_runs_rls
Create Date: 2026-02-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "058_add_agent_global_commands_to_users"
down_revision: Union[str, None] = "057_enable_workflow_runs_rls"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("agent_global_commands", sa.String(length=4000), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "agent_global_commands")
