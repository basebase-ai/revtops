"""Add GitHub integration tables: repositories, commits, pull requests.

Revision ID: 047
Revises: 046
Create Date: 2026-02-10

Adds three tables for the GitHub connector:
- github_repositories: repos an org has chosen to track
- github_commits: commit history on tracked repos
- github_pull_requests: PR activity on tracked repos
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "047"
down_revision = "046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── github_repositories ──────────────────────────────────────────────
    op.create_table(
        "github_repositories",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("integration_id", UUID(as_uuid=True), sa.ForeignKey("integrations.id"), nullable=False),
        sa.Column("github_repo_id", sa.Integer(), nullable=False),
        sa.Column("owner", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(512), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("default_branch", sa.String(255), nullable=False, server_default="main"),
        sa.Column("is_private", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("language", sa.String(100), nullable=True),
        sa.Column("url", sa.String(512), nullable=False),
        sa.Column("is_tracked", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("last_sync_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
    )
    op.create_index("idx_gh_repos_organization", "github_repositories", ["organization_id"])
    op.create_index(
        "uq_gh_repos_org_github_id",
        "github_repositories",
        ["organization_id", "github_repo_id"],
        unique=True,
    )

    # ── github_commits ───────────────────────────────────────────────────
    op.create_table(
        "github_commits",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("repository_id", UUID(as_uuid=True), sa.ForeignKey("github_repositories.id"), nullable=False),
        sa.Column("sha", sa.String(40), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("author_name", sa.String(255), nullable=False),
        sa.Column("author_email", sa.String(255), nullable=True),
        sa.Column("author_login", sa.String(255), nullable=True),
        sa.Column("author_date", sa.DateTime(), nullable=False),
        sa.Column("committer_name", sa.String(255), nullable=True),
        sa.Column("committer_email", sa.String(255), nullable=True),
        sa.Column("committed_date", sa.DateTime(), nullable=True),
        sa.Column("additions", sa.Integer(), nullable=True),
        sa.Column("deletions", sa.Integer(), nullable=True),
        sa.Column("changed_files", sa.Integer(), nullable=True),
        sa.Column("url", sa.String(512), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
    )
    op.create_index("idx_gh_commits_organization", "github_commits", ["organization_id"])
    op.create_index("idx_gh_commits_repository", "github_commits", ["repository_id"])
    op.create_index("idx_gh_commits_author_date", "github_commits", ["author_date"])
    op.create_index("idx_gh_commits_user", "github_commits", ["user_id"])
    op.create_index(
        "uq_gh_commits_org_sha",
        "github_commits",
        ["organization_id", "repository_id", "sha"],
        unique=True,
    )

    # ── github_pull_requests ─────────────────────────────────────────────
    op.create_table(
        "github_pull_requests",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("repository_id", UUID(as_uuid=True), sa.ForeignKey("github_repositories.id"), nullable=False),
        sa.Column("github_pr_id", sa.Integer(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("state", sa.String(20), nullable=False, server_default="open"),
        sa.Column("author_login", sa.String(255), nullable=False),
        sa.Column("author_avatar_url", sa.String(512), nullable=True),
        sa.Column("merged_by_login", sa.String(255), nullable=True),
        sa.Column("merge_commit_sha", sa.String(40), nullable=True),
        sa.Column("created_date", sa.DateTime(), nullable=False),
        sa.Column("updated_date", sa.DateTime(), nullable=True),
        sa.Column("merged_date", sa.DateTime(), nullable=True),
        sa.Column("closed_date", sa.DateTime(), nullable=True),
        sa.Column("additions", sa.Integer(), nullable=True),
        sa.Column("deletions", sa.Integer(), nullable=True),
        sa.Column("changed_files", sa.Integer(), nullable=True),
        sa.Column("commits_count", sa.Integer(), nullable=True),
        sa.Column("labels", JSONB(), nullable=True),
        sa.Column("reviewers", JSONB(), nullable=True),
        sa.Column("url", sa.String(512), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
    )
    op.create_index("idx_gh_prs_organization", "github_pull_requests", ["organization_id"])
    op.create_index("idx_gh_prs_repository", "github_pull_requests", ["repository_id"])
    op.create_index("idx_gh_prs_state", "github_pull_requests", ["state"])
    op.create_index("idx_gh_prs_user", "github_pull_requests", ["user_id"])
    op.create_index("idx_gh_prs_created_date", "github_pull_requests", ["created_date"])
    op.create_index(
        "uq_gh_prs_org_repo_number",
        "github_pull_requests",
        ["organization_id", "repository_id", "number"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("github_pull_requests")
    op.drop_table("github_commits")
    op.drop_table("github_repositories")
