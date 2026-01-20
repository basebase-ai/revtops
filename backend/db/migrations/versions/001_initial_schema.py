"""Initial schema

Revision ID: 001_initial
Revises: 
Create Date: 2026-01-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '001_initial'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Customers table
    op.create_table(
        'customers',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text('gen_random_uuid()')),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('salesforce_instance_url', sa.String(255), nullable=True),
        sa.Column('salesforce_org_id', sa.String(255), nullable=True),
        sa.Column('system_oauth_token_encrypted', sa.Text(), nullable=True),
        sa.Column('system_oauth_refresh_token_encrypted', sa.Text(), nullable=True),
        sa.Column('token_owner_user_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.Column('last_sync_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )

    # Users table
    op.create_table(
        'users',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text('gen_random_uuid()')),
        sa.Column('email', sa.String(255), nullable=False),
        sa.Column('name', sa.String(255), nullable=True),
        sa.Column('customer_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('salesforce_user_id', sa.String(255), nullable=True),
        sa.Column('role', sa.String(50), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.Column('last_login', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email'),
        sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], )
    )

    # Add foreign key for token_owner_user_id after users table exists
    op.create_foreign_key(
        'fk_customers_token_owner',
        'customers', 'users',
        ['token_owner_user_id'], ['id']
    )

    # Accounts table
    op.create_table(
        'accounts',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text('gen_random_uuid()')),
        sa.Column('customer_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('source_system', sa.String(50), nullable=False, server_default='salesforce'),
        sa.Column('source_id', sa.String(255), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('domain', sa.String(255), nullable=True),
        sa.Column('industry', sa.String(100), nullable=True),
        sa.Column('employee_count', sa.Integer(), nullable=True),
        sa.Column('annual_revenue', sa.Numeric(15, 2), nullable=True),
        sa.Column('owner_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('custom_fields', postgresql.JSONB(), nullable=True),
        sa.Column('synced_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ),
        sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ),
        sa.UniqueConstraint('customer_id', 'source_system', 'source_id', name='uq_accounts_source')
    )
    op.create_index('idx_accounts_customer', 'accounts', ['customer_id'])
    op.create_index('idx_accounts_name', 'accounts', ['name'])

    # Deals table
    op.create_table(
        'deals',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text('gen_random_uuid()')),
        sa.Column('customer_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('source_system', sa.String(50), nullable=False, server_default='salesforce'),
        sa.Column('source_id', sa.String(255), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('account_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('owner_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('amount', sa.Numeric(15, 2), nullable=True),
        sa.Column('stage', sa.String(100), nullable=True),
        sa.Column('probability', sa.Integer(), nullable=True),
        sa.Column('close_date', sa.Date(), nullable=True),
        sa.Column('created_date', sa.DateTime(), nullable=True),
        sa.Column('last_modified_date', sa.DateTime(), nullable=True),
        sa.Column('visible_to_user_ids', postgresql.ARRAY(postgresql.UUID(as_uuid=True)), nullable=True),
        sa.Column('custom_fields', postgresql.JSONB(), nullable=True),
        sa.Column('synced_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ),
        sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], ),
        sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ),
        sa.UniqueConstraint('customer_id', 'source_system', 'source_id', name='uq_deals_source')
    )
    op.create_index('idx_deals_customer', 'deals', ['customer_id'])
    op.create_index('idx_deals_owner', 'deals', ['owner_id'])
    op.create_index('idx_deals_stage', 'deals', ['stage'])
    op.create_index('idx_deals_close_date', 'deals', ['close_date'])
    op.create_index('idx_deals_visible_to', 'deals', ['visible_to_user_ids'], postgresql_using='gin')
    op.create_index('idx_deals_custom_fields', 'deals', ['custom_fields'], postgresql_using='gin')

    # Contacts table
    op.create_table(
        'contacts',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text('gen_random_uuid()')),
        sa.Column('customer_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('source_system', sa.String(50), nullable=False, server_default='salesforce'),
        sa.Column('source_id', sa.String(255), nullable=False),
        sa.Column('account_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('name', sa.String(255), nullable=True),
        sa.Column('email', sa.String(255), nullable=True),
        sa.Column('title', sa.String(255), nullable=True),
        sa.Column('phone', sa.String(50), nullable=True),
        sa.Column('custom_fields', postgresql.JSONB(), nullable=True),
        sa.Column('synced_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ),
        sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], ),
        sa.UniqueConstraint('customer_id', 'source_system', 'source_id', name='uq_contacts_source')
    )
    op.create_index('idx_contacts_customer', 'contacts', ['customer_id'])
    op.create_index('idx_contacts_account', 'contacts', ['account_id'])
    op.create_index('idx_contacts_email', 'contacts', ['email'])

    # Activities table
    op.create_table(
        'activities',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text('gen_random_uuid()')),
        sa.Column('customer_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('source_system', sa.String(50), nullable=False, server_default='salesforce'),
        sa.Column('source_id', sa.String(255), nullable=True),
        sa.Column('deal_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('account_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('contact_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('type', sa.String(50), nullable=True),
        sa.Column('subject', sa.Text(), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('activity_date', sa.DateTime(), nullable=True),
        sa.Column('created_by_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('custom_fields', postgresql.JSONB(), nullable=True),
        sa.Column('synced_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ),
        sa.ForeignKeyConstraint(['deal_id'], ['deals.id'], ),
        sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], ),
        sa.ForeignKeyConstraint(['contact_id'], ['contacts.id'], ),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], )
    )
    op.create_index('idx_activities_customer', 'activities', ['customer_id'])
    op.create_index('idx_activities_deal', 'activities', ['deal_id'])
    op.create_index('idx_activities_date', 'activities', ['activity_date'])

    # Chat messages table
    op.create_table(
        'chat_messages',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text('gen_random_uuid()')),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('role', sa.String(20), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('tool_calls', postgresql.JSONB(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], )
    )
    op.create_index('idx_chat_user', 'chat_messages', ['user_id', 'created_at'])

    # Artifacts table
    op.create_table(
        'artifacts',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text('gen_random_uuid()')),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('customer_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('type', sa.String(50), nullable=True),
        sa.Column('title', sa.String(255), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('config', postgresql.JSONB(), nullable=True),
        sa.Column('snapshot_data', postgresql.JSONB(), nullable=True),
        sa.Column('is_live', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.Column('last_viewed_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], )
    )
    op.create_index('idx_artifacts_user', 'artifacts', ['user_id'])
    op.create_index('idx_artifacts_customer', 'artifacts', ['customer_id'])

    # Integrations table (for Nango connections)
    op.create_table(
        'integrations',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text('gen_random_uuid()')),
        sa.Column('customer_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('provider', sa.String(50), nullable=False),
        sa.Column('nango_connection_id', sa.String(255), nullable=True),
        sa.Column('connected_by_user_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('last_sync_at', sa.DateTime(), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('extra_data', postgresql.JSONB(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ),
        sa.ForeignKeyConstraint(['connected_by_user_id'], ['users.id'], ),
        sa.UniqueConstraint('customer_id', 'provider', name='uq_integrations_customer_provider')
    )
    op.create_index('idx_integrations_customer', 'integrations', ['customer_id'])


def downgrade() -> None:
    op.drop_table('integrations')
    op.drop_table('artifacts')
    op.drop_table('chat_messages')
    op.drop_table('activities')
    op.drop_table('contacts')
    op.drop_table('deals')
    op.drop_table('accounts')
    op.drop_constraint('fk_customers_token_owner', 'customers', type_='foreignkey')
    op.drop_table('users')
    op.drop_table('customers')
