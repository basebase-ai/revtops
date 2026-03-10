"""Add activity visibility scoping (integration_id, owner_user_id, visibility).

Enables per-connector "others can read" enforcement so Basebase respects
share_synced_data when querying activities (e.g., private emails).

Revision ID: 093_activity_visibility_scoping
Revises: 092_org_handle_not_null
Create Date: 2026-03-06

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision: str = "093_activity_visibility_scoping"
down_revision: Union[str, None] = "092_org_handle_not_null"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NULL_UUID: str = "00000000-0000-0000-0000-000000000000"


def upgrade() -> None:
    conn = op.get_bind()

    # 1. Add integration_id (nullable, FK to integrations)
    op.add_column(
        "activities",
        sa.Column(
            "integration_id",
            sa.UUID(),
            sa.ForeignKey("integrations.id", ondelete="SET NULL", onupdate="CASCADE"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_activities_integration_id",
        "activities",
        ["integration_id"],
        unique=False,
    )

    # 2. Add owner_user_id (nullable for legacy rows)
    op.add_column(
        "activities",
        sa.Column(
            "owner_user_id",
            sa.UUID(),
            sa.ForeignKey("users.id", ondelete="SET NULL", onupdate="CASCADE"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_activities_owner_user_id",
        "activities",
        ["owner_user_id"],
        unique=False,
    )

    # 3. Add visibility column ('team' | 'owner_only')
    op.add_column(
        "activities",
        sa.Column(
            "visibility",
            sa.String(20),
            nullable=False,
            server_default="team",
        ),
    )

    # 4. Backfill integration_id, owner_user_id, visibility from integrations
    # Match by (organization_id, source_system); if multiple integrations exist,
    # pick the most recently updated one (arbitrary heuristic for legacy data).
    conn.execute(
        text("""
            UPDATE activities a
            SET
                integration_id = sub.integration_id,
                owner_user_id = sub.owner_user_id,
                visibility = sub.visibility
            FROM (
                SELECT
                    a2.id AS activity_id,
                    i.id AS integration_id,
                    i.user_id AS owner_user_id,
                    CASE WHEN i.share_synced_data THEN 'team'::varchar ELSE 'owner_only'::varchar END AS visibility
                FROM activities a2
                CROSS JOIN LATERAL (
                    SELECT id, user_id, share_synced_data
                    FROM integrations
                    WHERE organization_id = a2.organization_id
                      AND provider = a2.source_system
                      AND is_active = true
                      AND user_id IS NOT NULL
                    ORDER BY updated_at DESC NULLS LAST
                    LIMIT 1
                ) i
            ) sub
            WHERE a.id = sub.activity_id
        """)
    )

    # 5. For activities with no matching integration, visibility is already 'team'
    # from server_default; leave integration_id and owner_user_id NULL.

    # 6. Replace org_isolation RLS policy with org_and_user_isolation
    conn.execute(text("DROP POLICY IF EXISTS org_isolation ON activities"))
    conn.execute(
        text(f"""
            CREATE POLICY org_and_user_isolation ON activities
            FOR ALL
            USING (
                organization_id::text = COALESCE(
                    NULLIF(current_setting('app.current_org_id', true), ''),
                    '{NULL_UUID}'
                )
                AND (
                    visibility = 'team'
                    OR owner_user_id IS NULL
                    OR owner_user_id::text = COALESCE(
                        NULLIF(current_setting('app.current_user_id', true), ''),
                        '{NULL_UUID}'
                    )
                )
            )
        """)
    )


def downgrade() -> None:
    conn = op.get_bind()

    # Restore org_isolation policy
    conn.execute(text("DROP POLICY IF EXISTS org_and_user_isolation ON activities"))
    conn.execute(
        text(f"""
            CREATE POLICY org_isolation ON activities
            FOR ALL
            USING (
                organization_id::text = COALESCE(
                    NULLIF(current_setting('app.current_org_id', true), ''),
                    '{NULL_UUID}'
                )
            )
        """)
    )

    op.drop_index("ix_activities_owner_user_id", table_name="activities")
    op.drop_column("activities", "visibility")
    op.drop_column("activities", "owner_user_id")
    op.drop_index("ix_activities_integration_id", table_name="activities")
    op.drop_column("activities", "integration_id")
