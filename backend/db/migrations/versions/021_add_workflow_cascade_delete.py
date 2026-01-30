"""Add cascade delete to workflow_runs foreign key.

When a workflow is deleted, its associated run history should be
automatically deleted to prevent orphaned records and foreign key
constraint violations.

Revision ID: 021_wf_cascade
Revises: 020_add_meetings
Create Date: 2026-01-27
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "021_wf_cascade"
down_revision: Union[str, None] = "020_add_meetings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop existing foreign key constraint
    op.execute("""
        ALTER TABLE workflow_runs 
        DROP CONSTRAINT IF EXISTS workflow_runs_workflow_id_fkey
    """)
    
    # Add new foreign key with ON DELETE CASCADE
    op.execute("""
        ALTER TABLE workflow_runs 
        ADD CONSTRAINT workflow_runs_workflow_id_fkey 
        FOREIGN KEY (workflow_id) REFERENCES workflows(id) ON DELETE CASCADE
    """)


def downgrade() -> None:
    # Drop cascade foreign key
    op.execute("""
        ALTER TABLE workflow_runs 
        DROP CONSTRAINT IF EXISTS workflow_runs_workflow_id_fkey
    """)
    
    # Restore original foreign key without cascade
    op.execute("""
        ALTER TABLE workflow_runs 
        ADD CONSTRAINT workflow_runs_workflow_id_fkey 
        FOREIGN KEY (workflow_id) REFERENCES workflows(id)
    """)
