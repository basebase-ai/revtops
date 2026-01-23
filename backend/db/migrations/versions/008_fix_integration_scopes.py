"""Fix scope for existing user-scoped integrations

Revision ID: 008_fix_integration_scopes
Revises: 007_add_integration_scope
Create Date: 2026-01-23

This migration fixes existing integrations that should be user-scoped
(gmail, google_calendar, microsoft_calendar, microsoft_mail) but were
set to 'organization' by the default in the previous migration.

For these integrations, we:
1. Set scope='user'
2. Set user_id from connected_by_user_id if available, OR
   fall back to the first user in the organization
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '008_fix_integration_scopes'
down_revision: Union[str, None] = '007_add_integration_scope'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Step 1: Set scope to 'user' for user-scoped providers
    op.execute("""
        UPDATE integrations
        SET scope = 'user'
        WHERE provider IN ('gmail', 'google_calendar', 'microsoft_calendar', 'microsoft_mail')
    """)
    
    # Step 2: Set user_id from connected_by_user_id where available
    op.execute("""
        UPDATE integrations
        SET user_id = connected_by_user_id
        WHERE provider IN ('gmail', 'google_calendar', 'microsoft_calendar', 'microsoft_mail')
          AND connected_by_user_id IS NOT NULL
          AND user_id IS NULL
    """)
    
    # Step 3: For remaining integrations without user_id, 
    # assign to the first user in the organization
    op.execute("""
        UPDATE integrations i
        SET user_id = (
            SELECT u.id FROM users u 
            WHERE u.organization_id = i.organization_id 
            ORDER BY u.created_at ASC NULLS LAST
            LIMIT 1
        )
        WHERE i.provider IN ('gmail', 'google_calendar', 'microsoft_calendar', 'microsoft_mail')
          AND i.user_id IS NULL
    """)


def downgrade() -> None:
    # Revert: set scope back to organization and clear user_id
    op.execute("""
        UPDATE integrations
        SET scope = 'organization',
            user_id = NULL
        WHERE provider IN ('gmail', 'google_calendar', 'microsoft_calendar', 'microsoft_mail')
    """)
