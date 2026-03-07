"""Remove org-scoped memories (entity_type='organization') for security.

Revision ID: 094_remove_org_scoped_memories
Revises: 093_activity_visibility_scoping
Create Date: 2026-03-06

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

revision: str = "094_remove_org_scoped_memories"
down_revision: Union[str, None] = "093_activity_visibility_scoping"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(text("DELETE FROM memories WHERE entity_type = 'organization'"))


def downgrade() -> None:
    # Deleted org-scoped memories are not restored.
    pass
