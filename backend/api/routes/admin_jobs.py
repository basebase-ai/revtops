"""Global admin endpoints for inspecting and cancelling running jobs."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, update

from models.agent_task import AgentTask
from models.database import get_admin_session
from models.user import User
from services.task_manager import task_manager
from workers.celery_app import celery_app

logger = logging.getLogger(__name__)
router = APIRouter()

JobType = Literal["workflow", "sync", "chat"]


class AdminRunningJob(BaseModel):
    """Single running/scheduled job shown in the admin panel."""

    id: str
    job_type: JobType
    status: Literal["active", "reserved", "scheduled", "running"]
    task_name: str
    organization_id: str | None = None
    workflow_id: str | None = None
    conversation_id: str | None = None
    user_id: str | None = None
    worker: str | None = None
    started_at: str | None = None
    eta: str | None = None
    summary: str | None = None


class AdminRunningJobsResponse(BaseModel):
    jobs: list[AdminRunningJob]
    total: int


class AdminCancelJobResponse(BaseModel):
    status: Literal["cancelled", "not_running"]
    job_type: JobType
    id: str
    detail: str


def _is_global_admin(user: User | None) -> bool:
    return bool(user and "global_admin" in (user.roles or []))


async def _assert_global_admin(user_id: str) -> None:
    try:
        user_uuid = UUID(user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid user_id") from exc

    async with get_admin_session() as session:
        user = await session.get(User, user_uuid)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        if not _is_global_admin(user):
            raise HTTPException(status_code=403, detail="Requires global_admin role")


def _parse_task_args(task_data: dict[str, Any]) -> tuple[str | None, str | None]:
    """Best effort parse of Celery inspect task payload for workflow/sync args."""
    kwargs = task_data.get("kwargs")
    if isinstance(kwargs, dict):
        return kwargs.get("workflow_id"), kwargs.get("organization_id")

    args = task_data.get("args")
    if isinstance(args, list):
        if len(args) >= 1:
            workflow_id = str(args[0])
        else:
            workflow_id = None
        organization_id = str(args[4]) if len(args) >= 5 and args[4] else None
        return workflow_id, organization_id

    return None, None


def _extract_iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        return value
    return str(value)


@router.get("/jobs", response_model=AdminRunningJobsResponse)
async def list_running_jobs(user_id: str) -> AdminRunningJobsResponse:
    """List running chat tasks and active/scheduled Celery sync + workflow jobs."""
    await _assert_global_admin(user_id)

    jobs: list[AdminRunningJob] = []

    # Chat jobs from DB + in-memory task manager state.
    async with get_admin_session() as session:
        running_chat_result = await session.execute(
            select(AgentTask)
            .where(AgentTask.status == "running")
            .order_by(AgentTask.started_at.desc())
        )
        running_chats = running_chat_result.scalars().all()

    for task in running_chats:
        jobs.append(
            AdminRunningJob(
                id=str(task.id),
                job_type="chat",
                status="running",
                task_name="chat.agent_task",
                organization_id=str(task.organization_id),
                conversation_id=str(task.conversation_id),
                user_id=str(task.user_id),
                started_at=task.started_at.isoformat(),
                summary=(task.user_message[:140] + "…") if len(task.user_message) > 140 else task.user_message,
            )
        )

    # Celery jobs for workflows and connector syncs.
    inspect = celery_app.control.inspect(timeout=1.0)
    active = inspect.active() or {}
    reserved = inspect.reserved() or {}
    scheduled = inspect.scheduled() or {}

    logger.info(
        "[admin.jobs] inspect workers active=%d reserved=%d scheduled=%d",
        len(active),
        len(reserved),
        len(scheduled),
    )

    def add_celery_jobs(source: dict[str, Any], status: Literal["active", "reserved", "scheduled"]) -> None:
        for worker_name, task_list in source.items():
            for raw_task in task_list or []:
                task_data = raw_task.get("request", raw_task)
                task_name = str(task_data.get("name") or "")
                task_id = str(task_data.get("id") or "")
                if not task_id or not task_name:
                    continue

                job_type: JobType | None = None
                if task_name == "workers.tasks.workflows.execute_workflow":
                    job_type = "workflow"
                elif task_name in {
                    "workers.tasks.sync.sync_integration",
                    "workers.tasks.sync.sync_organization",
                    "workers.tasks.sync.sync_all_organizations",
                }:
                    job_type = "sync"

                if not job_type:
                    continue

                workflow_id, organization_id = _parse_task_args(task_data)
                eta = _extract_iso(raw_task.get("eta") or task_data.get("eta"))
                started_at = _extract_iso(task_data.get("time_start") or task_data.get("time_start"))

                jobs.append(
                    AdminRunningJob(
                        id=task_id,
                        job_type=job_type,
                        status=status,
                        task_name=task_name,
                        organization_id=organization_id,
                        workflow_id=workflow_id,
                        worker=worker_name,
                        started_at=started_at,
                        eta=eta,
                        summary=str(task_data.get("argsrepr") or task_data.get("args") or ""),
                    )
                )

    add_celery_jobs(active, "active")
    add_celery_jobs(reserved, "reserved")
    add_celery_jobs(scheduled, "scheduled")

    return AdminRunningJobsResponse(jobs=jobs, total=len(jobs))


@router.post("/jobs/{job_type}/{job_id}/cancel", response_model=AdminCancelJobResponse)
async def cancel_running_job(job_type: JobType, job_id: str, user_id: str) -> AdminCancelJobResponse:
    """Cancel a running chat job or revoke a Celery workflow/sync task."""
    await _assert_global_admin(user_id)

    if job_type == "chat":
        cancelled = await task_manager.cancel_task(job_id)
        if cancelled:
            return AdminCancelJobResponse(
                status="cancelled",
                job_type=job_type,
                id=job_id,
                detail="Chat task cancelled in active API process",
            )

        # Fallback: mark DB row cancelled if still listed as running.
        async with get_admin_session() as session:
            result = await session.execute(
                update(AgentTask)
                .where(AgentTask.id == UUID(job_id))
                .where(AgentTask.status == "running")
                .values(status="cancelled", completed_at=datetime.utcnow(), last_activity_at=datetime.utcnow())
                .execution_options(synchronize_session=False)
            )
            await session.commit()

        if result.rowcount:
            return AdminCancelJobResponse(
                status="cancelled",
                job_type=job_type,
                id=job_id,
                detail="Chat task marked cancelled in database (task may already have exited)",
            )

        return AdminCancelJobResponse(
            status="not_running",
            job_type=job_type,
            id=job_id,
            detail="Chat task not found running",
        )

    celery_app.control.revoke(job_id, terminate=True, signal="SIGTERM")
    logger.info("[admin.jobs] revoked celery task id=%s type=%s", job_id, job_type)
    return AdminCancelJobResponse(
        status="cancelled",
        job_type=job_type,
        id=job_id,
        detail="Cancellation signal sent to Celery worker",
    )
