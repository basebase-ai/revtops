"""Add ON DELETE CASCADE/SET NULL to all FK constraints referencing users.id.

Revision ID: 087_user_fk_on_delete
Revises: 086_add_org_website_url
Create Date: 2026-03-03

Enables a simple DELETE FROM users to cascade automatically via Postgres,
eliminating the need for a hardcoded list of manual delete/nullify statements.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

revision: str = "087_user_fk_on_delete"
down_revision: Union[str, None] = "086_add_org_website_url"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_FIND_USER_FKS: str = """
SELECT
    tc.constraint_name,
    tc.table_schema,
    tc.table_name,
    kcu.column_name,
    rc.delete_rule
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage kcu
    ON tc.constraint_name = kcu.constraint_name
    AND tc.table_schema = kcu.table_schema
JOIN information_schema.referential_constraints rc
    ON tc.constraint_name = rc.constraint_name
    AND tc.table_schema = rc.constraint_schema
JOIN information_schema.constraint_column_usage ccu
    ON rc.unique_constraint_name = ccu.constraint_name
    AND rc.unique_constraint_schema = ccu.constraint_schema
WHERE tc.constraint_type = 'FOREIGN KEY'
    AND ccu.table_name = 'users'
    AND ccu.column_name = 'id'
    AND tc.table_schema = 'public'
ORDER BY tc.table_name, kcu.column_name
"""

# (table_name, column_name) -> desired ON DELETE action
# CASCADE: user-owned data, delete with user
# SET NULL: shared data, keep row but clear reference (column must be nullable)
_DESIRED_ON_DELETE: dict[tuple[str, str], str] = {
    ("accounts", "owner_id"): "SET NULL",
    ("accounts", "updated_by"): "SET NULL",
    ("activities", "created_by_id"): "SET NULL",
    ("activities", "updated_by"): "SET NULL",
    ("agent_tasks", "user_id"): "CASCADE",
    ("apps", "user_id"): "CASCADE",
    ("artifacts", "user_id"): "SET NULL",
    ("bulk_operations", "user_id"): "SET NULL",
    ("change_sessions", "user_id"): "SET NULL",
    ("change_sessions", "resolved_by"): "SET NULL",
    ("chat_messages", "user_id"): "CASCADE",
    ("contacts", "updated_by"): "SET NULL",
    ("conversations", "user_id"): "CASCADE",
    ("credit_transactions", "user_id"): "CASCADE",
    ("deals", "owner_id"): "SET NULL",
    ("deals", "updated_by"): "SET NULL",
    ("github_commits", "user_id"): "CASCADE",
    ("github_pull_requests", "user_id"): "CASCADE",
    ("goals", "owner_id"): "SET NULL",
    ("integrations", "user_id"): "SET NULL",
    ("integrations", "connected_by_user_id"): "SET NULL",
    ("memories", "created_by_user_id"): "SET NULL",
    ("org_members", "user_id"): "CASCADE",
    ("org_members", "invited_by_user_id"): "SET NULL",
    ("organizations", "guest_user_id"): "SET NULL",
    ("organizations", "token_owner_user_id"): "SET NULL",
    ("pending_operations", "user_id"): "CASCADE",
    ("shared_files", "user_id"): "CASCADE",
    ("sheet_imports", "user_id"): "CASCADE",
    ("temp_data", "created_by_user_id"): "CASCADE",
    ("tracker_issues", "user_id"): "SET NULL",
    ("user_mappings_for_identity", "user_id"): "CASCADE",
    ("user_tool_settings", "user_id"): "CASCADE",
    ("workflows", "created_by_user_id"): "CASCADE",
}


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(text(_FIND_USER_FKS)).fetchall()

    for constraint_name, schema, table, column, delete_rule in rows:
        key = (table, column)
        on_delete: str = _DESIRED_ON_DELETE.get(key, "NO ACTION")
        if delete_rule == on_delete:
            continue  # Already at desired state

        op.drop_constraint(constraint_name, table, schema=schema, type_="foreignkey")
        op.create_foreign_key(
            constraint_name,
            table,
            "users",
            [column],
            ["id"],
            source_schema=schema,
            referent_schema=schema,
            onupdate="CASCADE",
            ondelete=on_delete,
        )


def downgrade() -> None:
    # Restore NO ACTION for constraints we changed to CASCADE/SET NULL
    conn = op.get_bind()
    rows = conn.execute(text(_FIND_USER_FKS)).fetchall()

    for constraint_name, schema, table, column, delete_rule in rows:
        key = (table, column)
        desired = _DESIRED_ON_DELETE.get(key)
        if not desired or desired == "NO ACTION":
            continue
        if delete_rule == "NO ACTION":
            continue  # Already reverted or never changed

        op.drop_constraint(constraint_name, table, schema=schema, type_="foreignkey")
        op.create_foreign_key(
            constraint_name,
            table,
            "users",
            [column],
            ["id"],
            source_schema=schema,
            referent_schema=schema,
            onupdate="CASCADE",
            ondelete="NO ACTION",
        )
