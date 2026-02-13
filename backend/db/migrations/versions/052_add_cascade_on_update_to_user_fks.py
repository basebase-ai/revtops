"""Add ON UPDATE CASCADE to all FK constraints referencing users.id.

Revision ID: 052
Revises: 051
Create Date: 2026-02-12

When an invited user signs in via OAuth, we need to migrate their DB
primary key to match their Supabase UUID. With ON UPDATE CASCADE,
Postgres automatically propagates the PK change to every child table,
eliminating the need for a brittle hardcoded list of UPDATE statements.
"""
from alembic import op
from sqlalchemy import text

revision = "052"
down_revision = "051"
branch_labels = None
depends_on = None

# Query to find all FK constraints referencing users.id, along with
# their current ON DELETE action so we can preserve it.
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

# Map information_schema delete_rule values to SQL syntax
_DELETE_ACTION_MAP: dict[str, str] = {
    "CASCADE": "CASCADE",
    "SET NULL": "SET NULL",
    "SET DEFAULT": "SET DEFAULT",
    "RESTRICT": "RESTRICT",
    "NO ACTION": "NO ACTION",
}


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(text(_FIND_USER_FKS)).fetchall()

    for constraint_name, schema, table, column, delete_rule in rows:
        on_delete: str = _DELETE_ACTION_MAP.get(delete_rule, "NO ACTION")

        # Drop the existing constraint
        op.drop_constraint(constraint_name, table, schema=schema, type_="foreignkey")

        # Recreate with ON UPDATE CASCADE (preserving existing ON DELETE)
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
    conn = op.get_bind()
    rows = conn.execute(text(_FIND_USER_FKS)).fetchall()

    for constraint_name, schema, table, column, delete_rule in rows:
        on_delete: str = _DELETE_ACTION_MAP.get(delete_rule, "NO ACTION")

        # Drop the CASCADE version
        op.drop_constraint(constraint_name, table, schema=schema, type_="foreignkey")

        # Recreate without ON UPDATE CASCADE
        op.create_foreign_key(
            constraint_name,
            table,
            "users",
            [column],
            ["id"],
            source_schema=schema,
            referent_schema=schema,
            ondelete=on_delete,
        )
