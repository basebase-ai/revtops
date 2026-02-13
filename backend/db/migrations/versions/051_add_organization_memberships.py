"""Add organization_memberships table for multi-org support.

Revision ID: 051
Revises: 050
Create Date: 2026-02-11

Enables users to belong to multiple organizations. Backfills
existing User.organization_id values into membership rows.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import UUID

revision = "051"
down_revision = "050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Create the table
    op.create_table(
        "organization_memberships",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(50), nullable=False, server_default="member"),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("invited_by_user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("invited_at", sa.DateTime(), nullable=True),
        sa.Column("joined_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.UniqueConstraint("user_id", "organization_id", name="uq_membership_user_org"),
    )
    op.create_index("idx_org_memberships_user_id", "organization_memberships", ["user_id"])
    op.create_index("idx_org_memberships_org_id", "organization_memberships", ["organization_id"])

    # 2. Backfill from existing users
    conn = op.get_bind()
    conn.execute(text("""
        INSERT INTO organization_memberships (user_id, organization_id, role, status, joined_at, created_at)
        SELECT
            u.id,
            u.organization_id,
            COALESCE(NULLIF(u.role, ''), 'member'),
            'active',
            u.created_at,
            NOW()
        FROM users u
        WHERE u.organization_id IS NOT NULL
          AND u.status IN ('active', 'invited')
        ON CONFLICT (user_id, organization_id) DO NOTHING
    """))

    # 3. Enable RLS
    conn.execute(text("ALTER TABLE organization_memberships ENABLE ROW LEVEL SECURITY"))
    conn.execute(text("ALTER TABLE organization_memberships FORCE ROW LEVEL SECURITY"))
    conn.execute(text("DROP POLICY IF EXISTS org_isolation ON organization_memberships"))
    conn.execute(text("""
        CREATE POLICY org_isolation ON organization_memberships
        FOR ALL
        USING (
            organization_id::text = COALESCE(
                NULLIF(current_setting('app.current_org_id', true), ''),
                '00000000-0000-0000-0000-000000000000'
            )
        )
    """))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("DROP POLICY IF EXISTS org_isolation ON organization_memberships"))
    op.drop_table("organization_memberships")
