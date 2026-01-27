"""Add pipelines and pipeline_stages tables.

Normalized representation of CRM pipelines that works for both
HubSpot and Salesforce.

Revision ID: 017_add_pipelines
Revises: 016_add_organizations_rls
Create Date: 2026-01-26
"""
from alembic import op
from sqlalchemy import text
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = '017_add_pipelines'
down_revision = '016_add_organizations_rls'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create pipelines table
    op.create_table(
        'pipelines',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('organization_id', UUID(as_uuid=True), sa.ForeignKey('organizations.id'), nullable=False),
        sa.Column('source_system', sa.String(50), nullable=False),
        sa.Column('source_id', sa.String(255), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('display_order', sa.Integer, nullable=True),
        sa.Column('is_default', sa.Boolean, default=False, nullable=False),
        sa.Column('synced_at', sa.DateTime, nullable=True),
        sa.UniqueConstraint('organization_id', 'source_system', 'source_id', name='uq_pipeline_source'),
    )

    # Create pipeline_stages table
    op.create_table(
        'pipeline_stages',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('pipeline_id', UUID(as_uuid=True), sa.ForeignKey('pipelines.id', ondelete='CASCADE'), nullable=False),
        sa.Column('source_id', sa.String(255), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('display_order', sa.Integer, nullable=True),
        sa.Column('probability', sa.Integer, nullable=True),
        sa.Column('is_closed_won', sa.Boolean, default=False, nullable=False),
        sa.Column('is_closed_lost', sa.Boolean, default=False, nullable=False),
        sa.Column('synced_at', sa.DateTime, nullable=True),
        sa.UniqueConstraint('pipeline_id', 'source_id', name='uq_stage_source'),
    )

    # Add pipeline_id to deals table
    op.add_column('deals', sa.Column('pipeline_id', UUID(as_uuid=True), sa.ForeignKey('pipelines.id'), nullable=True))

    # Create indexes
    op.create_index('ix_pipelines_org_id', 'pipelines', ['organization_id'])
    op.create_index('ix_pipeline_stages_pipeline_id', 'pipeline_stages', ['pipeline_id'])
    op.create_index('ix_deals_pipeline_id', 'deals', ['pipeline_id'])

    # Enable RLS on new tables
    conn = op.get_bind()

    # Pipelines RLS
    conn.execute(text('ALTER TABLE pipelines ENABLE ROW LEVEL SECURITY'))
    conn.execute(text('ALTER TABLE pipelines FORCE ROW LEVEL SECURITY'))
    conn.execute(text('''
        CREATE POLICY org_isolation ON pipelines
        FOR ALL
        USING (
            organization_id::text = COALESCE(
                NULLIF(current_setting('app.current_org_id', true), ''),
                '00000000-0000-0000-0000-000000000000'
            )
        )
    '''))

    # Pipeline stages RLS (join through pipelines)
    conn.execute(text('ALTER TABLE pipeline_stages ENABLE ROW LEVEL SECURITY'))
    conn.execute(text('ALTER TABLE pipeline_stages FORCE ROW LEVEL SECURITY'))
    conn.execute(text('''
        CREATE POLICY org_isolation ON pipeline_stages
        FOR ALL
        USING (
            pipeline_id IN (
                SELECT id FROM pipelines
                WHERE organization_id::text = COALESCE(
                    NULLIF(current_setting('app.current_org_id', true), ''),
                    '00000000-0000-0000-0000-000000000000'
                )
            )
        )
    '''))

    print("Created pipelines and pipeline_stages tables with RLS")


def downgrade() -> None:
    conn = op.get_bind()

    # Remove RLS
    conn.execute(text('DROP POLICY IF EXISTS org_isolation ON pipeline_stages'))
    conn.execute(text('ALTER TABLE pipeline_stages DISABLE ROW LEVEL SECURITY'))
    conn.execute(text('DROP POLICY IF EXISTS org_isolation ON pipelines'))
    conn.execute(text('ALTER TABLE pipelines DISABLE ROW LEVEL SECURITY'))

    # Drop indexes
    op.drop_index('ix_deals_pipeline_id', 'deals')
    op.drop_index('ix_pipeline_stages_pipeline_id', 'pipeline_stages')
    op.drop_index('ix_pipelines_org_id', 'pipelines')

    # Remove pipeline_id from deals
    op.drop_column('deals', 'pipeline_id')

    # Drop tables
    op.drop_table('pipeline_stages')
    op.drop_table('pipelines')
