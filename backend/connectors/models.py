"""
Canonical Pydantic record models for the connector interface.

Connectors return instances of these models from their ``sync_*`` methods.
The sync engine handles persistence (upserting into the corresponding DB
tables).  ``source_system`` is always set by the sync engine based on the
connector's ``meta.slug``, so connectors can omit it.

Each model mirrors the business columns of the corresponding SQLAlchemy
model but is framework-agnostic and fully typed.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# CRM canonical models
# ---------------------------------------------------------------------------


class DealRecord(BaseModel):
    """A deal / opportunity from a CRM."""

    source_id: str
    name: str
    amount: float | None = None
    stage: str | None = None
    probability: int | None = None
    close_date: date | None = None
    created_date: datetime | None = None
    last_modified_date: datetime | None = None
    owner_email: str | None = None
    account_source_id: str | None = None
    pipeline_source_id: str | None = None
    custom_fields: dict[str, Any] | None = None
    source_system: str = ""


class AccountRecord(BaseModel):
    """A company / account from a CRM or enrichment service."""

    source_id: str
    name: str
    domain: str | None = None
    industry: str | None = None
    employee_count: int | None = None
    annual_revenue: float | None = None
    owner_email: str | None = None
    custom_fields: dict[str, Any] | None = None
    source_system: str = ""


class ContactRecord(BaseModel):
    """A contact / person from a CRM or enrichment service."""

    source_id: str
    name: str | None = None
    email: str | None = None
    title: str | None = None
    phone: str | None = None
    account_source_id: str | None = None
    custom_fields: dict[str, Any] | None = None
    source_system: str = ""


class ActivityRecord(BaseModel):
    """An activity (email, meeting, call, message, ticket, etc.)."""

    source_id: str
    type: str
    subject: str | None = None
    description: str | None = None
    activity_date: datetime | None = None
    deal_source_id: str | None = None
    account_source_id: str | None = None
    contact_source_id: str | None = None
    custom_fields: dict[str, Any] | None = None
    source_system: str = ""


class PipelineRecord(BaseModel):
    """A sales pipeline definition."""

    source_id: str
    name: str
    display_order: int | None = None
    is_default: bool = False
    source_system: str = ""


class PipelineStageRecord(BaseModel):
    """A stage within a pipeline."""

    source_id: str
    pipeline_source_id: str
    name: str
    display_order: int | None = None
    probability: int | None = None
    is_closed_won: bool = False
    is_closed_lost: bool = False
    source_system: str = ""


class GoalRecord(BaseModel):
    """A sales goal / quota / target."""

    source_id: str
    name: str
    target_amount: float | None = None
    start_date: date | None = None
    end_date: date | None = None
    goal_type: str | None = None
    owner_email: str | None = None
    pipeline_source_id: str | None = None
    custom_fields: dict[str, Any] | None = None
    source_system: str = ""


# ---------------------------------------------------------------------------
# Issue tracker models (Linear, Asana, GitHub Issues, etc.)
# ---------------------------------------------------------------------------


class TrackerTeamRecord(BaseModel):
    """A team in an issue tracker."""

    source_id: str
    name: str
    key: str | None = None
    description: str | None = None
    source_system: str = ""


class TrackerProjectRecord(BaseModel):
    """A project in an issue tracker."""

    source_id: str
    name: str
    description: str | None = None
    state: str | None = None
    progress: float | None = None
    target_date: date | None = None
    start_date: date | None = None
    url: str = ""
    lead_name: str | None = None
    team_source_ids: list[str] = Field(default_factory=list)
    source_system: str = ""


class TrackerIssueRecord(BaseModel):
    """An issue / task / ticket in an issue tracker."""

    source_id: str
    identifier: str
    title: str
    description: str | None = None
    state_name: str | None = None
    state_type: str | None = None
    priority: int | None = None
    priority_label: str | None = None
    issue_type: str | None = None
    assignee_name: str | None = None
    assignee_email: str | None = None
    creator_name: str | None = None
    project_source_id: str | None = None
    team_source_id: str | None = None
    labels: list[str] = Field(default_factory=list)
    estimate: float | None = None
    url: str = ""
    due_date: date | None = None
    created_date: datetime | None = None
    updated_date: datetime | None = None
    completed_date: datetime | None = None
    cancelled_date: datetime | None = None
    source_system: str = ""


# ---------------------------------------------------------------------------
# File storage models (Google Drive, Dropbox, etc.)
# ---------------------------------------------------------------------------


class SharedFileRecord(BaseModel):
    """A file entry from a cloud storage provider."""

    external_id: str
    name: str
    mime_type: str = ""
    parent_external_id: str | None = None
    folder_path: str = "/"
    web_view_link: str | None = None
    file_size: int | None = None
    source_modified_at: datetime | None = None
    source: str = ""


# ---------------------------------------------------------------------------
# GitHub-specific models
# ---------------------------------------------------------------------------


class GitHubRepositoryRecord(BaseModel):
    """A GitHub repository."""

    github_repo_id: int
    owner: str
    name: str
    full_name: str
    description: str | None = None
    default_branch: str = "main"
    is_private: bool = False
    language: str | None = None
    url: str
    is_tracked: bool = True
    source_system: str = "github"


class GitHubCommitRecord(BaseModel):
    """A commit in a GitHub repository."""

    sha: str
    repository_full_name: str
    message: str
    author_name: str
    author_email: str | None = None
    author_login: str | None = None
    author_date: datetime
    committer_name: str | None = None
    committer_email: str | None = None
    committed_date: datetime | None = None
    additions: int | None = None
    deletions: int | None = None
    changed_files: int | None = None
    url: str
    source_system: str = "github"


class GitHubPullRequestRecord(BaseModel):
    """A pull request in a GitHub repository."""

    github_pr_id: int
    repository_full_name: str
    number: int
    title: str
    body: str | None = None
    state: str = "open"
    author_login: str
    author_avatar_url: str | None = None
    merged_by_login: str | None = None
    merge_commit_sha: str | None = None
    created_date: datetime
    updated_date: datetime | None = None
    merged_date: datetime | None = None
    closed_date: datetime | None = None
    additions: int | None = None
    deletions: int | None = None
    changed_files: int | None = None
    commits_count: int | None = None
    labels: list[str] = Field(default_factory=list)
    reviewers: list[str] = Field(default_factory=list)
    url: str
    source_system: str = "github"
