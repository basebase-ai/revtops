"""Create daily_team_summaries table for org-wide daily team summaries.

Revision ID: 128_daily_team_summaries
Revises: 127_artifact_app_visibility
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "128_daily_team_summaries"
down_revision: Union[str, None] = "127_artifact_app_visibility"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "daily_team_summaries",
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
        sa.Column("digest_date", sa.Date(), nullable=False),
        sa.Column("summary_text", sa.Text(), nullable=False),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "organization_id",
            "digest_date",
            name="uq_daily_team_summaries_org_date",
        ),
    )
    op.create_index(
        "ix_daily_team_summaries_org_date",
        "daily_team_summaries",
        ["organization_id", "digest_date"],
    )

    op.execute("ALTER TABLE daily_team_summaries ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY daily_team_summaries_org_isolation ON daily_team_summaries
        FOR ALL
        USING (organization_id = current_setting('app.current_org_id')::uuid)
    """)
    op.execute("GRANT ALL ON daily_team_summaries TO revtops_app")


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS daily_team_summaries_org_isolation ON daily_team_summaries"
    )
    op.drop_index("ix_daily_team_summaries_org_date", table_name="daily_team_summaries")
    op.drop_table("daily_team_summaries")
