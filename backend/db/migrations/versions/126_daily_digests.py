"""Create daily_digests table for per-member daily activity summaries.

Revision ID: 126_daily_digests
Revises: 125_conversation_summary_plaintext
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "126_daily_digests"
down_revision: Union[str, None] = "125_conversation_summary_plaintext"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "daily_digests",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE", onupdate="CASCADE"),
            nullable=False,
        ),
        sa.Column("digest_date", sa.Date(), nullable=False),
        sa.Column("summary", postgresql.JSONB, nullable=False),
        sa.Column("raw_data", postgresql.JSONB, nullable=True),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "organization_id",
            "user_id",
            "digest_date",
            name="uq_daily_digests_org_user_date",
        ),
    )
    op.create_index(
        "ix_daily_digests_org_date",
        "daily_digests",
        ["organization_id", "digest_date"],
    )

    op.execute("ALTER TABLE daily_digests ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY daily_digests_org_isolation ON daily_digests
        FOR ALL
        USING (organization_id = current_setting('app.current_org_id')::uuid)
    """)
    op.execute("GRANT ALL ON daily_digests TO revtops_app")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS daily_digests_org_isolation ON daily_digests")
    op.drop_index("ix_daily_digests_org_date", table_name="daily_digests")
    op.drop_table("daily_digests")
