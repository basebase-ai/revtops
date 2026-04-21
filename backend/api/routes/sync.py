"""
Sync trigger endpoints for all integrations.

Work runs on Celery workers (``sync_integration`` task), not in the API process.

Endpoints:
- POST /api/sync/{organization_id}/{provider} - Queue sync for specific integration
- GET /api/sync/{organization_id}/{provider}/status - Get sync status (DB-backed)
- POST /api/sync/{organization_id}/all - Queue sync for all integrations (org admin or global_admin; JWT required)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import ast
import logging
import uuid as uuid_mod
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, update

from connectors.github import GitHubConnector
from connectors.hubspot import HubSpotConnector
from connectors.slack import SlackConnector
from connectors.registry import Capability, discover_connectors
from api.auth_middleware import AuthContext, get_current_auth, require_global_admin
from api.routes.auth import _can_administer_org
from models.database import get_admin_session, get_session
from models.user import User
from models.integration import Integration
from models.organization import Organization
from models.org_member import OrgMember
from models.external_identity_mapping import ExternalIdentityMapping
from models.agent_task import AgentTask
from models.conversation import Conversation
from models.workflow import Workflow, WorkflowRun

router = APIRouter()
logger = logging.getLogger(__name__)

# Connector registry – auto-discovered from backend/connectors/ + entry_points
CONNECTORS = discover_connectors()


def parse_sync_since_param(since: str | None) -> datetime | None:
    """Parse optional ``since`` query into naive UTC for ``sync_since_override``."""
    if since is None:
        return None
    stripped: str = since.strip()
    if not stripped:
        return None
    norm: str = stripped[:-1] + "+00:00" if stripped.endswith("Z") else stripped
    try:
        parsed: datetime = datetime.fromisoformat(norm)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="Invalid since parameter: expected ISO8601 datetime",
        ) from exc
    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed

class SyncStatusResponse(BaseModel):
    """Response model for sync status.

    ``status`` is one of: ``syncing``, ``failed``, ``completed``, ``never_synced``.
    """

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


# =============================================================================
# Admin endpoints (must be defined BEFORE parameterized routes)
# =============================================================================


class GlobalSyncResponse(BaseModel):
    """Response model for global sync (all organizations)."""

    status: str
    task_id: str
    integration_count: int


class AdminQueueTaskResponse(BaseModel):
    """Response model for queued admin maintenance tasks."""

    status: str
    task_id: str


class AdminFireIncidentResponse(BaseModel):
    """Response model for manually triggered PagerDuty incidents."""

    status: str
    title: str


class AdminIntegration(BaseModel):
    """Integration info for admin view."""

    id: str
    organization_id: str
    organization_name: str
    provider: str
    is_active: bool
    last_sync_at: str | None
    last_error: str | None
    sync_stats: dict[str, int | str] | None
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


class AdminKillAllJobsResponse(BaseModel):
    """Response model for emergency kill-all jobs operation."""

    status: str
    message: str
    revoked_task_count: int
    queue_purged_count: int
    workflow_pause_until: str


def _ensure_org_path_matches_auth(organization_id: str, auth: AuthContext) -> None:
    """Require active org context and that path org matches JWT org (unless global admin)."""
    if auth.is_global_admin:
        return
    if auth.organization_id is None:
        raise HTTPException(
            status_code=403,
            detail="User not associated with an organization",
        )
    if str(auth.organization_id) != organization_id:
        raise HTTPException(
            status_code=403,
            detail="Not authorized for this organization",
        )


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


def _normalize_task_started_at(raw_started_at: Any, task_id: str) -> str | None:
    """Normalize Celery active-task ``time_start`` into an ISO8601 string."""
    if raw_started_at is None:
        return None

    if isinstance(raw_started_at, datetime):
        return raw_started_at.isoformat()

    if isinstance(raw_started_at, (int, float)):
        return datetime.fromtimestamp(raw_started_at, tz=timezone.utc).isoformat()

    if isinstance(raw_started_at, str):
        normalized_value = raw_started_at.strip()
        if not normalized_value:
            return None
        try:
            parsed_epoch = float(normalized_value)
        except ValueError:
            return normalized_value
        return datetime.fromtimestamp(parsed_epoch, tz=timezone.utc).isoformat()

    logger.debug(
        "Unable to normalize Celery task started_at for task %s (type=%s)",
        task_id,
        type(raw_started_at).__name__,
    )
    return None


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
async def list_admin_integrations(
    auth: AuthContext = Depends(require_global_admin),
) -> AdminIntegrationsResponse:
    """
    List all integrations across all organizations (global admin only).
    """
    from models.database import get_admin_session

    async with get_admin_session() as session:
        # Get all integrations with org names
        result = await session.execute(
            select(Integration, Organization.name.label("org_name"))
            .join(Organization, Integration.organization_id == Organization.id)
            .order_by(Organization.name, Integration.connector)
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
            provider=integration.connector,
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
async def trigger_global_sync(
    auth: AuthContext = Depends(require_global_admin),
) -> GlobalSyncResponse:
    """
    Trigger sync for ALL organizations (global admin only).

    This calls the same task that the hourly beat scheduler runs.
    """
    from models.database import get_admin_session

    async with get_admin_session() as session:
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


@router.post("/admin/dependency-checks", response_model=AdminQueueTaskResponse)
async def trigger_dependency_checks(
    auth: AuthContext = Depends(require_global_admin),
) -> AdminQueueTaskResponse:
    """Trigger dependency checks immediately (global admin only)."""
    from workers.tasks.monitoring import monitor_dependencies

    task = monitor_dependencies.delay()
    logger.warning(
        "Admin user %s queued dependency checks task_id=%s",
        auth.user_id,
        task.id,
    )
    return AdminQueueTaskResponse(status="queued", task_id=task.id)


@router.post("/admin/fire-incident", response_model=AdminFireIncidentResponse)
async def admin_fire_incident(
    auth: AuthContext = Depends(require_global_admin),
) -> AdminFireIncidentResponse:
    """Manually fire a PagerDuty incident to validate alerting (global admin only)."""
    from services.pagerduty import create_pagerduty_incident_with_details

    title = "Admin test incident"
    incident_result = await create_pagerduty_incident_with_details(
        title=title,
        details=(
            "Manual admin-panel trigger to validate PagerDuty wiring and on-call delivery. "
            f"Triggered by global admin user_id={auth.user_id}."
        ),
    )
    if not incident_result.ok:
        logger.error(
            "Admin user %s failed to fire PagerDuty test incident; reason=%s status=%s body=%s",
            auth.user_id,
            incident_result.reason,
            incident_result.status_code,
            incident_result.response_body,
        )
        raise HTTPException(
            status_code=503,
            detail=(
                "PagerDuty request failed "
                f"(reason={incident_result.reason}, status={incident_result.status_code})"
            ),
        )

    logger.warning("Admin user %s fired PagerDuty test incident", auth.user_id)
    return AdminFireIncidentResponse(status="sent", title=title)


@router.get("/admin/jobs", response_model=AdminRunningJobsResponse)
async def list_admin_running_jobs(
    auth: AuthContext = Depends(require_global_admin),
) -> AdminRunningJobsResponse:
    """List currently running jobs across chats, workflows, and connector syncs."""
    from models.database import get_admin_session
    from workers.celery_app import celery_app
    from models.workflow import WorkflowRun, Workflow

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
                    started_at=_normalize_task_started_at(task.get("time_start"), task_id),
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
    logger.info("Returning %d running jobs for admin user %s", len(jobs), auth.user_id)
    return AdminRunningJobsResponse(jobs=jobs, total=len(jobs))


@router.post("/admin/jobs/{job_id}/cancel", response_model=AdminCancelJobResponse)
async def cancel_admin_running_job(
    job_id: str,
    request: AdminCancelJobRequest,
    auth: AuthContext = Depends(require_global_admin),
) -> AdminCancelJobResponse:
    """Cancel a running admin-visible job."""
    from services.task_manager import task_manager
    from workers.celery_app import celery_app
    from models.database import get_admin_session
    from models.workflow import WorkflowRun

    if request.job_type == "chat":
        cancelled = await task_manager.cancel_task(job_id)
        if not cancelled:
            raise HTTPException(status_code=404, detail="Running chat job not found")
        logger.info("Admin %s cancelled chat job %s", auth.user_id, job_id)
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
                    logger.info("Admin %s cancelled workflow run %s", auth.user_id, job_id)
                    return AdminCancelJobResponse(
                        status="cancelled",
                        job_id=job_id,
                        job_type=request.job_type,
                        message="Workflow run marked as cancelled",
                    )

    celery_app.control.revoke(job_id, terminate=True)
    logger.info(
        "Admin %s revoked celery task %s of type %s",
        auth.user_id,
        job_id,
        request.job_type,
    )
    return AdminCancelJobResponse(
        status="cancelled",
        job_id=job_id,
        job_type=request.job_type,
        message="Celery task revoke requested",
    )


@router.post("/admin/jobs/kill-all", response_model=AdminKillAllJobsResponse)
async def kill_all_admin_jobs(
    auth: AuthContext = Depends(require_global_admin),
) -> AdminKillAllJobsResponse:
    """Terminate all active worker tasks, purge queued work, and pause workflows briefly."""
    from workers.celery_app import celery_app
    from services.task_manager import task_manager
    from services.workflow_pause import pause_workflow_execution_for_seconds

    revoked_ids: set[str] = set()
    queue_purged_count = 0
    pause_seconds = 60
    cancelled_chat_count = 0
    cancelled_workflow_count = 0

    try:
        inspector = celery_app.control.inspect(timeout=2.0)
        active_by_worker = inspector.active() or {}
        reserved_by_worker = inspector.reserved() or {}
        scheduled_by_worker = inspector.scheduled() or {}
    except Exception as exc:
        logger.exception("Admin %s failed to inspect celery jobs for kill-all: %s", auth.user_id, exc)
        raise HTTPException(status_code=503, detail="Failed to inspect worker queues") from exc

    def _revoke_task(task_id: str) -> None:
        normalized_task_id = str(task_id).strip()
        if not normalized_task_id or normalized_task_id in revoked_ids:
            return
        celery_app.control.revoke(normalized_task_id, terminate=True, signal="SIGKILL")
        revoked_ids.add(normalized_task_id)

    for worker_name, tasks in active_by_worker.items():
        for task in tasks:
            task_id = str(task.get("id") or "")
            _revoke_task(task_id)
        logger.warning(
            "Admin %s kill-all revoked %d active task(s) on worker=%s",
            auth.user_id,
            len(tasks),
            worker_name,
        )

    for worker_name, tasks in reserved_by_worker.items():
        for task in tasks:
            task_id = str(task.get("id") or "")
            _revoke_task(task_id)
        logger.warning(
            "Admin %s kill-all revoked %d reserved task(s) on worker=%s",
            auth.user_id,
            len(tasks),
            worker_name,
        )

    for worker_name, tasks in scheduled_by_worker.items():
        for task in tasks:
            request = task.get("request") if isinstance(task, dict) else None
            task_id = str((request or {}).get("id") or task.get("id") or "") if isinstance(task, dict) else ""
            _revoke_task(task_id)
        logger.warning(
            "Admin %s kill-all revoked %d scheduled task(s) on worker=%s",
            auth.user_id,
            len(tasks),
            worker_name,
        )

    try:
        queue_purged_count = int(celery_app.control.purge() or 0)
    except Exception as exc:
        logger.exception("Admin %s failed to purge celery queues during kill-all: %s", auth.user_id, exc)
        raise HTTPException(status_code=503, detail="Failed to purge queued tasks") from exc

    # Cancel in-process chat jobs first, then mark any remaining DB rows as cancelled
    # so the admin jobs pane does not continue to show stale "running" work.
    async with get_admin_session() as session:
        running_chat_ids_result = await session.execute(
            select(AgentTask.id).where(AgentTask.status == "running")
        )
        running_chat_ids = [str(row[0]) for row in running_chat_ids_result.all()]
    for task_id in running_chat_ids:
        try:
            if await task_manager.cancel_task(task_id):
                cancelled_chat_count += 1
        except Exception:
            logger.exception("Admin %s failed to cancel chat task %s during kill-all", auth.user_id, task_id)

    async with get_admin_session() as session:
        now = datetime.utcnow()
        chat_update_result = await session.execute(
            update(AgentTask)
            .where(AgentTask.status == "running")
            .values(
                status="cancelled",
                completed_at=now,
                last_activity_at=now,
                error_message="Cancelled by admin kill-all",
            )
            .execution_options(synchronize_session=False)
        )
        cancelled_chat_count = int(chat_update_result.rowcount or 0)
        await session.commit()

    # Mark DB-backed workflow runs as cancelled when they are still pending/running.
    async with get_admin_session() as session:
        now = datetime.utcnow()
        workflow_update_result = await session.execute(
            update(WorkflowRun)
            .where(WorkflowRun.status.in_(["pending", "running"]))
            .values(
                status="cancelled",
                completed_at=now,
                error_message="Cancelled by admin kill-all",
            )
            .execution_options(synchronize_session=False)
        )
        cancelled_workflow_count = int(workflow_update_result.rowcount or 0)
        await session.commit()

    workflow_pause_until = await pause_workflow_execution_for_seconds(seconds=pause_seconds)
    logger.warning(
        "Admin %s executed kill-all jobs operation revoked=%d purged=%d cancelled_chats=%d cancelled_workflows=%d workflow_pause_until=%s",
        auth.user_id,
        len(revoked_ids),
        queue_purged_count,
        cancelled_chat_count,
        cancelled_workflow_count,
        workflow_pause_until.isoformat(),
    )
    return AdminKillAllJobsResponse(
        status="ok",
        message=(
            "All active work was terminated, queued tasks purged, "
            "in-flight chat/workflow records cancelled, and workflow execution paused for 60 seconds"
        ),
        revoked_task_count=len(revoked_ids),
        queue_purged_count=queue_purged_count,
        workflow_pause_until=workflow_pause_until.isoformat(),
    )


# =============================================================================
# Organization-scoped endpoints
# =============================================================================


async def _ensure_org_admin_can_sync_all(organization_id: str, auth: AuthContext) -> None:
    """Require authenticated org admin (or global admin) for bulk sync."""
    if auth.user_id is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        customer_uuid = UUID(organization_id)
        requester_uuid = UUID(str(auth.user_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid customer ID") from None

    async with get_admin_session() as session:
        db_user: User | None = await session.get(User, requester_uuid)
        if db_user is None:
            raise HTTPException(status_code=401, detail="User not found")
        allowed: bool = await _can_administer_org(session, db_user, customer_uuid)
        if not allowed:
            raise HTTPException(
                status_code=403,
                detail="Org admin or global admin required to sync all integrations",
            )


async def _execute_sync_all_integrations(organization_id: str) -> SyncAllResponse:
    """Enqueue Celery sync for every active integration that supports SYNC."""
    try:
        customer_uuid = UUID(organization_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid customer ID")

    async with get_session(organization_id=organization_id) as session:
        result = await session.execute(
            select(Integration.connector, Integration.user_id).where(
                Integration.organization_id == customer_uuid,
                Integration.is_active == True,  # noqa: E712
            )
        )
        integrations: list[tuple[str, UUID | None]] = list(result.all())

    if not integrations:
        raise HTTPException(status_code=404, detail="No active integrations found")

    from workers.tasks.sync import sync_integration

    syncing_providers: list[str] = []
    for prov, integration_user_id in integrations:
        connector_cls = CONNECTORS.get(prov)
        if connector_cls is not None and Capability.SYNC in connector_cls.meta.capabilities:
            if prov not in syncing_providers:
                syncing_providers.append(prov)
            uid: str | None = str(integration_user_id) if integration_user_id else None
            sync_integration.delay(organization_id, prov, uid)

    return SyncAllResponse(
        status="queued",
        organization_id=organization_id,
        integrations=syncing_providers,
    )


@router.post("/{organization_id}/all", response_model=SyncAllResponse)
async def trigger_sync_all(
    organization_id: str,
    auth: AuthContext = Depends(get_current_auth),
) -> SyncAllResponse:
    """Trigger sync for all active integrations (org admin or global admin only)."""
    await _ensure_org_admin_can_sync_all(organization_id, auth)
    return await _execute_sync_all_integrations(organization_id)


@router.post("/{organization_id}/{provider}", response_model=SyncTriggerResponse)
async def trigger_sync(
    organization_id: str,
    provider: str,
    auth: AuthContext = Depends(get_current_auth),
    since: str | None = Query(
        default=None,
        description="ISO8601 UTC start time for manual resync (overrides last_sync_at for this run)",
    ),
) -> SyncTriggerResponse:
    """Trigger a sync for a specific integration.

    Per-user integrations (Gmail, Calendar, etc.) may have multiple rows per
    provider.  We sync each one individually so every connected user gets
    updated.

    Optional ``since`` (ISO8601) temporarily overrides the incremental window
    for this run only (e.g. resync last 7 days).
    """
    _ensure_org_path_matches_auth(organization_id, auth)
    try:
        customer_uuid = UUID(organization_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid customer ID")

    connector_cls = CONNECTORS.get(provider)
    if not connector_cls:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown provider: {provider}. Available: {list(CONNECTORS.keys())}",
        )
    if Capability.SYNC not in connector_cls.meta.capabilities:
        raise HTTPException(
            status_code=400,
            detail=f"Provider {provider} does not support sync (query-only connector).",
        )

    # Fetch *all* active integrations for this provider (may be per-user)
    async with get_session(organization_id=organization_id) as session:
        result = await session.execute(
            select(Integration.user_id).where(
                Integration.organization_id == customer_uuid,
                Integration.connector == provider,
                Integration.is_active == True,  # noqa: E712
            )
        )
        integration_user_ids: list[UUID | None] = list(result.scalars().all())

        if not integration_user_ids:
            raise HTTPException(
                status_code=404,
                detail=f"No active {provider} integration found",
            )

    sync_since_override: datetime | None = parse_sync_since_param(since)
    since_iso: str | None = (
        sync_since_override.isoformat() if sync_since_override is not None else None
    )

    from workers.tasks.sync import sync_integration

    for integration_user_id in integration_user_ids:
        user_id: str | None = str(integration_user_id) if integration_user_id else None
        sync_integration.delay(
            organization_id,
            provider,
            user_id,
            sync_since_override_iso=since_iso,
        )

    return SyncTriggerResponse(
        status="queued", organization_id=organization_id, provider=provider
    )


@router.get("/{organization_id}/{provider}/status", response_model=SyncStatusResponse)
async def get_sync_status(
    organization_id: str,
    provider: str,
    auth: AuthContext = Depends(get_current_auth),
) -> SyncStatusResponse:
    """Get sync status for a specific integration.

    Resolution order: ``syncing`` (in-flight, fresh) → ``failed`` (``last_error`` set) →
    ``completed`` (successful sync, no error) → ``never_synced``.
    """
    _ensure_org_path_matches_auth(organization_id, auth)
    try:
        UUID(organization_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid customer ID")

    # DB is the primary source of truth — check sync_started_at first
    async with get_session(organization_id=organization_id) as session:
        result = await session.execute(
            select(
                Integration.sync_stats,
                Integration.last_error,
                Integration.last_sync_at,
            ).where(
                Integration.organization_id == UUID(organization_id),
                Integration.connector == provider,
            )
        )
        integration_row: tuple[dict[str, Any] | None, str | None, datetime | None] | None = (
            result.first()
        )

    if integration_row:
        stats, last_err, last_sync_at = integration_row
        sync_started_raw: str | None = (
            stats.get("sync_started_at") if isinstance(stats, dict) else None
        )

        if sync_started_raw:
            try:
                sync_started: datetime = datetime.fromisoformat(sync_started_raw)
                stale_cutoff: timedelta = timedelta(hours=2)
                if datetime.utcnow() - sync_started < stale_cutoff:
                    return SyncStatusResponse(
                        organization_id=organization_id,
                        provider=provider,
                        status="syncing",
                        started_at=f"{sync_started.isoformat()}Z",
                        completed_at=None,
                        error=None,
                        counts=None,
                    )
            except (ValueError, TypeError):
                pass

        if last_err and last_err.strip():
            completed_at: str | None = None
            if last_sync_at:
                completed_at = f"{last_sync_at.isoformat()}Z"
            return SyncStatusResponse(
                organization_id=organization_id,
                provider=provider,
                status="failed",
                started_at=None,
                completed_at=completed_at,
                error=last_err,
                counts=None,
            )

    if integration_row and integration_row[2]:
        return SyncStatusResponse(
            organization_id=organization_id,
            provider=provider,
            status="completed",
            started_at=None,
            completed_at=f"{integration_row[2].isoformat()}Z",
            error=None,
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
async def match_hubspot_owners(
    organization_id: str,
    auth: AuthContext = Depends(get_current_auth),
) -> OwnerMatchResponse:
    """
    Fetch all HubSpot owners and match them to local users by email.

    Persists mappings in ``user_mappings_for_identity`` for every match found.
    """
    _ensure_org_path_matches_auth(organization_id, auth)
    try:
        UUID(organization_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid organization ID")

    # Verify HubSpot integration is active
    async with get_session(organization_id=organization_id) as session:
        result = await session.execute(
            select(Integration).where(
                Integration.organization_id == UUID(organization_id),
                Integration.connector == "hubspot",
                Integration.is_active == True,
            )
        )
        integration: Integration | None = result.scalars().first()
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
async def list_github_repos(
    organization_id: str,
    auth: AuthContext = Depends(get_current_auth),
) -> GitHubAvailableReposResponse:
    """List all GitHub repos accessible to the connected token."""
    _ensure_org_path_matches_auth(organization_id, auth)
    try:
        UUID(organization_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid organization ID")

    async with get_session(organization_id=organization_id) as session:
        result = await session.execute(
            select(Integration)
            .where(
                Integration.organization_id == UUID(organization_id),
                Integration.connector == "github",
                Integration.user_id == auth.user_id,
                Integration.is_active == True,
            )
            .limit(1)
        )
        if not result.scalar_one_or_none():
            raise HTTPException(
                status_code=404, detail="No active GitHub integration found"
            )

    try:
        connector: GitHubConnector = GitHubConnector(
            organization_id,
            user_id=auth.user_id_str,
        )
        repos: list[dict[str, Any]] = await connector.list_available_repos()
        return GitHubAvailableReposResponse(
            repos=[GitHubRepoResponse(**r) for r in repos]
        )
    except ValueError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        logger.exception("list_github_repos failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/{organization_id}/github/repos/track",
    response_model=GitHubTrackedReposResponse,
)
async def track_github_repos(
    organization_id: str,
    body: GitHubTrackReposRequest,
    auth: AuthContext = Depends(get_current_auth),
) -> GitHubTrackedReposResponse:
    """Select specific repos to track for this organization."""
    _ensure_org_path_matches_auth(organization_id, auth)
    try:
        UUID(organization_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid organization ID")

    if not body.github_repo_ids:
        raise HTTPException(status_code=400, detail="No repo IDs provided")

    connector: GitHubConnector = GitHubConnector(
        organization_id,
        user_id=auth.user_id_str,
    )
    tracked: list[dict[str, Any]] = await connector.track_repos(body.github_repo_ids)
    return GitHubTrackedReposResponse(
        repos=[GitHubTrackedRepoResponse(**r) for r in tracked]
    )


@router.post("/{organization_id}/github/repos/untrack")
async def untrack_github_repos(
    organization_id: str,
    body: GitHubTrackReposRequest,
    auth: AuthContext = Depends(get_current_auth),
) -> dict[str, str]:
    """Stop tracking specific repos (data is preserved)."""
    _ensure_org_path_matches_auth(organization_id, auth)
    try:
        UUID(organization_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid organization ID")

    connector: GitHubConnector = GitHubConnector(
        organization_id,
        user_id=auth.user_id_str,
    )
    await connector.untrack_repos(body.github_repo_ids)
    return {"status": "ok"}


@router.get(
    "/{organization_id}/github/repos/tracked",
    response_model=GitHubTrackedReposResponse,
)
async def get_tracked_github_repos(
    organization_id: str,
    auth: AuthContext = Depends(get_current_auth),
) -> GitHubTrackedReposResponse:
    """Get all currently tracked repos for this organization."""
    from models.github_repository import GitHubRepository

    _ensure_org_path_matches_auth(organization_id, auth)
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
        repos_payload: list[dict[str, Any]] = [
            repo.to_dict() for repo in result.scalars().all()
        ]

    return GitHubTrackedReposResponse(
        repos=[GitHubTrackedRepoResponse(**repo) for repo in repos_payload]
    )


@router.post("/{organization_id}/github/match-users")
async def match_github_users(
    organization_id: str,
    auth: AuthContext = Depends(get_current_auth),
) -> dict[str, Any]:
    """
    Match GitHub commit authors to internal users by email and persist
    identity mappings. Also backfills user_id on existing commits/PRs.
    """
    _ensure_org_path_matches_auth(organization_id, auth)
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


# ── Identity mapping wizard endpoints ────────────────────────────────


class ExternalUserRow(BaseModel):
    external_id: str
    display_name: str
    email: str | None = None
    avatar_url: str | None = None
    source: str
    suggested_user_id: str | None = None
    match_confidence: str | None = None


class TeamMemberRow(BaseModel):
    user_id: str
    name: str | None = None
    email: str | None = None
    avatar_url: str | None = None


class ListExternalUsersResponse(BaseModel):
    external_users: list[ExternalUserRow]
    team_members: list[TeamMemberRow]


class IdentityMappingEntry(BaseModel):
    external_id: str
    user_id: str
    source: str


class SaveIdentityMappingsRequest(BaseModel):
    mappings: list[IdentityMappingEntry]


class SaveIdentityMappingsResponse(BaseModel):
    saved: int


def _auto_match_users(
    external_users: list[dict[str, Any]],
    team_members: list[TeamMemberRow],
) -> list[ExternalUserRow]:
    """Match external users to team members by email and name."""
    email_to_member: dict[str, TeamMemberRow] = {
        m.email.strip().lower(): m for m in team_members if m.email
    }
    name_to_member: dict[str, TeamMemberRow] = {
        m.name.strip().lower(): m for m in team_members if m.name
    }

    rows: list[ExternalUserRow] = []
    for ext in external_users:
        suggested_id: str | None = None
        confidence: str | None = None

        ext_email: str | None = ext.get("email")
        if ext_email:
            matched: TeamMemberRow | None = email_to_member.get(ext_email.strip().lower())
            if matched:
                suggested_id = matched.user_id
                confidence = "email"

        if suggested_id is None:
            ext_name: str = (ext.get("display_name") or "").strip().lower()
            if ext_name:
                name_matched: TeamMemberRow | None = name_to_member.get(ext_name)
                if name_matched:
                    suggested_id = name_matched.user_id
                    confidence = "name"

        rows.append(ExternalUserRow(
            external_id=ext["external_id"],
            display_name=ext.get("display_name", ext["external_id"]),
            email=ext.get("email"),
            avatar_url=ext.get("avatar_url"),
            source=ext.get("source", ""),
            suggested_user_id=suggested_id,
            match_confidence=confidence,
        ))
    return rows


@router.post("/{organization_id}/{provider}/list-external-users")
async def list_external_users(
    organization_id: str,
    provider: str,
    auth: AuthContext = Depends(get_current_auth),
) -> ListExternalUsersResponse:
    """
    Fetch external users from a provider and auto-match to team members.

    Used by the identity mapping wizard during connector setup.
    """
    _ensure_org_path_matches_auth(organization_id, auth)
    org_uuid: UUID
    try:
        org_uuid = UUID(organization_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid organization ID")

    user_id_str: str = str(auth.user_id) if auth.user_id else ""

    # Fetch external users from provider
    external_users: list[dict[str, Any]]
    if provider == "github":
        connector: GitHubConnector = GitHubConnector(organization_id, user_id_str)
        external_users = await connector.list_external_users()
    elif provider == "slack":
        slack_connector: SlackConnector = SlackConnector(organization_id, user_id_str)
        external_users = await slack_connector.list_external_users()
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Identity mapping not supported for provider: {provider}",
        )

    # Load team members for matching — extract scalars inside session
    team_member_rows: list[TeamMemberRow] = []
    async with get_session(organization_id=organization_id) as session:
        member_sub = select(OrgMember.user_id).where(
            OrgMember.organization_id == org_uuid,
            OrgMember.status.in_(("active", "onboarding")),
        )
        result = await session.execute(
            select(User).where(
                User.id.in_(member_sub),
                User.is_guest.is_(False),
            )
        )
        for u in result.scalars().all():
            team_member_rows.append(TeamMemberRow(
                user_id=str(u.id),
                name=u.name,
                email=u.email,
                avatar_url=u.avatar_url,
            ))

    matched_rows: list[ExternalUserRow] = _auto_match_users(external_users, team_member_rows)

    return ListExternalUsersResponse(
        external_users=matched_rows,
        team_members=team_member_rows,
    )


@router.post("/{organization_id}/{provider}/save-identity-mappings")
async def save_identity_mappings(
    organization_id: str,
    provider: str,
    body: SaveIdentityMappingsRequest,
    auth: AuthContext = Depends(get_current_auth),
) -> SaveIdentityMappingsResponse:
    """
    Save user-confirmed identity mappings from the setup wizard.

    Bulk-upserts into user_mappings_for_identity with match_source='setup_wizard'.
    """
    _ensure_org_path_matches_auth(organization_id, auth)
    org_uuid: UUID
    try:
        org_uuid = UUID(organization_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid organization ID")

    saved_count: int = 0

    async with get_session(organization_id=organization_id) as session:
        for entry in body.mappings:
            user_uuid: UUID
            try:
                user_uuid = UUID(entry.user_id)
            except ValueError:
                continue

            # Check if mapping already exists for this (org, external_id, source)
            existing = await session.execute(
                select(ExternalIdentityMapping).where(
                    ExternalIdentityMapping.organization_id == org_uuid,
                    ExternalIdentityMapping.external_userid == entry.external_id,
                    ExternalIdentityMapping.source == entry.source,
                ).limit(1)
            )
            mapping: ExternalIdentityMapping | None = existing.scalar_one_or_none()

            if mapping:
                mapping.user_id = user_uuid
                mapping.match_source = "setup_wizard"
            else:
                session.add(ExternalIdentityMapping(
                    id=uuid_mod.uuid4(),
                    organization_id=org_uuid,
                    user_id=user_uuid,
                    external_userid=entry.external_id,
                    source=entry.source,
                    match_source="setup_wizard",
                ))
            saved_count += 1

        await session.commit()

    return SaveIdentityMappingsResponse(saved=saved_count)


# Legacy endpoint for backwards compatibility
@router.post("/{organization_id}")
async def trigger_sync_legacy(
    organization_id: str,
    auth: AuthContext = Depends(get_current_auth),
) -> SyncAllResponse:
    """Legacy endpoint - triggers sync for all integrations (same auth as /all)."""
    await _ensure_org_admin_can_sync_all(organization_id, auth)
    return await _execute_sync_all_integrations(organization_id)
