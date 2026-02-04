"""Add input/output schemas to workflows.

Revision ID: 031
Revises: 030
Create Date: 2026-02-03

Workflows can now define typed input and output schemas for better composition:
- input_schema: JSON Schema defining expected input parameters (null = accept any)
- output_schema: JSON Schema defining expected output format (null = string/free-form)

When schemas are defined:
- Input is validated before execution
- Typed parameters are injected into the prompt
- Output is extracted/validated (best-effort)
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers
revision = "031"
down_revision = "030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add input_schema column - JSON Schema for expected inputs
    # null = no schema, accepts any trigger_data (default)
    op.add_column(
        "workflows",
        sa.Column("input_schema", JSONB, nullable=True),
    )
    
    # Add output_schema column - JSON Schema for expected output
    # null = string/free-form response (default)
    op.add_column(
        "workflows",
        sa.Column("output_schema", JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workflows", "output_schema")
    op.drop_column("workflows", "input_schema")
