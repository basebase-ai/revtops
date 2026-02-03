"""Add sync_status column to CRM models for local-first workflow.

Revision ID: 028_add_sync_status
Revises: 027_add_change_sessions
Create Date: 2026-02-03
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers
revision = "028"
down_revision = "027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add sync_status to contacts
    op.add_column(
        "contacts",
        sa.Column(
            "sync_status",
            sa.String(20),
            nullable=False,
            server_default="synced",  # Existing records are already synced
        ),
    )
    
    # Add sync_status to deals
    op.add_column(
        "deals",
        sa.Column(
            "sync_status",
            sa.String(20),
            nullable=False,
            server_default="synced",
        ),
    )
    
    # Add sync_status to accounts
    op.add_column(
        "accounts",
        sa.Column(
            "sync_status",
            sa.String(20),
            nullable=False,
            server_default="synced",
        ),
    )
    
    # Create index for finding pending records quickly
    op.create_index(
        "ix_contacts_sync_status",
        "contacts",
        ["organization_id", "sync_status"],
        postgresql_where=sa.text("sync_status = 'pending'"),
    )
    op.create_index(
        "ix_deals_sync_status",
        "deals",
        ["organization_id", "sync_status"],
        postgresql_where=sa.text("sync_status = 'pending'"),
    )
    op.create_index(
        "ix_accounts_sync_status",
        "accounts",
        ["organization_id", "sync_status"],
        postgresql_where=sa.text("sync_status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index("ix_accounts_sync_status", table_name="accounts")
    op.drop_index("ix_deals_sync_status", table_name="deals")
    op.drop_index("ix_contacts_sync_status", table_name="contacts")
    
    op.drop_column("accounts", "sync_status")
    op.drop_column("deals", "sync_status")
    op.drop_column("contacts", "sync_status")
