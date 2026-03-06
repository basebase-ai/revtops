"""Add frontend_code_compiled column to apps table.

Stores pre-transpiled JS (JSX→JS via esbuild) so the frontend can skip
loading Babel Standalone at runtime.

Revision ID: 091_add_app_compiled_code
Revises: 090_add_org_handle
Create Date: 2026-03-04

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "091_add_app_compiled_code"
down_revision: Union[str, None] = "090_add_org_handle"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("apps", sa.Column("frontend_code_compiled", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("apps", "frontend_code_compiled")
