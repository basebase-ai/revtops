"""Add action_ledger retention indexes.

Revision ID: 119_audit_retention
Revises: 118_create_action_ledger
Create Date: 2026-03-31
"""
from alembic import op


revision = "119_audit_retention"
down_revision = "118_create_action_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_action_ledger_created_at",
        "action_ledger",
        ["created_at"],
    )
    op.create_index(
        "ix_action_ledger_org_user_created",
        "action_ledger",
        ["organization_id", "user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_action_ledger_org_user_created", table_name="action_ledger")
    op.drop_index("ix_action_ledger_created_at", table_name="action_ledger")
