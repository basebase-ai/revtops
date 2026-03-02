"""Lock guest identity updates and users-table writes for revtops_app.

Revision ID: 083_guest_locks
Revises: 082_guest_unique
Create Date: 2026-03-02
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "083_guest_locks"
down_revision: Union[str, None] = "082_guest_unique"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    assert len(revision) <= 32
    assert isinstance(down_revision, str) and len(down_revision) <= 32

    bind = op.get_bind()

    bind.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION prevent_guest_user_mutations()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                IF OLD.is_guest IS TRUE THEN
                    IF NEW.email IS DISTINCT FROM OLD.email THEN
                        RAISE EXCEPTION 'Guest user email is immutable';
                    END IF;

                    IF NEW.organization_id IS DISTINCT FROM OLD.organization_id THEN
                        RAISE EXCEPTION 'Guest user organization is immutable';
                    END IF;
                END IF;
                RETURN NEW;
            END;
            $$;
            """
        )
    )

    bind.execute(
        sa.text(
            """
            DROP TRIGGER IF EXISTS trg_prevent_guest_user_mutations ON users;
            CREATE TRIGGER trg_prevent_guest_user_mutations
            BEFORE UPDATE ON users
            FOR EACH ROW
            EXECUTE FUNCTION prevent_guest_user_mutations();
            """
        )
    )

    bind.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION prevent_guest_identity_mapping_mutations()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            DECLARE
                existing_is_guest boolean;
                incoming_is_guest boolean;
            BEGIN
                IF OLD.user_id IS NOT NULL THEN
                    SELECT is_guest INTO existing_is_guest FROM users WHERE id = OLD.user_id;
                    IF existing_is_guest IS TRUE THEN
                        IF NEW.user_id IS DISTINCT FROM OLD.user_id
                           OR NEW.revtops_email IS DISTINCT FROM OLD.revtops_email
                           OR NEW.external_email IS DISTINCT FROM OLD.external_email
                           OR NEW.match_source IS DISTINCT FROM OLD.match_source THEN
                            RAISE EXCEPTION 'Identity mappings linked to guest users are immutable';
                        END IF;
                    END IF;
                END IF;

                IF NEW.user_id IS NOT NULL AND NEW.user_id IS DISTINCT FROM OLD.user_id THEN
                    SELECT is_guest INTO incoming_is_guest FROM users WHERE id = NEW.user_id;
                    IF incoming_is_guest IS TRUE THEN
                        RAISE EXCEPTION 'Identity mappings cannot be linked to guest users';
                    END IF;
                END IF;

                RETURN NEW;
            END;
            $$;
            """
        )
    )

    bind.execute(
        sa.text(
            """
            DROP TRIGGER IF EXISTS trg_prevent_guest_identity_mapping_mutations ON user_mappings_for_identity;
            CREATE TRIGGER trg_prevent_guest_identity_mapping_mutations
            BEFORE UPDATE ON user_mappings_for_identity
            FOR EACH ROW
            EXECUTE FUNCTION prevent_guest_identity_mapping_mutations();
            """
        )
    )

    bind.execute(
        sa.text(
            """
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'revtops_app') THEN
                    REVOKE INSERT, UPDATE, DELETE ON TABLE users FROM revtops_app;
                END IF;
            END $$;
            """
        )
    )


def downgrade() -> None:
    bind = op.get_bind()

    bind.execute(
        sa.text(
            """
            DROP TRIGGER IF EXISTS trg_prevent_guest_identity_mapping_mutations ON user_mappings_for_identity;
            DROP FUNCTION IF EXISTS prevent_guest_identity_mapping_mutations();
            """
        )
    )

    bind.execute(
        sa.text(
            """
            DROP TRIGGER IF EXISTS trg_prevent_guest_user_mutations ON users;
            DROP FUNCTION IF EXISTS prevent_guest_user_mutations();
            """
        )
    )

    bind.execute(
        sa.text(
            """
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'revtops_app') THEN
                    GRANT INSERT, UPDATE, DELETE ON TABLE users TO revtops_app;
                END IF;
            END $$;
            """
        )
    )
