"""Add index on apps.archived_at for gallery list query performance.

Revision ID: 084_app_archived_at_index
Revises: 083_guest_locks
Create Date: 2026-03-02
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "084_app_archived_at_index"
down_revision: Union[str, None] = "083_guest_locks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("ix_apps_archived_at", "apps", ["archived_at"])


def downgrade() -> None:
    op.drop_index("ix_apps_archived_at", table_name="apps")
