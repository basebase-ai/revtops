"""Enforce one guest user per organization.

Revision ID: 082_guest_unique
Revises: 081_org_guest_user
Create Date: 2026-03-02

"""
from __future__ import annotations

from collections import defaultdict
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "082_guest_unique"
down_revision: Union[str, None] = "081_org_guest_user"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    assert len(revision) <= 32
    assert isinstance(down_revision, str) and len(down_revision) <= 32

    bind = op.get_bind()

    guest_rows = bind.execute(
        sa.text(
            """
            SELECT id, organization_id, created_at
            FROM users
            WHERE is_guest = true
              AND organization_id IS NOT NULL
            ORDER BY organization_id, created_at NULLS LAST, id
            """
        )
    ).fetchall()

    guests_by_org: dict[str, list[str]] = defaultdict(list)
    for row in guest_rows:
        guests_by_org[str(row.organization_id)].append(str(row.id))

    for org_id in sorted(guests_by_org):
        guest_ids = guests_by_org[org_id]
        if len(guest_ids) <= 1:
            continue

        configured_guest_id = bind.execute(
            sa.text("SELECT guest_user_id FROM organizations WHERE id = :org_id"),
            {"org_id": org_id},
        ).scalar_one_or_none()
        configured_guest = str(configured_guest_id) if configured_guest_id else None
        canonical_guest = configured_guest if configured_guest in guest_ids else guest_ids[0]

        bind.execute(
            sa.text("UPDATE organizations SET guest_user_id = :guest_user_id WHERE id = :org_id"),
            {"guest_user_id": canonical_guest, "org_id": org_id},
        )

        duplicate_ids = [guest_id for guest_id in guest_ids if guest_id != canonical_guest]
        if duplicate_ids:
            bind.execute(
                sa.text(
                    """
                    DELETE FROM org_members
                    WHERE organization_id = :org_id
                      AND user_id = ANY(CAST(:user_ids AS uuid[]))
                    """
                ),
                {"org_id": org_id, "user_ids": duplicate_ids},
            )
            bind.execute(
                sa.text(
                    """
                    UPDATE users
                    SET is_guest = false
                    WHERE id = ANY(CAST(:user_ids AS uuid[]))
                    """
                ),
                {"user_ids": duplicate_ids},
            )

    op.create_index(
        "uq_users_one_guest_per_org",
        "users",
        ["organization_id"],
        unique=True,
        postgresql_where=sa.text("is_guest = true AND organization_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_users_one_guest_per_org", table_name="users")
