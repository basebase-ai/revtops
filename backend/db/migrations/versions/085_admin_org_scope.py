"""Remove org-admin state from users table.

Revision ID: 085_admin_org_scope
Revises: 084_org_admin_seed
Create Date: 2026-03-03
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "085_admin_org_scope"
down_revision: Union[str, None] = "084_org_admin_seed"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    assert len(revision) <= 32
    assert isinstance(down_revision, str) and len(down_revision) <= 32

    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            UPDATE users
               SET role = 'member'
             WHERE role = 'admin';
            """
        )
    )


def downgrade() -> None:
    # Data migration is not safely reversible.
    pass
