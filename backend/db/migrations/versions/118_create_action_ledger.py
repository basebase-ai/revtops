"""Create action_ledger table for connector mutation audit trail.

Revision ID: 118_create_action_ledger
Revises: 117_guest_org
Create Date: 2026-03-29
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "118_create_action_ledger"
down_revision = "117_guest_org"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "action_ledger",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "workflow_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workflows.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # What was done
        sa.Column("connector", sa.String(50), nullable=False),
        sa.Column("dispatch_type", sa.String(10), nullable=False),
        sa.Column("operation", sa.String(100), nullable=False),
        sa.Column("entity_type", sa.String(50), nullable=True),
        sa.Column("entity_id", sa.Text, nullable=True),
        # Intent / outcome (JSONB)
        sa.Column("intent", postgresql.JSONB, nullable=False),
        sa.Column("outcome", postgresql.JSONB, nullable=True),
        # Reversibility
        sa.Column("reversible", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("reversed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "reversed_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("action_ledger.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # Timestamps
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Primary listing: all actions for an org, newest first
    op.create_index(
        "ix_action_ledger_org_created",
        "action_ledger",
        ["organization_id", sa.text("created_at DESC")],
    )

    # Per-conversation filter
    op.create_index(
        "ix_action_ledger_conversation",
        "action_ledger",
        ["conversation_id", sa.text("created_at DESC")],
    )

    # "What happened to this entity?" lookup
    op.create_index(
        "ix_action_ledger_connector_entity",
        "action_ledger",
        ["organization_id", "connector", "entity_type", "entity_id"],
    )

    # RLS
    op.execute("ALTER TABLE action_ledger ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY action_ledger_org_isolation ON action_ledger
        FOR ALL
        USING (organization_id = current_setting('app.current_org_id')::uuid)
    """)
    op.execute("GRANT ALL ON action_ledger TO revtops_app")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS action_ledger_org_isolation ON action_ledger")
    op.drop_index("ix_action_ledger_connector_entity", table_name="action_ledger")
    op.drop_index("ix_action_ledger_conversation", table_name="action_ledger")
    op.drop_index("ix_action_ledger_org_created", table_name="action_ledger")
    op.drop_table("action_ledger")
