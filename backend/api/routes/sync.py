"""
Sync trigger endpoints for all integrations.

Endpoints:
- POST /api/sync/{organization_id}/{provider} - Trigger sync for specific integration
- GET /api/sync/{organization_id}/{provider}/status - Get sync status
- POST /api/sync/{organization_id}/all - Sync all active integrations
"""
from __future__ import annotations

from datetime import datetime
import asyncio
import logging
from typing import Any, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from connectors.base import SyncCancelledError
from connectors.fireflies import FirefliesConnector
from connectors.github import GitHubConnector
from connectors.gmail import GmailConnector
from connectors.google_calendar import GoogleCalendarConnector
from connectors.hubspot import HubSpotConnector
from connectors.microsoft_calendar import MicrosoftCalendarConnector
from connectors.microsoft_mail import MicrosoftMailConnector
from connectors.salesforce import SalesforceConnector
from connectors.slack import SlackConnector
from connectors.zoom import ZoomConnector
from models.database import get_session
from models.agent_task import AgentTask
from models.conversation import Conversation
from models.integration import Integration
from models.organization import Organization
from workers.celery_app import celery_app

logger = logging.getLogger(__name__)

router = APIRouter()

# Connector registry
CONNECTORS = {
    "salesforce": SalesforceConnector,
    "hubspot": HubSpotConnector,
    "slack": SlackConnector,
    "fireflies": FirefliesConnector,
    "google_calendar": GoogleCalendarConnector,
    "gmail": GmailConnector,
    "microsoft_calendar": MicrosoftCalendarConnector,
    "microsoft_mail": MicrosoftMailConnector,
    "zoom": ZoomConnector,
    "github": GitHubConnector,
}

# Simple in-memory sync status tracking (use Redis in production)
_sync_status: dict[str, dict[str, str | datetime | None | dict[str, int]]] = {}


class SyncStatusResponse(BaseModel):
    """Response model for sync status."""

    organization_id: str
    provider: str
    status: str
    started_at: Optional[str]
    completed_at: Optional[str]
    error: Optional[str]
    counts: Optional[dict[str, int]]


class SyncTriggerResponse(BaseModel):
    """Response model for sync trigger."""

    status: str
    organization_id: str
    provider: str


class SyncAllResponse(BaseModel):
    """Response model for syncing all integrations."""

    status: str
    organization_id: str
    integrations: list[str]


def _get_status_key(organization_id: str, provider: str) -> str:
    """Generate a unique key for sync status tracking."""
    return f"{organization_id}:{provider}"


# =============================================================================
# Admin endpoints (must be defined BEFORE parameterized routes)
# =============================================================================


class GlobalSyncResponse(BaseModel):
    """Response model for global sync (all organizations)."""

    status: str
    task_id: str
    integration_count: int


class AdminIntegration(BaseModel):
    """Integration info for admin view."""

    id: str
    organization_id: str
    organization_name: str
    provider: str
    is_active: bool
    last_sync_at: str | None
    last_error: str | None
    sync_stats: dict[str, int] | None
    created_at: str | None


class AdminIntegrationsResponse(BaseModel):
    """Response model for admin integrations list."""

    integrations: list[AdminIntegration]
    total: int


class AdminRunningJob(BaseModel):
    """Running job details for admin monitoring."""

    id: str
    job_type: Literal["chat", "workflow", "connector_sync"]
    status: str
    task_name: str
    started_at: str | None
    organization_id: str | None = None
    organization_name: str | None = None
    user_id: str | None = None
    user_email: str | None = None
    provider: str | None = None
    workflow_id: str | None = None


class AdminRunningJobsResponse(BaseModel):
    """Response model for currently running jobs."""

    jobs: list[AdminRunningJob]
    total: int


class CancelAdminJobResponse(BaseModel):
    """Response model for admin job cancellation."""

    status: str
    job_type: Literal["chat", "workflow", "connector_sync"]
    id: str


async def _require_global_admin(user_id: str) -> None:
    from models.database import get_admin_session
    from models.user import User

    async with get_admin_session() as session:
        result = await session.execute(select(User).where(User.id == UUID(user_id)))
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        if "global_admin" not in (user.roles or []):
            raise HTTPException(status_code=403, detail="Requires global_admin role")


def _load_celery_jobs_sync() -> list[dict[str, Any]]:
    inspect = celery_app.control.inspect(timeout=1.5)
    jobs: list[dict[str, Any]] = []
    for state_name, state_jobs in {
        "running": inspect.active() or {},
        "reserved": inspect.reserved() or {},
    }.items():
        for worker_name, worker_jobs in state_jobs.items():
            for job in worker_jobs:
                job["state"] = state_name
                job["worker"] = worker_name
                jobs.append(job)
    return jobs


@router.get("/admin/jobs", response_model=AdminRunningJobsResponse)
async def list_running_jobs(user_id: str) -> AdminRunningJobsResponse:
    """List currently running chat, workflow, and connector sync jobs (global admin only)."""
    await _require_global_admin(user_id)

    jobs: list[AdminRunningJob] = []

    # Chat jobs tracked in DB + in-memory task manager.
    from models.database import get_admin_session
    async with get_admin_session() as session:
        result = await session.execute(
            select(AgentTask, Conversation.type, Organization.name.label("org_name"))
            .join(Conversation, Conversation.id == AgentTask.conversation_id)
            .join(Organization, Organization.id == AgentTask.organization_id)
            .where(AgentTask.status == "running")
            .order_by(AgentTask.started_at.desc())
        )
        for task, conversation_type, org_name in result.all():
            jobs.append(
                AdminRunningJob(
                    id=str(task.id),
                    job_type="chat" if conversation_type == "chat" else "workflow",
                    status="running",
                    task_name="agent.chat",
                    started_at=task.started_at.isoformat() if task.started_at else None,
                    organization_id=str(task.organization_id),
                    organization_name=org_name,
                    user_id=str(task.user_id),
                )
            )

    # Celery jobs for workflow execution and connector sync.
    celery_jobs = await asyncio.to_thread(_load_celery_jobs_sync)
    for job in celery_jobs:
        task_name = str(job.get("name") or "")
        task_id = str(job.get("id") or "")
        kwargs = job.get("kwargs") or {}
        if isinstance(kwargs, str):
            kwargs = {}

        if task_name == "workers.tasks.workflows.execute_workflow":
            jobs.append(
                AdminRunningJob(
                    id=task_id,
                    job_type="workflow",
                    status=str(job.get("state") or "running"),
                    task_name=task_name,
                    started_at=None,
                    organization_id=kwargs.get("organization_id"),
                    workflow_id=kwargs.get("workflow_id"),
                )
            )
        elif task_name in {
            "workers.tasks.sync.sync_integration",
            "workers.tasks.sync.sync_organization_integrations",
            "workers.tasks.sync.sync_all_organizations",
        }:
            jobs.append(
                AdminRunningJob(
                    id=task_id,
                    job_type="connector_sync",
                    status=str(job.get("state") or "running"),
                    task_name=task_name,
                    started_at=None,
                    organization_id=kwargs.get("organization_id"),
                    provider=kwargs.get("provider"),
                )
            )

    logger.info("Admin running jobs requested: %s jobs", len(jobs))
    return AdminRunningJobsResponse(jobs=jobs, total=len(jobs))


@router.post("/admin/jobs/{job_type}/{job_id}/cancel", response_model=CancelAdminJobResponse)
async def cancel_running_job(
    job_type: Literal["chat", "workflow", "connector_sync"],
    job_id: str,
    user_id: str,
) -> CancelAdminJobResponse:
    """Cancel a running job by job type and ID (global admin only)."""
    await _require_global_admin(user_id)

    if job_type == "chat":
        from services.task_manager import task_manager

        cancelled = await task_manager.cancel_task(job_id)
        if not cancelled:
            raise HTTPException(status_code=404, detail="Chat job not running or not found")
    else:
        celery_app.control.revoke(job_id, terminate=True, signal="SIGTERM")

    logger.warning("Admin cancelled job: type=%s id=%s", job_type, job_id)
    return CancelAdminJobResponse(status="cancelled", job_type=job_type, id=job_id)


@router.get("/admin/integrations", response_model=AdminIntegrationsResponse)
async def list_admin_integrations(user_id: str) -> AdminIntegrationsResponse:
    """
    List all integrations across all organizations (global admin only).
    """
    from models.database import get_admin_session
    from models.user import User
    
    # Verify user is global admin
    async with get_admin_session() as session:
        result = await session.execute(
            select(User).where(User.id == UUID(user_id))
        )
        user = result.scalar_one_or_none()
        
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        if "global_admin" not in (user.roles or []):
            raise HTTPException(status_code=403, detail="Requires global_admin role")
        
        # Get all integrations with org names
        result = await session.execute(
            select(Integration, Organization.name.label("org_name"))
            .join(Organization, Integration.organization_id == Organization.id)
            .order_by(Organization.name, Integration.provider)
        )
        rows = result.all()
    
    integrations: list[AdminIntegration] = []
    for row in rows:
        integration = row[0]
        org_name = row[1]
        integrations.append(AdminIntegration(
            id=str(integration.id),
            organization_id=str(integration.organization_id),
            organization_name=org_name,
            provider=integration.provider,
            is_active=integration.is_active,
            last_sync_at=integration.last_sync_at.isoformat() if integration.last_sync_at else None,
            last_error=integration.last_error,
            sync_stats=integration.sync_stats,
            created_at=integration.created_at.isoformat() if integration.created_at else None,
        ))
    
    return AdminIntegrationsResponse(
        integrations=integrations,
        total=len(integrations),
    )


@router.post("/admin/all", response_model=GlobalSyncResponse)
async def trigger_global_sync(user_id: str) -> GlobalSyncResponse:
    """
    Trigger sync for ALL organizations (global admin only).
    
    This calls the same task that the hourly beat scheduler runs.
    """
    from models.database import get_admin_session
    from models.user import User
    
    # Verify user is global admin
    async with get_admin_session() as session:
        result = await session.execute(
            select(User).where(User.id == UUID(user_id))
        )
        user = result.scalar_one_or_none()
        
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        if "global_admin" not in (user.roles or []):
            raise HTTPException(status_code=403, detail="Requires global_admin role")
        
        # Count active integrations
        result = await session.execute(
            select(Integration).where(Integration.is_active == True)
        )
        integrations = result.scalars().all()
    
    if not integrations:
        raise HTTPException(status_code=404, detail="No active integrations found")
    
    # Queue the global sync task (same as hourly beat task)
    from workers.tasks.sync import sync_all_organizations
    task = sync_all_organizations.delay()
    
    return GlobalSyncResponse(
        status="queued",
        task_id=task.id,
        integration_count=len(integrations),
    )


# =============================================================================
# Organization-scoped endpoints
# =============================================================================


@router.post("/{organization_id}/{provider}", response_model=SyncTriggerResponse)
async def trigger_sync(
    organization_id: str, provider: str, background_tasks: BackgroundTasks
) -> SyncTriggerResponse:
    """Trigger a sync for a specific integration."""
    try:
        customer_uuid = UUID(organization_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid customer ID")

    if provider not in CONNECTORS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown provider: {provider}. Available: {list(CONNECTORS.keys())}",
        )

    # Verify integration exists and is active
    async with get_session(organization_id=organization_id) as session:
        result = await session.execute(
            select(Integration).where(
                Integration.organization_id == customer_uuid,
                Integration.provider == provider,
                Integration.is_active == True,
            )
        )
        integration = result.scalar_one_or_none()

        if not integration:
            raise HTTPException(
                status_code=404,
                detail=f"No active {provider} integration found",
            )

    # Initialize sync status
    status_key = _get_status_key(organization_id, provider)
    _sync_status[status_key] = {
        "status": "syncing",
        "started_at": datetime.utcnow(),
        "completed_at": None,
        "error": None,
        "counts": None,
    }

    # Add background task
    background_tasks.add_task(sync_integration_data, organization_id, provider)

    return SyncTriggerResponse(
        status="syncing", organization_id=organization_id, provider=provider
    )


@router.get("/{organization_id}/{provider}/status", response_model=SyncStatusResponse)
async def get_sync_status(organization_id: str, provider: str) -> SyncStatusResponse:
    """Get sync status for a specific integration."""
    try:
        UUID(organization_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid customer ID")

    status_key = _get_status_key(organization_id, provider)
    status = _sync_status.get(status_key)

    if not status:
        # Check database for last sync time
        async with get_session(organization_id=organization_id) as session:
            result = await session.execute(
                select(Integration).where(
                    Integration.organization_id == UUID(organization_id),
                    Integration.provider == provider,
                )
            )
            integration = result.scalar_one_or_none()

            if integration and integration.last_sync_at:
                return SyncStatusResponse(
                    organization_id=organization_id,
                    provider=provider,
                    status="completed",
                    started_at=None,
                    completed_at=f"{integration.last_sync_at.isoformat()}Z",
                    error=integration.last_error,
                    counts=None,
                )

        return SyncStatusResponse(
            organization_id=organization_id,
            provider=provider,
            status="never_synced",
            started_at=None,
            completed_at=None,
            error=None,
            counts=None,
        )

    started_at = status.get("started_at")
    completed_at = status.get("completed_at")
    counts = status.get("counts")

    return SyncStatusResponse(
        organization_id=organization_id,
        provider=provider,
        status=str(status.get("status", "unknown")),
        started_at=f"{started_at.isoformat()}Z" if isinstance(started_at, datetime) else None,
        completed_at=f"{completed_at.isoformat()}Z" if isinstance(completed_at, datetime) else None,
        error=str(status["error"]) if status.get("error") else None,
        counts=counts if isinstance(counts, dict) else None,
    )


@router.post("/{organization_id}/all", response_model=SyncAllResponse)
async def trigger_sync_all(
    organization_id: str, background_tasks: BackgroundTasks
) -> SyncAllResponse:
    """Trigger sync for all active integrations."""
    try:
        customer_uuid = UUID(organization_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid customer ID")

    # Get all active integrations
    async with get_session(organization_id=organization_id) as session:
        result = await session.execute(
            select(Integration).where(
                Integration.organization_id == customer_uuid,
                Integration.is_active == True,
            )
        )
        integrations = result.scalars().all()

    if not integrations:
        raise HTTPException(status_code=404, detail="No active integrations found")

    providers = [i.provider for i in integrations]

    # Trigger sync for each integration
    for provider in providers:
        if provider in CONNECTORS:
            status_key = _get_status_key(organization_id, provider)
            _sync_status[status_key] = {
                "status": "syncing",
                "started_at": datetime.utcnow(),
                "completed_at": None,
                "error": None,
                "counts": None,
            }
            background_tasks.add_task(sync_integration_data, organization_id, provider)

    return SyncAllResponse(
        status="syncing",
        organization_id=organization_id,
        integrations=providers,
    )


async def sync_integration_data(organization_id: str, provider: str) -> None:
    """Background task to sync data for a specific integration."""
    status_key = _get_status_key(organization_id, provider)
    connector_class = CONNECTORS.get(provider)

    if not connector_class:
        _sync_status[status_key]["status"] = "failed"
        _sync_status[status_key]["error"] = f"Unknown provider: {provider}"
        _sync_status[status_key]["completed_at"] = datetime.utcnow()
        return

    try:
        print(f"[Sync] Starting sync for {provider} in org {organization_id}")
        connector = connector_class(organization_id)
        counts = await connector.sync_all()
        print(f"[Sync] sync_all returned counts: {counts}")
        await connector.update_last_sync(counts)
        print(f"[Sync] Completed sync for {provider}, saved sync_stats: {counts}")

        _sync_status[status_key]["status"] = "completed"
        _sync_status[status_key]["completed_at"] = datetime.utcnow()
        _sync_status[status_key]["counts"] = counts

        # Generate embeddings for newly synced activities (non-blocking)
        try:
            from services.embedding_sync import generate_embeddings_for_organization
            embedded_count = await generate_embeddings_for_organization(
                organization_id, limit=500  # Limit per sync to avoid timeout
            )
            if embedded_count > 0:
                print(f"Generated embeddings for {embedded_count} activities")
        except Exception as embed_err:
            # Don't fail sync if embedding generation fails
            print(f"Warning: Embedding generation failed: {embed_err}")

        # Emit sync completed event for workflow triggers
        try:
            from workers.events import emit_event
            await emit_event(
                event_type="sync.completed",
                organization_id=organization_id,
                data={
                    "provider": provider,
                    "counts": counts,
                    "completed_at": datetime.utcnow().isoformat(),
                },
            )
        except Exception as event_err:
            print(f"Warning: Event emission failed: {event_err}")

    except SyncCancelledError as e:
        cancel_msg = str(e)
        print(f"[Sync] CANCELLED syncing {provider} for org {organization_id}: {cancel_msg}")

        _sync_status[status_key]["status"] = "cancelled"
        _sync_status[status_key]["error"] = cancel_msg
        _sync_status[status_key]["completed_at"] = datetime.utcnow()

        # Emit sync cancelled event (best effort)
        try:
            from workers.events import emit_event
            await emit_event(
                event_type="sync.cancelled",
                organization_id=organization_id,
                data={
                    "provider": provider,
                    "message": cancel_msg,
                    "cancelled_at": datetime.utcnow().isoformat(),
                },
            )
        except Exception:
            pass

    except Exception as e:
        import traceback
        error_msg = str(e)
        full_traceback = traceback.format_exc()
        print(f"[Sync] ERROR syncing {provider} for org {organization_id}: {error_msg}")
        print(f"[Sync] Traceback:\n{full_traceback}")
        
        _sync_status[status_key]["status"] = "failed"
        _sync_status[status_key]["error"] = error_msg
        _sync_status[status_key]["completed_at"] = datetime.utcnow()

        # Record error in database
        try:
            connector = connector_class(organization_id)
            await connector.record_error(error_msg)
        except Exception as record_err:
            print(f"[Sync] Failed to record error to DB: {record_err}")

        # Emit sync failed event
        try:
            from workers.events import emit_event
            await emit_event(
                event_type="sync.failed",
                organization_id=organization_id,
                data={
                    "provider": provider,
                    "error": error_msg,
                    "failed_at": datetime.utcnow().isoformat(),
                },
            )
        except Exception:
            pass


class OwnerMatchResult(BaseModel):
    """Single owner match result."""

    email: str
    hubspot_owner_id: str | None
    user_id: str | None
    user_name: str | None
    matched: bool


class OwnerMatchResponse(BaseModel):
    """Response model for HubSpot owner matching."""

    matched: int
    unmatched: int
    results: list[OwnerMatchResult]


@router.post(
    "/{organization_id}/hubspot/match-owners",
    response_model=OwnerMatchResponse,
)
async def match_hubspot_owners(organization_id: str) -> OwnerMatchResponse:
    """
    Fetch all HubSpot owners and match them to local users by email.

    Persists mappings in ``user_mappings_for_identity`` for every match found.
    """
    try:
        UUID(organization_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid organization ID")

    # Verify HubSpot integration is active
    async with get_session(organization_id=organization_id) as session:
        result = await session.execute(
            select(Integration).where(
                Integration.organization_id == UUID(organization_id),
                Integration.provider == "hubspot",
                Integration.is_active == True,
            )
        )
        integration: Integration | None = result.scalar_one_or_none()
        if not integration:
            raise HTTPException(
                status_code=404, detail="No active HubSpot integration found"
            )

    connector: HubSpotConnector = HubSpotConnector(organization_id)
    raw_results: list[dict[str, Any]] = await connector.match_owners_to_users()

    results: list[OwnerMatchResult] = [
        OwnerMatchResult(
            email=r["email"],
            hubspot_owner_id=r["hubspot_owner_id"],
            user_id=r.get("user_id"),
            user_name=r.get("user_name"),
            matched=r["matched"],
        )
        for r in raw_results
    ]
    matched_count: int = sum(1 for r in results if r.matched)
    unmatched_count: int = len(results) - matched_count

    return OwnerMatchResponse(
        matched=matched_count,
        unmatched=unmatched_count,
        results=results,
    )


# =============================================================================
# GitHub-specific endpoints
# =============================================================================


class GitHubRepoResponse(BaseModel):
    """A GitHub repository."""

    github_repo_id: int
    owner: str
    name: str
    full_name: str
    description: Optional[str] = None
    default_branch: str = "main"
    is_private: bool = False
    language: Optional[str] = None
    url: str


class GitHubAvailableReposResponse(BaseModel):
    """Available repos from the GitHub token."""

    repos: list[GitHubRepoResponse]


class GitHubTrackedRepoResponse(BaseModel):
    """A tracked repository record."""

    id: str
    organization_id: str
    github_repo_id: int
    owner: str
    name: str
    full_name: str
    description: Optional[str] = None
    default_branch: str
    is_private: bool
    language: Optional[str] = None
    url: str
    is_tracked: bool
    last_sync_at: Optional[str] = None
    created_at: Optional[str] = None


class GitHubTrackedReposResponse(BaseModel):
    """List of tracked repos."""

    repos: list[GitHubTrackedRepoResponse]


class GitHubTrackReposRequest(BaseModel):
    """Request to track specific repos."""

    github_repo_ids: list[int]


@router.get(
    "/{organization_id}/github/repos",
    response_model=GitHubAvailableReposResponse,
)
async def list_github_repos(organization_id: str) -> GitHubAvailableReposResponse:
    """List all GitHub repos accessible to the connected token."""
    try:
        UUID(organization_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid organization ID")

    async with get_session(organization_id=organization_id) as session:
        result = await session.execute(
            select(Integration).where(
                Integration.organization_id == UUID(organization_id),
                Integration.provider == "github",
                Integration.is_active == True,
            )
        )
        if not result.scalar_one_or_none():
            raise HTTPException(
                status_code=404, detail="No active GitHub integration found"
            )

    connector: GitHubConnector = GitHubConnector(organization_id)
    repos: list[dict[str, Any]] = await connector.list_available_repos()
    return GitHubAvailableReposResponse(
        repos=[GitHubRepoResponse(**r) for r in repos]
    )


@router.post(
    "/{organization_id}/github/repos/track",
    response_model=GitHubTrackedReposResponse,
)
async def track_github_repos(
    organization_id: str, body: GitHubTrackReposRequest
) -> GitHubTrackedReposResponse:
    """Select specific repos to track for this organization."""
    try:
        UUID(organization_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid organization ID")

    if not body.github_repo_ids:
        raise HTTPException(status_code=400, detail="No repo IDs provided")

    connector: GitHubConnector = GitHubConnector(organization_id)
    tracked: list[dict[str, Any]] = await connector.track_repos(body.github_repo_ids)
    return GitHubTrackedReposResponse(
        repos=[GitHubTrackedRepoResponse(**r) for r in tracked]
    )


@router.post("/{organization_id}/github/repos/untrack")
async def untrack_github_repos(
    organization_id: str, body: GitHubTrackReposRequest
) -> dict[str, str]:
    """Stop tracking specific repos (data is preserved)."""
    try:
        UUID(organization_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid organization ID")

    connector: GitHubConnector = GitHubConnector(organization_id)
    await connector.untrack_repos(body.github_repo_ids)
    return {"status": "ok"}


@router.get(
    "/{organization_id}/github/repos/tracked",
    response_model=GitHubTrackedReposResponse,
)
async def get_tracked_github_repos(
    organization_id: str,
) -> GitHubTrackedReposResponse:
    """Get all currently tracked repos for this organization."""
    from models.github_repository import GitHubRepository

    try:
        org_uuid: UUID = UUID(organization_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid organization ID")

    async with get_session(organization_id=organization_id) as session:
        result = await session.execute(
            select(GitHubRepository).where(
                GitHubRepository.organization_id == org_uuid,
                GitHubRepository.is_tracked == True,
            )
        )
        repos = result.scalars().all()

    return GitHubTrackedReposResponse(
        repos=[GitHubTrackedRepoResponse(**r.to_dict()) for r in repos]
    )


@router.post("/{organization_id}/github/match-users")
async def match_github_users(
    organization_id: str,
) -> dict[str, Any]:
    """
    Match GitHub commit authors to internal users by email and persist
    identity mappings. Also backfills user_id on existing commits/PRs.
    """
    try:
        UUID(organization_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid organization ID")

    connector: GitHubConnector = GitHubConnector(organization_id)
    match_results: list[dict[str, Any]] = (
        await connector.match_github_users_to_team()
    )
    backfilled: int = await connector._backfill_user_ids()

    return {
        "status": "ok",
        "matched": sum(1 for r in match_results if r["matched"]),
        "unmatched": sum(1 for r in match_results if not r["matched"]),
        "backfilled_rows": backfilled,
        "details": match_results,
    }


# Legacy endpoint for backwards compatibility
@router.post("/{organization_id}")
async def trigger_sync_legacy(
    organization_id: str, background_tasks: BackgroundTasks
) -> SyncAllResponse:
    """Legacy endpoint - triggers sync for all integrations."""
    return await trigger_sync_all(organization_id, background_tasks)


# ============================================================================
# Celery-based sync endpoints (queued execution)
# ============================================================================


class QueuedSyncResponse(BaseModel):
    """Response model for queued sync."""

    status: str
    task_id: str
    organization_id: str
    provider: Optional[str] = None


@router.post("/{organization_id}/{provider}/queue", response_model=QueuedSyncResponse)
async def queue_sync(organization_id: str, provider: str) -> QueuedSyncResponse:
    """
    Queue a sync for execution via Celery worker.
    
    This is the preferred method for scheduled syncs as it:
    - Handles retries automatically
    - Doesn't block the API server
    - Can be monitored via task_id
    """
    try:
        customer_uuid = UUID(organization_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid customer ID")

    if provider not in CONNECTORS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown provider: {provider}. Available: {list(CONNECTORS.keys())}",
        )

    # Verify integration exists
    async with get_session(organization_id=organization_id) as session:
        result = await session.execute(
            select(Integration).where(
                Integration.organization_id == customer_uuid,
                Integration.provider == provider,
                Integration.is_active == True,
            )
        )
        integration = result.scalar_one_or_none()

        if not integration:
            raise HTTPException(
                status_code=404,
                detail=f"No active {provider} integration found",
            )

    # Queue task via Celery
    from workers.tasks.sync import sync_integration
    task = sync_integration.delay(organization_id, provider)

    return QueuedSyncResponse(
        status="queued",
        task_id=task.id,
        organization_id=organization_id,
        provider=provider,
    )


@router.post("/{organization_id}/all/queue", response_model=QueuedSyncResponse)
async def queue_sync_all(organization_id: str) -> QueuedSyncResponse:
    """Queue sync for all integrations via Celery worker."""
    try:
        customer_uuid = UUID(organization_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid customer ID")

    # Verify org has integrations
    async with get_session(organization_id=organization_id) as session:
        result = await session.execute(
            select(Integration).where(
                Integration.organization_id == customer_uuid,
                Integration.is_active == True,
            )
        )
        integrations = result.scalars().all()

    if not integrations:
        raise HTTPException(status_code=404, detail="No active integrations found")

    # Queue task via Celery
    from workers.tasks.sync import sync_organization
    task = sync_organization.delay(organization_id)

    return QueuedSyncResponse(
        status="queued",
        task_id=task.id,
        organization_id=organization_id,
    )
