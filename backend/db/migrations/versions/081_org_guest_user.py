"""Add org guest users and guest toggle.

Revision ID: 081_org_guest_user
Revises: 080_app_archived_at
Create Date: 2026-03-01

"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "081_org_guest_user"
down_revision: Union[str, None] = "080_app_archived_at"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    assert len(revision) <= 32
    assert isinstance(down_revision, str) and len(down_revision) <= 32

    op.add_column("users", sa.Column("is_guest", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("organizations", sa.Column("guest_user_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column(
        "organizations",
        sa.Column("guest_user_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_foreign_key(
        "fk_organizations_guest_user_id",
        "organizations",
        "users",
        ["guest_user_id"],
        ["id"],
        ondelete="SET NULL",
        onupdate="CASCADE",
    )

    bind = op.get_bind()
    org_rows = bind.execute(sa.text("SELECT id FROM organizations ORDER BY id")).fetchall()
    for (org_id,) in org_rows:
        guest_id = uuid.uuid4()
        guest_email = f"guest+{org_id}@guest.basebase.local"
        bind.execute(
            sa.text(
                """
                INSERT INTO users (id, email, name, organization_id, role, status, roles, is_guest, created_at)
                VALUES (:id, :email, :name, :organization_id, :role, :status, CAST(:roles AS jsonb), :is_guest, :created_at)
                ON CONFLICT (email) DO NOTHING
                """
            ),
            {
                "id": str(guest_id),
                "email": guest_email,
                "name": "Guest user",
                "organization_id": str(org_id),
                "role": "member",
                "status": "active",
                "roles": "[]",
                "is_guest": True,
                "created_at": datetime.utcnow(),
            },
        )
        guest_row = bind.execute(
            sa.text("SELECT id FROM users WHERE email = :email"),
            {"email": guest_email},
        ).first()
        if not guest_row:
            continue

        bind.execute(
            sa.text(
                """
                INSERT INTO org_members (id, user_id, organization_id, role, status, joined_at, created_at)
                VALUES (:id, :user_id, :organization_id, :role, :status, :joined_at, :created_at)
                ON CONFLICT ON CONSTRAINT uq_membership_user_org DO NOTHING
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "user_id": str(guest_row.id),
                "organization_id": str(org_id),
                "role": "member",
                "status": "active",
                "joined_at": datetime.utcnow(),
                "created_at": datetime.utcnow(),
            },
        )
        bind.execute(
            sa.text("UPDATE organizations SET guest_user_id = :guest_user_id WHERE id = :org_id"),
            {"guest_user_id": str(guest_row.id), "org_id": str(org_id)},
        )

    op.alter_column("users", "is_guest", server_default=None)
    op.alter_column("organizations", "guest_user_enabled", server_default=None)


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM org_members WHERE user_id IN (SELECT id FROM users WHERE is_guest = true)"))
    bind.execute(sa.text("DELETE FROM users WHERE is_guest = true"))

    op.drop_constraint("fk_organizations_guest_user_id", "organizations", type_="foreignkey")
    op.drop_column("organizations", "guest_user_enabled")
    op.drop_column("organizations", "guest_user_id")
    op.drop_column("users", "is_guest")
