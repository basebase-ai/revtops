"""Backfill org handles and make handle NOT NULL.

Revision ID: 092_org_handle_not_null
Revises: 091_add_app_compiled_code
Create Date: 2026-03-06

"""
from __future__ import annotations

import re
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision: str = "092_org_handle_not_null"
down_revision: Union[str, None] = "091_add_app_compiled_code"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _slugify(s: str) -> str:
    """Convert to URL-safe handle (e.g. orangeco.com -> orangeco)."""
    s = (s or "").strip().lower()
    s = re.sub(r"\.(com|co|io|org|net|ai|app|dev|xyz|tech)(\.[a-z]{2})?$", "", s)
    s = re.sub(r"[^a-z0-9-]", "", s)
    return s[:64] if s else "org"


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        text("SELECT id, name, email_domain FROM organizations WHERE handle IS NULL")
    ).fetchall()

    used: set[str] = set()
    existing = conn.execute(text("SELECT handle FROM organizations WHERE handle IS NOT NULL")).fetchall()
    used.update((r[0].lower() for r in existing if r[0]))

    for row in rows:
        org_id, name, email_domain = row
        base = _slugify(email_domain or name or "org")
        handle = base
        n = 2
        while handle.lower() in used:
            handle = f"{base}-{n}"
            n += 1
        used.add(handle.lower())
        conn.execute(
            text("UPDATE organizations SET handle = :h WHERE id = :id"),
            {"h": handle, "id": str(org_id)},
        )

    op.alter_column(
        "organizations",
        "handle",
        existing_type=sa.String(64),
        nullable=False,
    )
    # Replace partial unique index with plain unique (all handles now non-null)
    op.drop_index(
        "ix_organizations_handle_unique",
        table_name="organizations",
        postgresql_where=sa.text("handle IS NOT NULL"),
    )
    op.create_index(
        "ix_organizations_handle_unique",
        "organizations",
        ["handle"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_organizations_handle_unique", table_name="organizations")
    op.create_index(
        "ix_organizations_handle_unique",
        "organizations",
        ["handle"],
        unique=True,
        postgresql_where=sa.text("handle IS NOT NULL"),
    )
    op.alter_column(
        "organizations",
        "handle",
        existing_type=sa.String(64),
        nullable=True,
    )
