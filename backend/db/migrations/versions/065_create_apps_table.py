"""Create apps table for interactive Penny mini-apps.

Separates apps from the artifacts table. Apps have dedicated columns
for queries (JSONB) and frontend_code (Text) instead of overloading
Artifact.config.

Revision ID: 065_create_apps_table
Revises: 064_add_conv_sandbox_id
Create Date: 2026-02-17
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "065_create_apps_table"
down_revision = "064_add_conv_sandbox_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "apps",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", onupdate="CASCADE"), nullable=False),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("queries", JSONB, nullable=False),
        sa.Column("frontend_code", sa.Text, nullable=False),
        sa.Column("conversation_id", UUID(as_uuid=True), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=True, index=True),
        sa.Column("message_id", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )

    # Enable RLS and grant access to the app role
    op.execute("ALTER TABLE apps ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY apps_org_isolation ON apps
        USING (organization_id = current_setting('app.current_org_id')::uuid)
    """)
    op.execute("GRANT ALL ON apps TO revtops_app")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS apps_org_isolation ON apps")
    op.drop_table("apps")
