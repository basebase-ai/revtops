"""Add apps.widget_config (JSONB).

Revision ID: 116_add_app_widget_config
Revises: 115_fix_notifications_rls_policy
Create Date: 2026-03-24

Idempotent: safe if the column already exists (e.g. DB was migrated from a branch
that carried this revision before it landed in this repo).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
revision: str = "116_add_app_widget_config"
down_revision: Union[str, None] = "115_fix_notifications_rls_policy"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "ALTER TABLE apps ADD COLUMN IF NOT EXISTS widget_config JSONB NULL"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("ALTER TABLE apps DROP COLUMN IF EXISTS widget_config"))
