"""Seed org admins from first joined member.

Revision ID: 084_org_admin_seed
Revises: 084_app_archived_at_index
Create Date: 2026-03-02
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "084_org_admin_seed"
down_revision: Union[str, None] = "084_app_archived_at_index"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    assert len(revision) <= 32
    assert isinstance(down_revision, str) and len(down_revision) <= 32

    bind = op.get_bind()

    bind.execute(
        sa.text(
            """
            WITH active_admin_orgs AS (
                SELECT DISTINCT om.organization_id
                FROM org_members om
                JOIN users u ON u.id = om.user_id
                WHERE om.status = 'active'
                  AND om.role = 'admin'
                  AND COALESCE(u.is_guest, FALSE) = FALSE
            ),
            ranked_members AS (
                SELECT
                    om.id,
                    om.user_id,
                    om.organization_id,
                    ROW_NUMBER() OVER (
                        PARTITION BY om.organization_id
                        ORDER BY om.joined_at ASC NULLS LAST, om.created_at ASC NULLS LAST, om.id ASC
                    ) AS rn
                FROM org_members om
                JOIN users u ON u.id = om.user_id
                WHERE om.status = 'active'
                  AND COALESCE(u.is_guest, FALSE) = FALSE
                  AND om.organization_id NOT IN (SELECT organization_id FROM active_admin_orgs)
            ),
            promoted AS (
                UPDATE org_members om
                SET role = 'admin'
                FROM ranked_members rm
                WHERE om.id = rm.id
                  AND rm.rn = 1
                RETURNING rm.user_id, rm.organization_id
            )
            UPDATE users u
            SET role = 'admin'
            FROM promoted p
            WHERE u.id = p.user_id
              AND u.organization_id = p.organization_id;
            """
        )
    )


def downgrade() -> None:
    # Data migration is not safely reversible.
    pass
