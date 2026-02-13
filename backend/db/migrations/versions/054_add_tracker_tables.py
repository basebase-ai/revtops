"""Add generic issue tracker tables: teams, projects, issues.

Revision ID: 051
Revises: 050
Create Date: 2026-02-12

Adds three tables shared across issue tracking providers (Linear, Jira, Asana):
- tracker_teams: teams/workspaces
- tracker_projects: projects grouping issues
- tracker_issues: issue/task items

Each table has a source_system discriminator ('linear', 'jira', 'asana')
and a source_id for the provider's external ID.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "054"
down_revision = "053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── tracker_teams ─────────────────────────────────────────────────────
    op.create_table(
        "tracker_teams",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("integration_id", UUID(as_uuid=True), sa.ForeignKey("integrations.id"), nullable=False),
        sa.Column("source_system", sa.String(30), nullable=False),
        sa.Column("source_id", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("key", sa.String(30), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
    )
    op.create_index("idx_tracker_teams_organization", "tracker_teams", ["organization_id"])
    op.create_index("idx_tracker_teams_source_system", "tracker_teams", ["source_system"])
    op.create_index(
        "uq_tracker_teams_org_source",
        "tracker_teams",
        ["organization_id", "source_system", "source_id"],
        unique=True,
    )

    # ── tracker_projects ──────────────────────────────────────────────────
    op.create_table(
        "tracker_projects",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("source_system", sa.String(30), nullable=False),
        sa.Column("source_id", sa.String(255), nullable=False),
        sa.Column("name", sa.String(512), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("state", sa.String(30), nullable=True),
        sa.Column("progress", sa.Float(), nullable=True),
        sa.Column("target_date", sa.Date(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("url", sa.String(512), nullable=False, server_default=""),
        sa.Column("lead_name", sa.String(255), nullable=True),
        sa.Column("team_ids", JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
    )
    op.create_index("idx_tracker_projects_organization", "tracker_projects", ["organization_id"])
    op.create_index("idx_tracker_projects_source_system", "tracker_projects", ["source_system"])
    op.create_index(
        "uq_tracker_projects_org_source",
        "tracker_projects",
        ["organization_id", "source_system", "source_id"],
        unique=True,
    )

    # ── tracker_issues ────────────────────────────────────────────────────
    op.create_table(
        "tracker_issues",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("team_id", UUID(as_uuid=True), sa.ForeignKey("tracker_teams.id"), nullable=False),
        sa.Column("source_system", sa.String(30), nullable=False),
        sa.Column("source_id", sa.String(255), nullable=False),
        sa.Column("identifier", sa.String(30), nullable=False),
        sa.Column("title", sa.String(1024), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("state_name", sa.String(100), nullable=True),
        sa.Column("state_type", sa.String(30), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=True),
        sa.Column("priority_label", sa.String(20), nullable=True),
        sa.Column("issue_type", sa.String(50), nullable=True),
        sa.Column("assignee_name", sa.String(255), nullable=True),
        sa.Column("assignee_email", sa.String(255), nullable=True),
        sa.Column("creator_name", sa.String(255), nullable=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("tracker_projects.id"), nullable=True),
        sa.Column("labels", JSONB(), nullable=True),
        sa.Column("estimate", sa.Float(), nullable=True),
        sa.Column("url", sa.String(512), nullable=False, server_default=""),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("created_date", sa.DateTime(), nullable=False),
        sa.Column("updated_date", sa.DateTime(), nullable=True),
        sa.Column("completed_date", sa.DateTime(), nullable=True),
        sa.Column("cancelled_date", sa.DateTime(), nullable=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
    )
    op.create_index("idx_tracker_issues_organization", "tracker_issues", ["organization_id"])
    op.create_index("idx_tracker_issues_source_system", "tracker_issues", ["source_system"])
    op.create_index("idx_tracker_issues_team", "tracker_issues", ["team_id"])
    op.create_index("idx_tracker_issues_project", "tracker_issues", ["project_id"])
    op.create_index("idx_tracker_issues_state_type", "tracker_issues", ["state_type"])
    op.create_index("idx_tracker_issues_assignee", "tracker_issues", ["assignee_name"])
    op.create_index("idx_tracker_issues_created_date", "tracker_issues", ["created_date"])
    op.create_index("idx_tracker_issues_user", "tracker_issues", ["user_id"])
    op.create_index(
        "uq_tracker_issues_org_source",
        "tracker_issues",
        ["organization_id", "source_system", "source_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("tracker_issues")
    op.drop_table("tracker_projects")
    op.drop_table("tracker_teams")
