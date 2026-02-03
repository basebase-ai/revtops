"""Generalize crm_operations to pending_operations.

Part of Phase 7: Generalize Pending Operations.
This allows the same approval flow to be used for any tool,
not just CRM operations.

Revision ID: 030
Revises: 029
Create Date: 2026-02-02
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "030"
down_revision = "029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Rename table from crm_operations to pending_operations
    op.rename_table("crm_operations", "pending_operations")
    
    # Add tool_name column (for non-CRM tools)
    op.add_column(
        "pending_operations",
        sa.Column("tool_name", sa.String(50), nullable=True),
    )
    
    # Add tool_params column (for non-CRM tools)
    op.add_column(
        "pending_operations",
        sa.Column("tool_params", JSONB, nullable=True),
    )
    
    # Migrate existing data - set tool_name to 'crm_write' for all existing records
    op.execute("UPDATE pending_operations SET tool_name = 'crm_write' WHERE tool_name IS NULL")
    
    # Make CRM-specific columns nullable (they won't be used for non-CRM tools)
    op.alter_column("pending_operations", "target_system", nullable=True)
    op.alter_column("pending_operations", "record_type", nullable=True)
    op.alter_column("pending_operations", "operation", nullable=True)
    op.alter_column("pending_operations", "input_records", nullable=True)
    op.alter_column("pending_operations", "validated_records", nullable=True)
    
    # Update indexes (if any reference the old table name)
    # Note: Alembic automatically handles index name changes with table rename


def downgrade() -> None:
    # Make CRM-specific columns required again (this may fail if there's non-CRM data)
    op.alter_column("pending_operations", "target_system", nullable=False)
    op.alter_column("pending_operations", "record_type", nullable=False)
    op.alter_column("pending_operations", "operation", nullable=False)
    op.alter_column("pending_operations", "input_records", nullable=False)
    op.alter_column("pending_operations", "validated_records", nullable=False)
    
    # Drop new columns
    op.drop_column("pending_operations", "tool_params")
    op.drop_column("pending_operations", "tool_name")
    
    # Rename table back
    op.rename_table("pending_operations", "crm_operations")
