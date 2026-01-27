"""Fix integration unique constraint to include user_id.

The old constraint (organization_id, provider) prevents multiple users
from having the same provider integration. The new constraint
(organization_id, provider, user_id) allows each user to have their own
connection for user-scoped integrations.

Revision ID: 019_fix_int_unique
Revises: 018_add_workflows
Create Date: 2026-01-27
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "019_fix_int_unique"
down_revision: Union[str, None] = "018_add_workflows"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop old constraint if it exists
    op.execute("""
        ALTER TABLE integrations 
        DROP CONSTRAINT IF EXISTS uq_integrations_org_provider
    """)
    
    # Drop new constraint if it exists (in case of re-run)
    op.execute("""
        ALTER TABLE integrations 
        DROP CONSTRAINT IF EXISTS uq_integration_org_provider_user
    """)
    
    # Add new constraint that includes user_id
    # This allows:
    # - One org-scoped integration per (org, provider) where user_id is NULL
    # - Multiple user-scoped integrations per (org, provider) - one per user
    op.execute("""
        ALTER TABLE integrations 
        ADD CONSTRAINT uq_integration_org_provider_user 
        UNIQUE (organization_id, provider, user_id)
    """)


def downgrade() -> None:
    # Drop new constraint
    op.execute("""
        ALTER TABLE integrations 
        DROP CONSTRAINT IF EXISTS uq_integration_org_provider_user
    """)
    
    # Restore old constraint
    op.execute("""
        ALTER TABLE integrations 
        ADD CONSTRAINT uq_integrations_org_provider 
        UNIQUE (organization_id, provider)
    """)
