"""Rename integrations.provider to integrations.connector.

Aligns DB terminology with agent-facing 'connector' (connector slug).

Revision ID: 095_rename_provider_to_connector
Revises: 094_remove_org_scoped_memories
Create Date: 2026-03-07

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import Column, String, text


revision: str = "095_rename_provider_to_connector"
down_revision: Union[str, None] = "094_remove_org_scoped_memories"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Phase 1: Add connector column, copy from provider, keep provider for now.
    # A future migration will drop provider after all code is deployed.
    op.add_column("integrations", Column("connector", String(50), nullable=True))
    op.execute(text("UPDATE integrations SET connector = provider"))
    op.alter_column(
        "integrations",
        "connector",
        existing_type=String(50),
        nullable=False,
    )
    op.drop_constraint("uq_integration_org_provider_user", "integrations", type_="unique")
    op.create_unique_constraint(
        "uq_integration_org_connector_user",
        "integrations",
        ["organization_id", "connector", "user_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_integration_org_connector_user", "integrations", type_="unique")
    op.create_unique_constraint(
        "uq_integration_org_provider_user",
        "integrations",
        ["organization_id", "provider", "user_id"],
    )
    op.drop_column("integrations", "connector")

