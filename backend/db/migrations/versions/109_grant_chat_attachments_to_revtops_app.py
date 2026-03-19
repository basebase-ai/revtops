"""Grant revtops_app access to chat_attachments.

Revision ID: 109_grant_chat_attachments
Revises: 108_chat_attachments
Create Date: 2026-03-19

The app uses SET ROLE revtops_app; new tables need explicit GRANT.
Without this, INSERT into chat_attachments raises ProgrammingError.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "109_grant_chat_attachments"
down_revision: Union[str, None] = "108_chat_attachments"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("GRANT ALL ON chat_attachments TO revtops_app")


def downgrade() -> None:
    # Revoke is optional; table drop in 108 downgrade removes the table
    pass
