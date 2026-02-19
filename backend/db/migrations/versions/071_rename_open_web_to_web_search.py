"""Rename integrations provider open_web to web_search.

Revision ID: 071_rename_open_web
Revises: 58726d896351
Create Date: 2026-02-18

"""
from typing import Sequence, Union

from alembic import op

revision: str = "071_rename_open_web"
down_revision: Union[str, None] = "58726d896351"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "UPDATE integrations SET provider = 'web_search' WHERE provider = 'open_web'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE integrations SET provider = 'open_web' WHERE provider = 'web_search'"
    )
