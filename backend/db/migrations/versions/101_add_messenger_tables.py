"""Add generic messenger_user_mappings and messenger_bot_installs tables.

Replaces the Slack-specific ``user_mappings_for_identity`` and
``slack_bot_installs`` tables with platform-generic equivalents.

Data is copied from the old tables with ``platform='slack'``.
Phone-based mappings are seeded from ``users.phone_number``.
Old tables are kept for backward compatibility and will be dropped
in a subsequent migration once all code is updated.

Revision ID: 101_messenger_tables
Revises: 100_meet_space_fields
Create Date: 2026-03-13
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "101_messenger_tables"
down_revision: Union[str, None] = "100_meet_space_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── messenger_user_mappings ───────────────────────────────────────
    op.create_table(
        "messenger_user_mappings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("platform", sa.String(30), nullable=False),
        sa.Column("workspace_id", sa.String(100), nullable=True),
        sa.Column("external_user_id", sa.String(255), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", onupdate="CASCADE", ondelete="CASCADE"), nullable=False),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("external_email", sa.String(255), nullable=True),
        sa.Column("match_source", sa.String(50), nullable=True),
        sa.Column("verified_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_messenger_user_mappings_platform_extid",
        "messenger_user_mappings",
        ["platform", "external_user_id"],
    )
    op.create_index(
        "ix_messenger_user_mappings_user_id",
        "messenger_user_mappings",
        ["user_id"],
    )
    op.create_index(
        "ix_messenger_user_mappings_org_platform",
        "messenger_user_mappings",
        ["organization_id", "platform"],
    )
    op.create_unique_constraint(
        "uq_messenger_user_mappings_platform_ws_extid",
        "messenger_user_mappings",
        ["platform", "workspace_id", "external_user_id"],
    )

    # ── messenger_bot_installs ────────────────────────────────────────
    op.create_table(
        "messenger_bot_installs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("platform", sa.String(30), nullable=False),
        sa.Column("workspace_id", sa.String(100), nullable=False),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("access_token_encrypted", sa.Text(), nullable=False),
        sa.Column("extra_data", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_messenger_bot_installs_workspace_id",
        "messenger_bot_installs",
        ["workspace_id"],
    )
    op.create_index(
        "ix_messenger_bot_installs_organization_id",
        "messenger_bot_installs",
        ["organization_id"],
    )
    op.create_unique_constraint(
        "uq_messenger_bot_installs_platform_ws",
        "messenger_bot_installs",
        ["platform", "workspace_id"],
    )

    # ── Migrate existing Slack user mappings ──────────────────────────
    op.execute(sa.text("""
        INSERT INTO messenger_user_mappings
            (platform, workspace_id, external_user_id, user_id, organization_id,
             external_email, match_source, created_at, updated_at)
        SELECT
            'slack',
            NULL,
            external_userid,
            user_id,
            organization_id,
            external_email,
            match_source,
            created_at,
            updated_at
        FROM user_mappings_for_identity
        WHERE user_id IS NOT NULL
          AND external_userid IS NOT NULL
        ON CONFLICT (platform, workspace_id, external_user_id)
        DO NOTHING
    """))

    # ── Migrate existing Slack bot installs ───────────────────────────
    op.execute(sa.text("""
        INSERT INTO messenger_bot_installs
            (platform, workspace_id, organization_id, access_token_encrypted,
             extra_data, created_at, updated_at)
        SELECT
            'slack',
            team_id,
            organization_id,
            access_token_encrypted,
            '{}',
            created_at,
            updated_at
        FROM slack_bot_installs
        ON CONFLICT (platform, workspace_id)
        DO NOTHING
    """))

    # ── Seed phone-based mappings from users.phone_number ─────────────
    op.execute(sa.text("""
        INSERT INTO messenger_user_mappings
            (platform, workspace_id, external_user_id, user_id, organization_id,
             created_at, updated_at)
        SELECT
            'sms',
            NULL,
            u.phone_number,
            u.id,
            om.organization_id,
            NOW(),
            NOW()
        FROM users u
        JOIN org_members om ON om.user_id = u.id AND om.status IN ('active', 'onboarding')
        WHERE u.phone_number IS NOT NULL
          AND u.phone_number != ''
        ON CONFLICT (platform, workspace_id, external_user_id)
        DO NOTHING
    """))

    op.execute(sa.text("""
        INSERT INTO messenger_user_mappings
            (platform, workspace_id, external_user_id, user_id, organization_id,
             created_at, updated_at)
        SELECT
            'whatsapp',
            NULL,
            u.phone_number,
            u.id,
            om.organization_id,
            NOW(),
            NOW()
        FROM users u
        JOIN org_members om ON om.user_id = u.id AND om.status IN ('active', 'onboarding')
        WHERE u.phone_number IS NOT NULL
          AND u.phone_number != ''
        ON CONFLICT (platform, workspace_id, external_user_id)
        DO NOTHING
    """))


def downgrade() -> None:
    op.drop_table("messenger_bot_installs")
    op.drop_table("messenger_user_mappings")
