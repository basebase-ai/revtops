"""Change github_pull_requests.github_pr_id to BIGINT.

GitHub PR IDs can exceed int32 range (2^31-1); use BIGINT to avoid overflow.

Revision ID: 096_github_pr_id_bigint
Revises: 095_rename_provider_to_connector
Create Date: 2026-03-07

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text


revision: str = "096_github_pr_id_bigint"
down_revision: Union[str, None] = "095_rename_provider_to_connector"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        text(
            "ALTER TABLE github_pull_requests "
            "ALTER COLUMN github_pr_id TYPE BIGINT USING github_pr_id::BIGINT"
        )
    )


def downgrade() -> None:
    # Only safe if all values fit in int32
    op.execute(
        text(
            "ALTER TABLE github_pull_requests "
            "ALTER COLUMN github_pr_id TYPE INTEGER USING github_pr_id::INTEGER"
        )
    )
