"""
GitHub connector – syncs repositories, commits, and pull requests.

Unlike CRM connectors, GitHub data doesn't map to accounts/deals/contacts.
The CRM abstract methods are implemented as no-ops; sync_all() is overridden
to run GitHub-specific sync operations instead.

OAuth is handled through Nango (GitHub App or OAuth App).
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from connectors.base import BaseConnector
from models.database import get_session
from models.github_commit import GitHubCommit
from models.github_pull_request import GitHubPullRequest
from models.github_repository import GitHubRepository
from models.user import User

logger = logging.getLogger(__name__)

GITHUB_API_BASE: str = "https://api.github.com"


class GitHubConnector(BaseConnector):
    """Connector for GitHub – repos, commits, and pull requests."""

    source_system: str = "github"

    def __init__(
        self, organization_id: str, user_id: Optional[str] = None
    ) -> None:
        super().__init__(organization_id, user_id)
        self._user_email_cache: dict[str, UUID | None] = {}

    # ── HTTP helpers ─────────────────────────────────────────────────────

    async def _get_headers(self) -> dict[str, str]:
        token, _ = await self.get_oauth_token()
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def _gh_get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """GET from the GitHub REST API. Returns parsed JSON."""
        headers: dict[str, str] = await self._get_headers()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp: httpx.Response = await client.get(
                f"{GITHUB_API_BASE}{path}",
                headers=headers,
                params=params or {},
            )
            resp.raise_for_status()
            return resp.json()

    async def _gh_get_paginated(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """
        Paginate through a GitHub list endpoint.

        GitHub uses Link header pagination with per_page (max 100).
        """
        headers: dict[str, str] = await self._get_headers()
        all_items: list[dict[str, Any]] = []
        request_params: dict[str, Any] = {"per_page": 100, **(params or {})}
        url: str = f"{GITHUB_API_BASE}{path}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            for _ in range(max_pages):
                resp: httpx.Response = await client.get(
                    url, headers=headers, params=request_params
                )
                resp.raise_for_status()
                items: list[dict[str, Any]] = resp.json()
                if not items:
                    break
                all_items.extend(items)

                # Follow Link: <url>; rel="next" header
                link_header: str | None = resp.headers.get("Link")
                next_url: str | None = self._parse_next_link(link_header)
                if next_url is None:
                    break
                url = next_url
                request_params = {}  # params baked into the next URL

        return all_items

    @staticmethod
    def _parse_next_link(link_header: str | None) -> str | None:
        """Extract the 'next' URL from a GitHub Link header."""
        if not link_header:
            return None
        for part in link_header.split(","):
            segment: str = part.strip()
            if 'rel="next"' in segment:
                url_start: int = segment.index("<") + 1
                url_end: int = segment.index(">")
                return segment[url_start:url_end]
        return None

    # ── User mapping ─────────────────────────────────────────────────────

    async def _resolve_user_by_email(self, email: str | None) -> UUID | None:
        """Map an email address to an internal user_id, with caching."""
        if not email:
            return None
        if email in self._user_email_cache:
            return self._user_email_cache[email]

        async with get_session(organization_id=self.organization_id) as session:
            result = await session.execute(
                select(User.id).where(
                    User.organization_id == UUID(self.organization_id),
                    User.email == email,
                )
            )
            user_id: UUID | None = result.scalar_one_or_none()

        self._user_email_cache[email] = user_id
        return user_id

    # ── Repo listing (for UI to pick repos) ──────────────────────────────

    async def list_available_repos(self) -> list[dict[str, Any]]:
        """
        List all repos accessible to the authenticated GitHub token.

        The frontend uses this to let teams choose which repos to track.
        Returns a lightweight list (no commits/PRs fetched yet).
        """
        raw_repos: list[dict[str, Any]] = await self._gh_get_paginated(
            "/user/repos", params={"sort": "updated", "direction": "desc"}
        )
        repos: list[dict[str, Any]] = [
            {
                "github_repo_id": r["id"],
                "owner": r["owner"]["login"],
                "name": r["name"],
                "full_name": r["full_name"],
                "description": r.get("description"),
                "default_branch": r.get("default_branch", "main"),
                "is_private": r.get("private", False),
                "language": r.get("language"),
                "url": r["html_url"],
            }
            for r in raw_repos
        ]
        return repos

    async def track_repos(self, github_repo_ids: list[int]) -> list[dict[str, Any]]:
        """
        Mark specific repos for tracking by this org.

        Fetches metadata from GitHub and upserts into github_repositories.
        Returns the tracked repo records.
        """
        # Fetch current repo details from GitHub
        available: list[dict[str, Any]] = await self.list_available_repos()
        selected: list[dict[str, Any]] = [
            r for r in available if r["github_repo_id"] in github_repo_ids
        ]

        if not selected:
            return []

        # Get integration ID
        integration = await self._get_integration()
        integration_id: UUID = integration.id
        org_uuid: UUID = UUID(self.organization_id)

        tracked: list[dict[str, Any]] = []

        async with get_session(organization_id=self.organization_id) as session:
            for repo_data in selected:
                stmt = pg_insert(GitHubRepository).values(
                    organization_id=org_uuid,
                    integration_id=integration_id,
                    github_repo_id=repo_data["github_repo_id"],
                    owner=repo_data["owner"],
                    name=repo_data["name"],
                    full_name=repo_data["full_name"],
                    description=repo_data["description"],
                    default_branch=repo_data["default_branch"],
                    is_private=repo_data["is_private"],
                    language=repo_data["language"],
                    url=repo_data["url"],
                    is_tracked=True,
                ).on_conflict_do_update(
                    index_elements=["organization_id", "github_repo_id"],
                    set_={
                        "owner": repo_data["owner"],
                        "name": repo_data["name"],
                        "full_name": repo_data["full_name"],
                        "description": repo_data["description"],
                        "default_branch": repo_data["default_branch"],
                        "is_private": repo_data["is_private"],
                        "language": repo_data["language"],
                        "url": repo_data["url"],
                        "is_tracked": True,
                        "updated_at": datetime.utcnow(),
                    },
                )
                await session.execute(stmt)

            await session.commit()

            # Return the tracked repos
            result = await session.execute(
                select(GitHubRepository).where(
                    GitHubRepository.organization_id == org_uuid,
                    GitHubRepository.is_tracked == True,
                )
            )
            for repo in result.scalars().all():
                tracked.append(repo.to_dict())

        return tracked

    async def untrack_repos(self, github_repo_ids: list[int]) -> None:
        """Stop tracking specific repos (sets is_tracked=False, keeps data)."""
        org_uuid: UUID = UUID(self.organization_id)
        async with get_session(organization_id=self.organization_id) as session:
            result = await session.execute(
                select(GitHubRepository).where(
                    GitHubRepository.organization_id == org_uuid,
                    GitHubRepository.github_repo_id.in_(github_repo_ids),
                )
            )
            for repo in result.scalars().all():
                repo.is_tracked = False
            await session.commit()

    # ── Integration helper ───────────────────────────────────────────────

    async def _get_integration(self) -> Any:
        """Load the Integration record (cached on self._integration)."""
        if self._integration:
            return self._integration
        # get_oauth_token also loads the integration as a side effect
        await self.get_oauth_token()
        assert self._integration is not None
        return self._integration

    # ── Sync: Repositories ───────────────────────────────────────────────

    async def sync_repositories(self) -> int:
        """
        Refresh metadata for all tracked repos.

        Updates description, language, default_branch, etc.
        Returns count of repos refreshed.
        """
        org_uuid: UUID = UUID(self.organization_id)

        async with get_session(organization_id=self.organization_id) as session:
            result = await session.execute(
                select(GitHubRepository).where(
                    GitHubRepository.organization_id == org_uuid,
                    GitHubRepository.is_tracked == True,
                )
            )
            tracked_repos: list[GitHubRepository] = list(result.scalars().all())

        if not tracked_repos:
            logger.info(
                "No tracked repos for org %s – skipping repo sync",
                self.organization_id,
            )
            return 0

        count: int = 0
        for repo in tracked_repos:
            try:
                data: dict[str, Any] = await self._gh_get(
                    f"/repos/{repo.full_name}"
                )
                async with get_session(organization_id=self.organization_id) as session:
                    db_repo: GitHubRepository | None = await session.get(
                        GitHubRepository, repo.id
                    )
                    if db_repo:
                        db_repo.description = data.get("description")
                        db_repo.default_branch = data.get("default_branch", "main")
                        db_repo.is_private = data.get("private", False)
                        db_repo.language = data.get("language")
                        db_repo.updated_at = datetime.utcnow()
                        await session.commit()
                count += 1
            except Exception as exc:
                logger.warning(
                    "Failed to refresh repo %s: %s", repo.full_name, exc
                )

        return count

    # ── Sync: Commits ────────────────────────────────────────────────────

    async def sync_commits(self) -> int:
        """
        Fetch recent commits for all tracked repos and upsert.

        Fetches commits on the default branch.  On first sync, pulls up to
        ~5 000 commits (50 pages x 100); subsequent syncs are incremental
        because the unique-SHA constraint means duplicates are skipped.
        """
        org_uuid: UUID = UUID(self.organization_id)

        async with get_session(organization_id=self.organization_id) as session:
            result = await session.execute(
                select(GitHubRepository).where(
                    GitHubRepository.organization_id == org_uuid,
                    GitHubRepository.is_tracked == True,
                )
            )
            tracked_repos: list[GitHubRepository] = list(result.scalars().all())

        if not tracked_repos:
            return 0

        total_count: int = 0
        for repo in tracked_repos:
            try:
                count: int = await self._sync_commits_for_repo(repo)
                total_count += count
            except Exception as exc:
                logger.warning(
                    "Failed to sync commits for %s: %s", repo.full_name, exc
                )

        return total_count

    async def _sync_commits_for_repo(self, repo: GitHubRepository) -> int:
        """Fetch and upsert commits for a single repo."""
        org_uuid: UUID = UUID(self.organization_id)
        raw_commits: list[dict[str, Any]] = await self._gh_get_paginated(
            f"/repos/{repo.full_name}/commits",
            params={"sha": repo.default_branch},
        )

        count: int = 0
        async with get_session(organization_id=self.organization_id) as session:
            for c in raw_commits:
                commit_data: dict[str, Any] = c.get("commit", {})
                author_info: dict[str, Any] = commit_data.get("author", {})
                committer_info: dict[str, Any] = commit_data.get("committer", {})
                gh_author: dict[str, Any] | None = c.get("author")  # GitHub user

                author_email: str | None = author_info.get("email")
                author_login: str | None = (
                    gh_author["login"] if gh_author else None
                )
                user_id: UUID | None = await self._resolve_user_by_email(
                    author_email
                )

                # Parse dates
                author_date: datetime = self._parse_gh_date(
                    author_info.get("date", "")
                )
                committed_date: datetime | None = self._parse_gh_date_optional(
                    committer_info.get("date")
                )

                stmt = pg_insert(GitHubCommit).values(
                    organization_id=org_uuid,
                    repository_id=repo.id,
                    sha=c["sha"],
                    message=commit_data.get("message", ""),
                    author_name=author_info.get("name", "Unknown"),
                    author_email=author_email,
                    author_login=author_login,
                    author_date=author_date,
                    committer_name=committer_info.get("name"),
                    committer_email=committer_info.get("email"),
                    committed_date=committed_date,
                    url=c.get("html_url", ""),
                    user_id=user_id,
                ).on_conflict_do_nothing(
                    index_elements=["organization_id", "repository_id", "sha"]
                )
                await session.execute(stmt)
                count += 1

            await session.commit()

        # Update last_sync_at on the repo
        async with get_session(organization_id=self.organization_id) as session:
            db_repo: GitHubRepository | None = await session.get(
                GitHubRepository, repo.id
            )
            if db_repo:
                db_repo.last_sync_at = datetime.utcnow()
                await session.commit()

        logger.info("Synced %d commits for %s", count, repo.full_name)
        return count

    # ── Sync: Pull Requests ──────────────────────────────────────────────

    async def sync_pull_requests(self) -> int:
        """Fetch pull requests for all tracked repos and upsert."""
        org_uuid: UUID = UUID(self.organization_id)

        async with get_session(organization_id=self.organization_id) as session:
            result = await session.execute(
                select(GitHubRepository).where(
                    GitHubRepository.organization_id == org_uuid,
                    GitHubRepository.is_tracked == True,
                )
            )
            tracked_repos: list[GitHubRepository] = list(result.scalars().all())

        if not tracked_repos:
            return 0

        total_count: int = 0
        for repo in tracked_repos:
            try:
                count: int = await self._sync_prs_for_repo(repo)
                total_count += count
            except Exception as exc:
                logger.warning(
                    "Failed to sync PRs for %s: %s", repo.full_name, exc
                )

        return total_count

    async def _sync_prs_for_repo(self, repo: GitHubRepository) -> int:
        """Fetch and upsert PRs for a single repo."""
        org_uuid: UUID = UUID(self.organization_id)
        raw_prs: list[dict[str, Any]] = await self._gh_get_paginated(
            f"/repos/{repo.full_name}/pulls",
            params={"state": "all", "sort": "updated", "direction": "desc"},
        )

        count: int = 0
        async with get_session(organization_id=self.organization_id) as session:
            for pr in raw_prs:
                user_info: dict[str, Any] = pr.get("user", {})
                author_login: str = user_info.get("login", "unknown")
                merged_by: dict[str, Any] | None = pr.get("merged_by")

                # Determine state
                state: str
                if pr.get("merged_at"):
                    state = "merged"
                elif pr.get("state") == "closed":
                    state = "closed"
                else:
                    state = "open"

                # Extract labels
                labels: list[str] = [
                    lbl["name"] for lbl in pr.get("labels", []) if "name" in lbl
                ]

                # Extract requested reviewers
                reviewers: list[str] = [
                    rev["login"]
                    for rev in pr.get("requested_reviewers", [])
                    if "login" in rev
                ]

                stmt = pg_insert(GitHubPullRequest).values(
                    organization_id=org_uuid,
                    repository_id=repo.id,
                    github_pr_id=pr["id"],
                    number=pr["number"],
                    title=pr.get("title", ""),
                    body=pr.get("body"),
                    state=state,
                    author_login=author_login,
                    author_avatar_url=user_info.get("avatar_url"),
                    merged_by_login=(
                        merged_by["login"] if merged_by else None
                    ),
                    merge_commit_sha=pr.get("merge_commit_sha"),
                    created_date=self._parse_gh_date(pr["created_at"]),
                    updated_date=self._parse_gh_date_optional(
                        pr.get("updated_at")
                    ),
                    merged_date=self._parse_gh_date_optional(
                        pr.get("merged_at")
                    ),
                    closed_date=self._parse_gh_date_optional(
                        pr.get("closed_at")
                    ),
                    additions=pr.get("additions"),
                    deletions=pr.get("deletions"),
                    changed_files=pr.get("changed_files"),
                    commits_count=pr.get("commits"),
                    labels=labels or None,
                    reviewers=reviewers or None,
                    url=pr.get("html_url", ""),
                    user_id=None,  # TODO: map via identity once login→email resolved
                ).on_conflict_do_update(
                    index_elements=[
                        "organization_id",
                        "repository_id",
                        "number",
                    ],
                    set_={
                        "title": pr.get("title", ""),
                        "body": pr.get("body"),
                        "state": state,
                        "merged_by_login": (
                            merged_by["login"] if merged_by else None
                        ),
                        "merge_commit_sha": pr.get("merge_commit_sha"),
                        "updated_date": self._parse_gh_date_optional(
                            pr.get("updated_at")
                        ),
                        "merged_date": self._parse_gh_date_optional(
                            pr.get("merged_at")
                        ),
                        "closed_date": self._parse_gh_date_optional(
                            pr.get("closed_at")
                        ),
                        "additions": pr.get("additions"),
                        "deletions": pr.get("deletions"),
                        "changed_files": pr.get("changed_files"),
                        "commits_count": pr.get("commits"),
                        "labels": labels or None,
                        "reviewers": reviewers or None,
                    },
                )
                await session.execute(stmt)
                count += 1

            await session.commit()

        logger.info("Synced %d PRs for %s", count, repo.full_name)
        return count

    # ── Date helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _parse_gh_date(date_str: str) -> datetime:
        """Parse a GitHub ISO-8601 date string to datetime."""
        if not date_str:
            return datetime.utcnow()
        # GitHub returns "2024-01-15T10:30:00Z" format
        cleaned: str = date_str.replace("Z", "+00:00")
        return datetime.fromisoformat(cleaned).replace(tzinfo=None)

    @staticmethod
    def _parse_gh_date_optional(date_str: str | None) -> datetime | None:
        """Parse a GitHub date that may be None."""
        if not date_str:
            return None
        cleaned: str = date_str.replace("Z", "+00:00")
        return datetime.fromisoformat(cleaned).replace(tzinfo=None)

    # ── CRM no-ops (BaseConnector requires these) ────────────────────────

    async def sync_deals(self) -> int:
        """Not applicable for GitHub."""
        return 0

    async def sync_accounts(self) -> int:
        """Not applicable for GitHub."""
        return 0

    async def sync_contacts(self) -> int:
        """Not applicable for GitHub."""
        return 0

    async def sync_activities(self) -> int:
        """Not applicable for GitHub."""
        return 0

    async def fetch_deal(self, deal_id: str) -> dict[str, Any]:
        """Not applicable for GitHub."""
        raise NotImplementedError("GitHub connector does not support deals")

    # ── Override sync_all with GitHub-specific flow ──────────────────────

    async def sync_all(self) -> dict[str, int]:
        """
        Run all GitHub sync operations.

        Order: repositories metadata → commits → pull requests.
        """
        await self.ensure_sync_active("sync_all:start")

        repos_count: int = await self.sync_repositories()
        await self.ensure_sync_active("sync_all:after_repositories")

        commits_count: int = await self.sync_commits()
        await self.ensure_sync_active("sync_all:after_commits")

        prs_count: int = await self.sync_pull_requests()
        await self.ensure_sync_active("sync_all:after_pull_requests")

        result: dict[str, int] = {
            "repositories": repos_count,
            "commits": commits_count,
            "pull_requests": prs_count,
        }
        return result
