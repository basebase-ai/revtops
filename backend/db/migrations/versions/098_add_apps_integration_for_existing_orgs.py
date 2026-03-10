"""Add apps Integration for existing orgs.

Revision ID: 098_apps_integration
Revises: 097_artifacts_integration
Create Date: 2026-03-10

Auto-enable the apps connector for organizations that don't already have it.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text


revision: str = "098_apps_integration"
down_revision: Union[str, None] = "097_artifacts_integration"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        text("""
            INSERT INTO integrations (
                id,
                organization_id,
                connector,
                provider,
                user_id,
                scope,
                nango_connection_id,
                connected_by_user_id,
                is_active,
                share_synced_data,
                share_query_access,
                share_write_access,
                pending_sharing_config,
                created_at,
                updated_at
            )
            SELECT
                gen_random_uuid(),
                o.id,
                'apps',
                'apps',
                om.user_id,
                'organization',
                'builtin',
                om.user_id,
                true,
                true,
                true,
                true,
                false,
                NOW(),
                NOW()
            FROM organizations o
            JOIN LATERAL (
                SELECT user_id FROM org_members
                WHERE organization_id = o.id
                ORDER BY CASE WHEN role = 'admin' THEN 0 ELSE 1 END, joined_at ASC
                LIMIT 1
            ) om ON true
            WHERE NOT EXISTS (
                SELECT 1 FROM integrations i
                WHERE i.organization_id = o.id AND i.connector = 'apps'
            )
        """)
    )


def downgrade() -> None:
    op.execute(
        text("DELETE FROM integrations WHERE connector = 'apps' AND nango_connection_id = 'builtin'")
    )
