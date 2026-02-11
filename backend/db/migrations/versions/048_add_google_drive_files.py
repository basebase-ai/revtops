"""Add google_drive_files table for synced Drive metadata.

Revision ID: 048
Revises: 047
Create Date: 2026-02-11

Stores file metadata from a user's Google Drive so the agent can
search files by name without hitting the Google API every time.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "048"
down_revision = "047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "google_drive_files",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("google_file_id", sa.String(255), nullable=False),
        sa.Column("name", sa.String(1024), nullable=False, server_default=""),
        sa.Column("mime_type", sa.String(255), nullable=False, server_default=""),
        sa.Column("parent_google_id", sa.String(255), nullable=True),
        sa.Column("folder_path", sa.Text(), nullable=False, server_default="/"),
        sa.Column("web_view_link", sa.String(1024), nullable=True),
        sa.Column("file_size", sa.BigInteger(), nullable=True),
        sa.Column("google_modified_at", sa.DateTime(), nullable=True),
        sa.Column("synced_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
    )
    op.create_index("idx_gdrive_org_user", "google_drive_files", ["organization_id", "user_id"])
    op.create_index(
        "uq_gdrive_org_user_fileid",
        "google_drive_files",
        ["organization_id", "user_id", "google_file_id"],
        unique=True,
    )
    op.create_index("idx_gdrive_name_trgm", "google_drive_files", ["name"])


def downgrade() -> None:
    op.drop_table("google_drive_files")
