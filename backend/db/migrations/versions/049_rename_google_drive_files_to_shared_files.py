"""Rename google_drive_files to shared_files with generic column names.

Revision ID: 049
Revises: 048
Create Date: 2026-02-11

Generalises the table so it can store file metadata from any source
(Google Drive, Airtable, OneDrive, etc.) via a new `source` column.
"""
from alembic import op
import sqlalchemy as sa

revision = "049"
down_revision = "048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Rename table
    op.rename_table("google_drive_files", "shared_files")

    # 2. Add source column (defaults to google_drive for existing rows)
    op.add_column(
        "shared_files",
        sa.Column("source", sa.String(64), nullable=False, server_default="google_drive"),
    )

    # 3. Rename Google-specific columns to generic names
    op.alter_column("shared_files", "google_file_id", new_column_name="external_id")
    op.alter_column("shared_files", "parent_google_id", new_column_name="parent_external_id")
    op.alter_column("shared_files", "google_modified_at", new_column_name="source_modified_at")

    # 4. Drop old indexes
    op.drop_index("idx_gdrive_org_user", table_name="shared_files")
    op.drop_index("uq_gdrive_org_user_fileid", table_name="shared_files")
    op.drop_index("idx_gdrive_name_trgm", table_name="shared_files")

    # 5. Create new indexes with source-aware names
    op.create_index("idx_shared_files_org_user", "shared_files", ["organization_id", "user_id"])
    op.create_index(
        "uq_shared_files_org_user_source_extid",
        "shared_files",
        ["organization_id", "user_id", "source", "external_id"],
        unique=True,
    )
    op.create_index("idx_shared_files_name_trgm", "shared_files", ["name"])
    op.create_index("idx_shared_files_source", "shared_files", ["source"])


def downgrade() -> None:
    # Drop new indexes
    op.drop_index("idx_shared_files_source", table_name="shared_files")
    op.drop_index("idx_shared_files_name_trgm", table_name="shared_files")
    op.drop_index("uq_shared_files_org_user_source_extid", table_name="shared_files")
    op.drop_index("idx_shared_files_org_user", table_name="shared_files")

    # Rename columns back
    op.alter_column("shared_files", "external_id", new_column_name="google_file_id")
    op.alter_column("shared_files", "parent_external_id", new_column_name="parent_google_id")
    op.alter_column("shared_files", "source_modified_at", new_column_name="google_modified_at")

    # Drop source column
    op.drop_column("shared_files", "source")

    # Rename table back
    op.rename_table("shared_files", "google_drive_files")

    # Recreate old indexes
    op.create_index("idx_gdrive_org_user", "google_drive_files", ["organization_id", "user_id"])
    op.create_index(
        "uq_gdrive_org_user_fileid",
        "google_drive_files",
        ["organization_id", "user_id", "google_file_id"],
        unique=True,
    )
    op.create_index("idx_gdrive_name_trgm", "google_drive_files", ["name"])
