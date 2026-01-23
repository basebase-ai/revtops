"""Add scope and user_id to integrations for user-scoped connections

Revision ID: 007_add_integration_scope
Revises: 006_add_user_roles
Create Date: 2026-01-23

This migration adds support for user-scoped integrations (like Gmail, Calendar)
where each team member connects individually, as opposed to org-scoped
integrations (like HubSpot, Salesforce) where one connection serves the whole org.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = '007_add_integration_scope'
down_revision: Union[str, None] = '006_add_user_roles'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add scope column - defaults to 'organization' for existing integrations
    op.add_column(
        'integrations',
        sa.Column('scope', sa.String(20), nullable=False, server_default='organization')
    )
    
    # Add user_id column for user-scoped integrations
    op.add_column(
        'integrations',
        sa.Column('user_id', UUID(as_uuid=True), sa.ForeignKey('users.id'), nullable=True)
    )
    
    # Add index on user_id for efficient queries
    op.create_index('ix_integrations_user_id', 'integrations', ['user_id'])
    
    # Add unique constraint: one integration per (org, provider, user)
    # Note: PostgreSQL treats NULL as distinct, so org-scoped (user_id=NULL) 
    # will have at most one row per provider, and user-scoped will have one per user
    op.create_unique_constraint(
        'uq_integration_org_provider_user',
        'integrations',
        ['organization_id', 'provider', 'user_id']
    )


def downgrade() -> None:
    op.drop_constraint('uq_integration_org_provider_user', 'integrations', type_='unique')
    op.drop_index('ix_integrations_user_id', table_name='integrations')
    op.drop_column('integrations', 'user_id')
    op.drop_column('integrations', 'scope')
