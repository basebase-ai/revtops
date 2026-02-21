"""Compatibility merge revision for global command column drop.

Revision ID: 070_merge_drop_agent_global_cmd
Revises: 069_conv_participants
Create Date: 2026-02-18 16:10:00
"""

from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = "070_merge_drop_agent_global_cmd"
down_revision: Union[str, None] = "069_conv_participants"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Intentionally empty. This revision exists to support environments
    # that already recorded this historical revision ID.
    pass


def downgrade() -> None:
    # Intentionally empty.
    pass
