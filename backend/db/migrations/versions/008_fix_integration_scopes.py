"""Fix scope for existing user-scoped integrations

Revision ID: 008_fix_integration_scopes
Revises: 007_add_integration_scope
Create Date: 2026-01-23

This migration fixes existing integrations that should be user-scoped
(gmail, google_calendar, microsoft_calendar, microsoft_mail) but were
set to 'organization' by the default in the previous migration.

For these integrations, we:
1. Set scope='user'
2. Copy connected_by_user_id to user_id (so they show as that user's connection)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '008_fix_integration_scopes'
down_revision: Union[str, None] = '007_add_integration_scope'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Providers that should be user-scoped
USER_SCOPED_PROVIDERS = ['gmail', 'google_calendar', 'microsoft_calendar', 'microsoft_mail']


def upgrade() -> None:
    # Update existing user-scoped integrations:
    # - Set scope to 'user'
    # - Copy connected_by_user_id to user_id
    op.execute(f"""
        UPDATE integrations
        SET scope = 'user',
            user_id = connected_by_user_id
        WHERE provider IN ('gmail', 'google_calendar', 'microsoft_calendar', 'microsoft_mail')
          AND scope = 'organization'
    """)


def downgrade() -> None:
    # Revert: set scope back to organization and clear user_id
    op.execute(f"""
        UPDATE integrations
        SET scope = 'organization',
            user_id = NULL
        WHERE provider IN ('gmail', 'google_calendar', 'microsoft_calendar', 'microsoft_mail')
    """)
