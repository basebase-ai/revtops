"""Add unique constraint on users.phone_number for SMS-based auth.

Revision ID: 061_add_unique_phone_number
Revises: 060_rename_to_org_members
Create Date: 2026-02-15
"""

from alembic import op

revision = "061_add_unique_phone_number"
down_revision = "060_rename_to_org_members"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_users_phone_number", "users", ["phone_number"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_users_phone_number", "users", type_="unique")
