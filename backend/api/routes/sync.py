"""
Sync trigger endpoints for all integrations.

Endpoints:
- POST /api/sync/{organization_id}/{provider} - Trigger sync for specific integration
- GET /api/sync/{organization_id}/{provider}/status - Get sync status
- POST /api/sync/{organization_id}/all - Sync all active integrations
"""
from __future__ import annotations

from datetime import datetime
import ast
import logging
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from connectors.base import SyncCancelledError
from connectors.registry import discover_connectors
from models.database import get_session
from models.integration import Integration
from models.organization import Organization
from models.agent_task import AgentTask
from models.conversation import Conversation
from models.workflow import Workflow, WorkflowRun

router = APIRouter()
logger = logging.getLogger(__name__)

# Connector registry – auto-discovered from backend/connectors/ + entry_points
CONNECTORS = discover_connectors()

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
    """Normalized running job for admin monitoring."""

    id: str
    type: str
    status: str
    organization_id: str | None = None
    organization_name: str | None = None
    started_at: str | None = None
    title: str
    description: str
    metadata: dict[str, Any] | None = None


class AdminRunningJobsResponse(BaseModel):
    """Response model for admin running jobs list."""

    jobs: list[AdminRunningJob]
    total: int


class AdminCancelJobRequest(BaseModel):
    """Admin request model for cancelling a job."""

    job_type: str


class AdminCancelJobResponse(BaseModel):
    """Response model for cancelling a running job."""

    status: str
    job_id: str
    job_type: str
    message: str


async def _require_global_admin(user_id: str) -> None:
    """Raise if the user is not a global admin."""
    from models.database import get_admin_session
    from models.user import User

    async with get_admin_session() as session:
        result = await session.execute(select(User).where(User.id == UUID(user_id)))
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        if "global_admin" not in (user.roles or []):
            raise HTTPException(status_code=403, detail="Requires global_admin role")


def _parse_celery_args(args: Any) -> list[Any]:
    """Normalize Celery active-task args into a list."""
    if isinstance(args, list):
        return args
    if isinstance(args, tuple):
        return list(args)
    if isinstance(args, str):
        args = args.strip()
        if not args:
            return []
        try:
            parsed = ast.literal_eval(args)
            if isinstance(parsed, tuple):
                return list(parsed)
            if isinstance(parsed, list):
                return parsed
            return [parsed]
        except Exception:
            return [args]
    return []


def _get_celery_task_args(task: dict[str, Any]) -> list[Any]:
    """Extract Celery task args across protocol/version variants."""
    parsed_args = _parse_celery_args(task.get("args"))
    if parsed_args:
        return parsed_args
    # Celery can report repr-only fields depending on worker/protocol versions.
    parsed_argsrepr = _parse_celery_args(task.get("argsrepr"))
    if parsed_argsrepr:
        logger.debug("Parsed Celery args from argsrepr for task %s", task.get("id"))
    return parsed_argsrepr


def _parse_celery_kwargs(kwargs: Any) -> dict[str, Any]:
    """Normalize Celery active-task kwargs into a dict."""
    if isinstance(kwargs, dict):
        return kwargs
    if isinstance(kwargs, str):
        kwargs = kwargs.strip()
        if not kwargs:
            return {}
        try:
            parsed = ast.literal_eval(kwargs)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _get_celery_task_kwargs(task: dict[str, Any]) -> dict[str, Any]:
    """Extract Celery task kwargs across protocol/version variants."""
    parsed_kwargs = _parse_celery_kwargs(task.get("kwargs"))
    if parsed_kwargs:
        return parsed_kwargs
    # Celery can report repr-only fields depending on worker/protocol versions.
    parsed_kwargsrepr = _parse_celery_kwargs(task.get("kwargsrepr"))
    if parsed_kwargsrepr:
        logger.debug("Parsed Celery kwargs from kwargsrepr for task %s", task.get("id"))
    return parsed_kwargsrepr


def _is_workflow_task(task_name: str) -> bool:
    """Whether a task name represents workflow execution."""
    normalized_name = task_name.strip()
    if not normalized_name:
        return False
    return normalized_name.endswith(".workflows.execute_workflow") or normalized_name == "workers.tasks.workflows.execute_workflow"


def _is_trackable_admin_task(task_name: str) -> bool:
    """Whether a Celery task should appear in the admin running-jobs pane."""
    if ".tasks.sync." in task_name or task_name.startswith("workers.tasks.sync"):
        return True
    return _is_workflow_task(task_name)


def _extract_workflow_task_org_id(args: list[Any], kwargs: dict[str, Any]) -> str | None:
    """Extract workflow organization_id from kwargs-first, then legacy positional args."""
    org_id = kwargs.get("organization_id")
    if org_id:
        return str(org_id)
    if len(args) >= 5 and args[4] is not None:
        return str(args[4])
    if len(args) >= 1 and isinstance(args[-1], str):
        # Best-effort fallback for older/variant signatures.
        return str(args[-1])
    return None


def _is_active_workflow_run_status(status: str) -> bool:
    """Whether a workflow_run row represents in-progress work."""
    normalized = status.strip().lower()
    return normalized in {"pending", "running"}


def _build_workflow_run_admin_job(
    run: WorkflowRun,
    workflow_name: str | None,
    organization_name: str | None,
) -> AdminRunningJob:
    """Build an admin running-job payload from a workflow_runs row."""
    trigger_label = run.triggered_by or "unknown"
    workflow_label = workflow_name or str(run.workflow_id)
    return AdminRunningJob(
        id=str(run.id),
        type="workflow",
        status=run.status,
        organization_id=str(run.organization_id),
        organization_name=organization_name,
        started_at=run.started_at.isoformat() if run.started_at else None,
        title=f"Workflow run: {workflow_label}",
        description=f"Status={run.status} trigger={trigger_label}",
        metadata={
            "source": "workflow_runs",
            "workflow_id": str(run.workflow_id),
            "workflow_name": workflow_name,
            "triggered_by": run.triggered_by,
        },
    )


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


@router.get("/admin/jobs", response_model=AdminRunningJobsResponse)
async def list_admin_running_jobs(user_id: str) -> AdminRunningJobsResponse:
    """List currently running jobs across chats, workflows, and connector syncs."""
    from models.database import get_admin_session
    from workers.celery_app import celery_app
    from models.workflow import WorkflowRun, Workflow

    await _require_global_admin(user_id)

    async with get_admin_session() as session:
        org_rows = await session.execute(select(Organization.id, Organization.name))
        org_name_by_id = {str(row[0]): row[1] for row in org_rows.all()}

        chat_rows = await session.execute(
            select(AgentTask, Conversation)
            .join(Conversation, AgentTask.conversation_id == Conversation.id)
            .where(AgentTask.status == "running")
            .order_by(AgentTask.started_at.desc())
        )

        workflow_rows = await session.execute(
            select(WorkflowRun, Workflow)
            .join(Workflow, WorkflowRun.workflow_id == Workflow.id)
            .where(WorkflowRun.status.in_(["pending", "running"]))
            .order_by(WorkflowRun.started_at.desc())
        )

    jobs: list[AdminRunningJob] = []

    for agent_task, conversation in chat_rows.all():
        org_id = str(agent_task.organization_id)
        jobs.append(
            AdminRunningJob(
                id=str(agent_task.id),
                type="chat",
                status=agent_task.status,
                organization_id=org_id,
                organization_name=org_name_by_id.get(org_id),
                started_at=agent_task.started_at.isoformat() if agent_task.started_at else None,
                title=conversation.title or "Chat task",
                description=agent_task.user_message,
                metadata={
                    "conversation_id": str(conversation.id),
                    "conversation_type": conversation.type,
                    "user_id": str(agent_task.user_id),
                },
            )
        )

    for workflow_run, workflow in workflow_rows.all():
        org_id = str(workflow_run.organization_id)
        jobs.append(
            AdminRunningJob(
                id=str(workflow_run.id),
                type="workflow",
                status=workflow_run.status,
                organization_id=org_id,
                organization_name=org_name_by_id.get(org_id),
                started_at=workflow_run.started_at.isoformat() if workflow_run.started_at else None,
                title=workflow.name,
                description=f"Workflow run triggered by {workflow_run.triggered_by}",
                metadata={
                    "workflow_id": str(workflow.id),
                    "workflow_name": workflow.name,
                    "triggered_by": workflow_run.triggered_by,
                    "conversation_id": (workflow_run.output or {}).get("conversation_id") if isinstance(workflow_run.output, dict) else None,
                },
            )
        )

    try:
        inspector = celery_app.control.inspect(timeout=1.5)
        active_by_worker = inspector.active() or {}
        logger.info("Fetched Celery active tasks from %d workers", len(active_by_worker))
    except Exception as exc:
        logger.warning("Failed to inspect celery active tasks: %s", exc)
        active_by_worker = {}

    for worker_name, active_tasks in active_by_worker.items():
        for task in active_tasks:
            task_name = str(task.get("name") or task.get("type") or "")
            if not _is_trackable_admin_task(task_name):
                continue

            task_id = str(task.get("id"))
            args = _get_celery_task_args(task)
            kwargs = _get_celery_task_kwargs(task)
            is_workflow_task = _is_workflow_task(task_name)
            task_type = "workflow" if is_workflow_task else "connector_sync"

            if task_type == "workflow":
                org_id = _extract_workflow_task_org_id(args, kwargs)
            else:
                org_id = str(args[0]) if args else None

            org_name = org_name_by_id.get(org_id) if org_id else None
            provider = str(args[1]) if task_type == "connector_sync" and len(args) > 1 else None

            workflow_id = str(kwargs.get("workflow_id")) if kwargs.get("workflow_id") is not None else (str(args[0]) if args else None)

            title = "Workflow execution" if task_type == "workflow" else f"{provider or 'connector'} sync"
            description = f"Running on worker {worker_name}"
            if task_type == "workflow" and workflow_id:
                description = f"Workflow {workflow_id} running on worker {worker_name}"

            jobs.append(
                AdminRunningJob(
                    id=task_id,
                    type=task_type,
                    status="running",
                    organization_id=org_id,
                    organization_name=org_name,
                    started_at=task.get("time_start"),
                    title=title,
                    description=description,
                    metadata={
                        "task_name": task_name,
                        "worker": worker_name,
                        "args": [str(arg) for arg in args],
                        "kwargs": {k: str(v) for k, v in kwargs.items()},
                    },
                )
            )

    jobs.sort(key=lambda job: job.started_at or "", reverse=True)
    logger.info("Returning %d running jobs for admin user %s", len(jobs), user_id)
    return AdminRunningJobsResponse(jobs=jobs, total=len(jobs))


@router.post("/admin/jobs/{job_id}/cancel", response_model=AdminCancelJobResponse)
async def cancel_admin_running_job(
    job_id: str,
    request: AdminCancelJobRequest,
    user_id: str,
) -> AdminCancelJobResponse:
    """Cancel a running admin-visible job."""
    from services.task_manager import task_manager
    from workers.celery_app import celery_app
    from models.database import get_admin_session
    from models.workflow import WorkflowRun

    await _require_global_admin(user_id)

    if request.job_type == "chat":
        cancelled = await task_manager.cancel_task(job_id)
        if not cancelled:
            raise HTTPException(status_code=404, detail="Running chat job not found")
        logger.info("Admin %s cancelled chat job %s", user_id, job_id)
        return AdminCancelJobResponse(
            status="cancelled",
            job_id=job_id,
            job_type=request.job_type,
            message="Chat task cancellation requested",
        )

    if request.job_type not in {"workflow", "connector_sync"}:
        raise HTTPException(status_code=400, detail="Unsupported job_type")

    if request.job_type == "workflow":
        # First try treating job_id as WorkflowRun.id (DB-backed running workflow)
        try:
            run_uuid = UUID(job_id)
        except ValueError:
            run_uuid = None

        if run_uuid is not None:
            async with get_admin_session() as session:
                result = await session.execute(
                    select(WorkflowRun).where(WorkflowRun.id == run_uuid)
                )
                run = result.scalar_one_or_none()
                if run and run.status == "running":
                    run.status = "cancelled"
                    run.completed_at = datetime.utcnow()
                    run.error_message = "Cancelled by admin"
                    await session.commit()
                    logger.info("Admin %s cancelled workflow run %s", user_id, job_id)
                    return AdminCancelJobResponse(
                        status="cancelled",
                        job_id=job_id,
                        job_type=request.job_type,
                        message="Workflow run marked as cancelled",
                    )

    celery_app.control.revoke(job_id, terminate=True)
    logger.info("Admin %s revoked celery task %s of type %s", user_id, job_id, request.job_type)
    return AdminCancelJobResponse(
        status="cancelled",
        job_id=job_id,
        job_type=request.job_type,
        message="Celery task revoke requested",
    )


# =============================================================================
# Organization-scoped endpoints
# =============================================================================


@router.post("/{organization_id}/{provider}", response_model=SyncTriggerResponse)
async def trigger_sync(
    organization_id: str, provider: str, background_tasks: BackgroundTasks
) -> SyncTriggerResponse:
    """Trigger a sync for a specific integration.

    Per-user integrations (Gmail, Calendar, etc.) may have multiple rows per
    provider.  We sync each one individually so every connected user gets
    updated.
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

    # Fetch *all* active integrations for this provider (may be per-user)
    async with get_session(organization_id=organization_id) as session:
        result = await session.execute(
            select(Integration).where(
                Integration.organization_id == customer_uuid,
                Integration.provider == provider,
                Integration.is_active == True,
            )
        )
        integrations = result.scalars().all()

        if not integrations:
            raise HTTPException(
                status_code=404,
                detail=f"No active {provider} integration found",
            )

    # Initialize sync status
    status_key: str = _get_status_key(organization_id, provider)
    _sync_status[status_key] = {
        "status": "syncing",
        "started_at": datetime.utcnow(),
        "completed_at": None,
        "error": None,
        "counts": None,
    }

    # Queue a background sync for each per-user integration
    for integration in integrations:
        user_id: str | None = str(integration.user_id) if integration.user_id else None
        background_tasks.add_task(
            sync_integration_data, organization_id, provider, user_id
        )

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

    providers: list[str] = list({i.provider for i in integrations})

    # Trigger sync for each integration (including per-user variants)
    for integration in integrations:
        prov: str = integration.provider
        if prov in CONNECTORS:
            status_key: str = _get_status_key(organization_id, prov)
            _sync_status[status_key] = {
                "status": "syncing",
                "started_at": datetime.utcnow(),
                "completed_at": None,
                "error": None,
                "counts": None,
            }
            uid: str | None = str(integration.user_id) if integration.user_id else None
            background_tasks.add_task(
                sync_integration_data, organization_id, prov, uid
            )

    return SyncAllResponse(
        status="syncing",
        organization_id=organization_id,
        integrations=providers,
    )


async def sync_integration_data(
    organization_id: str,
    provider: str,
    user_id: str | None = None,
) -> None:
    """Background task to sync data for a specific integration.

    Args:
        organization_id: UUID of the organization.
        provider: Integration provider name.
        user_id: Optional UUID of the user who owns this integration
                 (for per-user providers like Gmail, Calendar, etc.).
    """
    status_key: str = _get_status_key(organization_id, provider)
    connector_class = CONNECTORS.get(provider)

    if not connector_class:
        _sync_status[status_key]["status"] = "failed"
        _sync_status[status_key]["error"] = f"Unknown provider: {provider}"
        _sync_status[status_key]["completed_at"] = datetime.utcnow()
        return

    try:
        user_label: str = f" user={user_id}" if user_id else ""
        print(f"[Sync] Starting sync for {provider} in org {organization_id}{user_label}")
        connector = connector_class(organization_id, user_id=user_id)
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

    # Verify at least one integration exists
    async with get_session(organization_id=organization_id) as session:
        result = await session.execute(
            select(Integration).where(
                Integration.organization_id == customer_uuid,
                Integration.provider == provider,
                Integration.is_active == True,
            )
        )
        integrations = result.scalars().all()

        if not integrations:
            raise HTTPException(
                status_code=404,
                detail=f"No active {provider} integration found",
            )

    # Queue a Celery task for each per-user integration
    from workers.tasks.sync import sync_integration
    last_task_id: str = ""
    for integration in integrations:
        uid: str | None = str(integration.user_id) if integration.user_id else None
        task = sync_integration.delay(organization_id, provider, uid)
        last_task_id = task.id

    return QueuedSyncResponse(
        status="queued",
        task_id=last_task_id,
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
