"""Merge multiple heads

Revision ID: 58726d896351
Revises: 066_add_home_app_id, 067_add_home_app_id, 070_drop_user_cmd_col
Create Date: 2026-02-18 15:53:41.261288

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '58726d896351'
down_revision: Union[str, None] = ('066_add_home_app_id', '067_add_home_app_id', '070_drop_user_cmd_col')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
