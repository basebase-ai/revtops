"""Add per-workflow LLM model override.

Revision ID: 132_wf_llm_model
Revises: 131_workflow_model
Create Date: 2026-04-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "132_wf_llm_model"
down_revision: Union[str, Sequence[str], None] = "131_workflow_model"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "workflows",
        sa.Column("llm_model", sa.String(length=128), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workflows", "llm_model")
