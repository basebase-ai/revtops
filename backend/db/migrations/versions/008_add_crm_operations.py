"""Add CRM operations table for write approval workflow

Revision ID: 008_add_crm_operations
Revises: 007_add_integration_scope
Create Date: 2026-01-23

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = '008_add_crm_operations'
down_revision: Union[str, None] = '007_add_integration_scope'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'crm_operations',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('organization_id', UUID(as_uuid=True), sa.ForeignKey('organizations.id'), nullable=False),
        sa.Column('user_id', UUID(as_uuid=True), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('conversation_id', UUID(as_uuid=True), sa.ForeignKey('conversations.id'), nullable=True),
        
        sa.Column('target_system', sa.String(50), nullable=False),
        sa.Column('record_type', sa.String(50), nullable=False),
        sa.Column('operation', sa.String(20), nullable=False),
        sa.Column('status', sa.String(20), nullable=False, server_default='pending'),
        
        sa.Column('input_records', JSONB, nullable=False),
        sa.Column('validated_records', JSONB, nullable=False),
        sa.Column('duplicate_warnings', JSONB, nullable=True),
        
        sa.Column('result', JSONB, nullable=True),
        sa.Column('error_message', sa.Text, nullable=True),
        
        sa.Column('record_count', sa.Integer, nullable=False, server_default='0'),
        sa.Column('success_count', sa.Integer, nullable=True),
        sa.Column('failure_count', sa.Integer, nullable=True),
        
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column('expires_at', sa.DateTime, nullable=False),
        sa.Column('executed_at', sa.DateTime, nullable=True),
    )
    
    # Index for looking up pending operations by user
    op.create_index(
        'ix_crm_operations_user_status',
        'crm_operations',
        ['user_id', 'status']
    )
    
    # Index for expiry cleanup
    op.create_index(
        'ix_crm_operations_expires_at',
        'crm_operations',
        ['expires_at']
    )


def downgrade() -> None:
    op.drop_index('ix_crm_operations_expires_at')
    op.drop_index('ix_crm_operations_user_status')
    op.drop_table('crm_operations')
