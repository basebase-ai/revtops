"""Make organizations.email_domain non-unique.

Revision ID: 079_org_domain_nonunique
Revises: 078_slack_bot_installs
Create Date: 2026-02-28

"""
from typing import Sequence, Union

from alembic import op


revision: str = "079_org_domain_nonunique"
down_revision: Union[str, None] = "078_slack_bot_installs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'organizations_email_domain_key'
                  AND conrelid = 'organizations'::regclass
            ) THEN
                ALTER TABLE organizations
                DROP CONSTRAINT organizations_email_domain_key;
            END IF;
        END
        $$;
        """
    )

    op.drop_index("ix_organizations_email_domain", table_name="organizations", if_exists=True)
    op.create_index("ix_organizations_email_domain", "organizations", ["email_domain"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_organizations_email_domain", table_name="organizations", if_exists=True)
    op.create_index("ix_organizations_email_domain", "organizations", ["email_domain"], unique=True)
