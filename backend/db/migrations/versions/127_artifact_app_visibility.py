"""Add visibility (private/team/public) to artifacts and apps; replace RLS policies.

Revision ID: 127_artifact_app_visibility
Revises: 126_daily_digests
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "127_artifact_app_visibility"
down_revision: Union[str, None] = "126_daily_digests"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Matches 123_rls_with_check_writables / org isolation pattern
_ORG_MATCH: str = """
organization_id::text = COALESCE(
    NULLIF(current_setting('app.current_org_id', true), ''),
    '00000000-0000-0000-0000-000000000000'
)
"""

_USER_MATCH: str = """
user_id::text = COALESCE(
    NULLIF(current_setting('app.current_user_id', true), ''),
    '00000000-0000-0000-0000-000000000000'
)
"""


def _artifact_select_expr() -> str:
    return f"""(
    (visibility = 'public')
    OR (
        visibility = 'team'
        AND {_ORG_MATCH.strip()}
    )
    OR (
        visibility = 'private'
        AND {_ORG_MATCH.strip()}
        AND user_id IS NOT NULL
        AND {_USER_MATCH.strip()}
    )
)"""


def _app_select_expr() -> str:
    return f"""(
    (visibility = 'public')
    OR (
        visibility = 'team'
        AND {_ORG_MATCH.strip()}
    )
    OR (
        visibility = 'private'
        AND {_ORG_MATCH.strip()}
        AND {_USER_MATCH.strip()}
    )
)"""


def _insert_check_expr() -> str:
    """Private rows must be owned by current session user."""
    return f"""(
    {_ORG_MATCH.strip()}
    AND (
        visibility != 'private'
        OR (
            user_id IS NOT NULL
            AND {_USER_MATCH.strip()}
        )
    )
)"""


def _update_with_check_expr() -> str:
    return _insert_check_expr()


def upgrade() -> None:
    bind = op.get_bind()

    # --- columns + constraints + indexes
    bind.execute(
        sa.text(
            """
            ALTER TABLE artifacts
            ADD COLUMN IF NOT EXISTS visibility TEXT NOT NULL DEFAULT 'team'
            """
        )
    )
    bind.execute(
        sa.text(
            """
            ALTER TABLE artifacts
            ADD CONSTRAINT artifacts_visibility_check
            CHECK (visibility IN ('private', 'team', 'public'))
            """
        )
    )
    bind.execute(
        sa.text(
            """
            ALTER TABLE artifacts
            ADD CONSTRAINT artifacts_chk_private_needs_owner
            CHECK (visibility != 'private' OR user_id IS NOT NULL)
            """
        )
    )
    bind.execute(
        sa.text(
            """
            CREATE INDEX IF NOT EXISTS idx_artifacts_visibility_public
            ON artifacts (visibility) WHERE visibility = 'public'
            """
        )
    )

    bind.execute(
        sa.text(
            """
            ALTER TABLE apps
            ADD COLUMN IF NOT EXISTS visibility TEXT NOT NULL DEFAULT 'team'
            """
        )
    )
    bind.execute(
        sa.text(
            """
            ALTER TABLE apps
            ADD CONSTRAINT apps_visibility_check
            CHECK (visibility IN ('private', 'team', 'public'))
            """
        )
    )
    bind.execute(
        sa.text(
            """
            ALTER TABLE apps
            ADD CONSTRAINT apps_chk_private_needs_owner
            CHECK (visibility != 'private' OR user_id IS NOT NULL)
            """
        )
    )
    bind.execute(
        sa.text(
            """
            CREATE INDEX IF NOT EXISTS idx_apps_visibility_public
            ON apps (visibility) WHERE visibility = 'public'
            """
        )
    )

    # --- artifacts RLS: drop old policy, add command-specific policies
    bind.execute(sa.text("DROP POLICY IF EXISTS org_isolation ON artifacts"))

    sel_a: str = _artifact_select_expr()
    ins_chk: str = _insert_check_expr()
    upd_chk: str = _update_with_check_expr()

    bind.execute(
        sa.text(
            f"""
            CREATE POLICY artifacts_select ON artifacts
            FOR SELECT
            USING ({sel_a})
            """
        )
    )
    bind.execute(
        sa.text(
            f"""
            CREATE POLICY artifacts_insert ON artifacts
            FOR INSERT
            WITH CHECK ({ins_chk})
            """
        )
    )
    bind.execute(
        sa.text(
            f"""
            CREATE POLICY artifacts_update ON artifacts
            FOR UPDATE
            USING ({sel_a})
            WITH CHECK ({upd_chk})
            """
        )
    )
    bind.execute(
        sa.text(
            f"""
            CREATE POLICY artifacts_delete ON artifacts
            FOR DELETE
            USING ({sel_a})
            """
        )
    )

    # --- apps RLS
    bind.execute(sa.text("DROP POLICY IF EXISTS apps_org_isolation ON apps"))

    sel_app: str = _app_select_expr()

    bind.execute(
        sa.text(
            f"""
            CREATE POLICY apps_select ON apps
            FOR SELECT
            USING ({sel_app})
            """
        )
    )
    bind.execute(
        sa.text(
            f"""
            CREATE POLICY apps_insert ON apps
            FOR INSERT
            WITH CHECK ({ins_chk})
            """
        )
    )
    bind.execute(
        sa.text(
            f"""
            CREATE POLICY apps_update ON apps
            FOR UPDATE
            USING ({sel_app})
            WITH CHECK ({upd_chk})
            """
        )
    )
    bind.execute(
        sa.text(
            f"""
            CREATE POLICY apps_delete ON apps
            FOR DELETE
            USING ({sel_app})
            """
        )
    )


def downgrade() -> None:
    bind = op.get_bind()

    # apps policies -> restore single FOR ALL
    bind.execute(sa.text("DROP POLICY IF EXISTS apps_select ON apps"))
    bind.execute(sa.text("DROP POLICY IF EXISTS apps_insert ON apps"))
    bind.execute(sa.text("DROP POLICY IF EXISTS apps_update ON apps"))
    bind.execute(sa.text("DROP POLICY IF EXISTS apps_delete ON apps"))
    bind.execute(
        sa.text(
            """
            CREATE POLICY apps_org_isolation ON apps
            FOR ALL
            USING (organization_id = current_setting('app.current_org_id')::uuid)
            """
        )
    )

    # artifacts -> restore 123-style org_isolation
    bind.execute(sa.text("DROP POLICY IF EXISTS artifacts_select ON artifacts"))
    bind.execute(sa.text("DROP POLICY IF EXISTS artifacts_insert ON artifacts"))
    bind.execute(sa.text("DROP POLICY IF EXISTS artifacts_update ON artifacts"))
    bind.execute(sa.text("DROP POLICY IF EXISTS artifacts_delete ON artifacts"))
    std_org: str = _ORG_MATCH.strip()
    bind.execute(
        sa.text(
            f"""
            CREATE POLICY org_isolation ON artifacts
            FOR ALL
            USING ({std_org})
            WITH CHECK ({std_org})
            """
        )
    )

    bind.execute(sa.text("DROP INDEX IF EXISTS idx_apps_visibility_public"))
    bind.execute(sa.text("ALTER TABLE apps DROP CONSTRAINT IF EXISTS apps_chk_private_needs_owner"))
    bind.execute(sa.text("ALTER TABLE apps DROP CONSTRAINT IF EXISTS apps_visibility_check"))
    bind.execute(sa.text("ALTER TABLE apps DROP COLUMN IF EXISTS visibility"))

    bind.execute(sa.text("DROP INDEX IF EXISTS idx_artifacts_visibility_public"))
    bind.execute(sa.text("ALTER TABLE artifacts DROP CONSTRAINT IF EXISTS artifacts_chk_private_needs_owner"))
    bind.execute(sa.text("ALTER TABLE artifacts DROP CONSTRAINT IF EXISTS artifacts_visibility_check"))
    bind.execute(sa.text("ALTER TABLE artifacts DROP COLUMN IF EXISTS visibility"))
