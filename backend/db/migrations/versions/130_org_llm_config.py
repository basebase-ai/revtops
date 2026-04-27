"""Add LLM provider/model config columns to organizations.

Allows per-org LLM provider selection (anthropic, minimax, openai, gemini, qwen)
with model overrides. NULL means use global defaults from env vars.

Revision ID: 130_org_llm_config
Revises: 129_daily_digests_user_rls
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "130_org_llm_config"
down_revision: Union[str, None] = "129_daily_digests_user_rls"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("llm_provider", sa.String(32), nullable=True),
    )
    op.add_column(
        "organizations",
        sa.Column("llm_primary_model", sa.String(128), nullable=True),
    )
    op.add_column(
        "organizations",
        sa.Column("llm_cheap_model", sa.String(128), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("organizations", "llm_cheap_model")
    op.drop_column("organizations", "llm_primary_model")
    op.drop_column("organizations", "llm_provider")
