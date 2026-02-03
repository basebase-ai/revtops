"""
Workflow management API endpoints.

Provides CRUD operations for user-defined workflow automations.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, and_

from models.database import get_session
from models.workflow import Workflow, WorkflowRun

router = APIRouter()


# ============================================================================
# Request/Response Models
# ============================================================================


class TriggerConfig(BaseModel):
    """Trigger configuration model."""

    cron: Optional[str] = None  # For schedule triggers: "0 8 * * *"
    event: Optional[str] = None  # For event triggers: "sync.completed"
    filter: Optional[dict[str, Any]] = None  # Optional event filter


class WorkflowStep(BaseModel):
    """Workflow step definition."""

    action: str  # 'query', 'llm', 'send_email', 'send_slack', 'sync'
    params: dict[str, Any] = {}


class CreateWorkflowRequest(BaseModel):
    """Request model for creating a workflow."""

    name: str
    description: Optional[str] = None
    trigger_type: str  # 'schedule', 'event', 'manual'
    trigger_config: TriggerConfig
    steps: list[WorkflowStep] = []  # Optional for prompt-based workflows
    prompt: Optional[str] = None  # Agent prompt for prompt-based workflows
    auto_approve_tools: list[str] = []  # Tools that run without approval
    output_config: Optional[dict[str, Any]] = None
    is_enabled: bool = True


class UpdateWorkflowRequest(BaseModel):
    """Request model for updating a workflow."""

    name: Optional[str] = None
    description: Optional[str] = None
    trigger_type: Optional[str] = None
    trigger_config: Optional[TriggerConfig] = None
    steps: Optional[list[WorkflowStep]] = None
    prompt: Optional[str] = None
    auto_approve_tools: Optional[list[str]] = None
    output_config: Optional[dict[str, Any]] = None
    is_enabled: Optional[bool] = None


class WorkflowResponse(BaseModel):
    """Response model for a workflow."""

    id: str
    organization_id: str
    created_by_user_id: str
    name: str
    description: Optional[str]
    trigger_type: str
    trigger_config: dict[str, Any]
    steps: list[dict[str, Any]]
    prompt: Optional[str]  # New: Agent prompt for prompt-based workflows
    auto_approve_tools: list[str]  # New: Tools that run without approval
    output_config: Optional[dict[str, Any]]
    is_enabled: bool
    last_run_at: Optional[str]
    last_error: Optional[str]
    created_at: str
    updated_at: str


class WorkflowRunResponse(BaseModel):
    """Response model for a workflow run."""

    id: str
    workflow_id: str
    triggered_by: str
    status: str
    steps_completed: Optional[list[dict[str, Any]]]
    error_message: Optional[str]
    started_at: str
    completed_at: Optional[str]
    duration_ms: Optional[int]


class WorkflowListResponse(BaseModel):
    """Response model for listing workflows."""

    workflows: list[WorkflowResponse]
    total: int


class TriggerWorkflowResponse(BaseModel):
    """Response model for triggering a workflow."""

    status: str
    task_id: str
    workflow_id: str


# ============================================================================
# Endpoints
# ============================================================================


@router.get("/{organization_id}", response_model=WorkflowListResponse)
async def list_workflows(
    organization_id: str,
    enabled_only: bool = False,
) -> WorkflowListResponse:
    """List all workflows for an organization."""
    try:
        org_uuid = UUID(organization_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid organization ID")

    async with get_session(organization_id=organization_id) as session:
        query = select(Workflow).where(Workflow.organization_id == org_uuid)
        if enabled_only:
            query = query.where(Workflow.is_enabled == True)
        query = query.order_by(Workflow.created_at.desc())

        result = await session.execute(query)
        workflows = result.scalars().all()

        return WorkflowListResponse(
            workflows=[WorkflowResponse(**w.to_dict()) for w in workflows],
            total=len(workflows),
        )


@router.get("/{organization_id}/{workflow_id}", response_model=WorkflowResponse)
async def get_workflow(organization_id: str, workflow_id: str) -> WorkflowResponse:
    """Get a specific workflow."""
    try:
        org_uuid = UUID(organization_id)
        wf_uuid = UUID(workflow_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ID format")

    async with get_session(organization_id=organization_id) as session:
        result = await session.execute(
            select(Workflow).where(
                and_(
                    Workflow.id == wf_uuid,
                    Workflow.organization_id == org_uuid,
                )
            )
        )
        workflow = result.scalar_one_or_none()

        if not workflow:
            raise HTTPException(status_code=404, detail="Workflow not found")

        return WorkflowResponse(**workflow.to_dict())


@router.post("/{organization_id}", response_model=WorkflowResponse)
async def create_workflow(
    organization_id: str,
    user_id: str,  # TODO: Get from auth context
    request: CreateWorkflowRequest,
) -> WorkflowResponse:
    """Create a new workflow."""
    try:
        org_uuid = UUID(organization_id)
        user_uuid = UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ID format")

    # Validate trigger type
    if request.trigger_type not in ("schedule", "event", "manual"):
        raise HTTPException(
            status_code=400,
            detail="trigger_type must be 'schedule', 'event', or 'manual'",
        )

    # Validate trigger config
    if request.trigger_type == "schedule" and not request.trigger_config.cron:
        raise HTTPException(
            status_code=400,
            detail="Schedule triggers require a cron expression",
        )
    if request.trigger_type == "event" and not request.trigger_config.event:
        raise HTTPException(
            status_code=400,
            detail="Event triggers require an event type",
        )

    # Validate cron expression if provided
    if request.trigger_config.cron:
        try:
            from croniter import croniter
            croniter(request.trigger_config.cron)
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid cron expression: {e}",
            )

    async with get_session(organization_id=organization_id) as session:
        workflow = Workflow(
            organization_id=org_uuid,
            created_by_user_id=user_uuid,
            name=request.name,
            description=request.description,
            trigger_type=request.trigger_type,
            trigger_config=request.trigger_config.model_dump(exclude_none=True),
            steps=[s.model_dump() for s in request.steps],
            prompt=request.prompt,
            auto_approve_tools=request.auto_approve_tools,
            output_config=request.output_config,
            is_enabled=request.is_enabled,
        )
        session.add(workflow)
        await session.commit()
        await session.refresh(workflow)

        return WorkflowResponse(**workflow.to_dict())


@router.patch("/{organization_id}/{workflow_id}", response_model=WorkflowResponse)
async def update_workflow(
    organization_id: str,
    workflow_id: str,
    request: UpdateWorkflowRequest,
) -> WorkflowResponse:
    """Update a workflow."""
    try:
        org_uuid = UUID(organization_id)
        wf_uuid = UUID(workflow_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ID format")

    async with get_session(organization_id=organization_id) as session:
        result = await session.execute(
            select(Workflow).where(
                and_(
                    Workflow.id == wf_uuid,
                    Workflow.organization_id == org_uuid,
                )
            )
        )
        workflow = result.scalar_one_or_none()

        if not workflow:
            raise HTTPException(status_code=404, detail="Workflow not found")

        # Update fields
        if request.name is not None:
            workflow.name = request.name
        if request.description is not None:
            workflow.description = request.description
        if request.trigger_type is not None:
            workflow.trigger_type = request.trigger_type
        if request.trigger_config is not None:
            workflow.trigger_config = request.trigger_config.model_dump(exclude_none=True)
        if request.steps is not None:
            workflow.steps = [s.model_dump() for s in request.steps]
        if request.prompt is not None:
            workflow.prompt = request.prompt
        if request.auto_approve_tools is not None:
            workflow.auto_approve_tools = request.auto_approve_tools
        if request.output_config is not None:
            workflow.output_config = request.output_config
        if request.is_enabled is not None:
            workflow.is_enabled = request.is_enabled

        workflow.updated_at = datetime.utcnow()
        await session.commit()
        await session.refresh(workflow)

        return WorkflowResponse(**workflow.to_dict())


@router.delete("/{organization_id}/{workflow_id}")
async def delete_workflow(organization_id: str, workflow_id: str) -> dict[str, str]:
    """Delete a workflow."""
    try:
        org_uuid = UUID(organization_id)
        wf_uuid = UUID(workflow_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ID format")

    async with get_session(organization_id=organization_id) as session:
        result = await session.execute(
            select(Workflow).where(
                and_(
                    Workflow.id == wf_uuid,
                    Workflow.organization_id == org_uuid,
                )
            )
        )
        workflow = result.scalar_one_or_none()

        if not workflow:
            raise HTTPException(status_code=404, detail="Workflow not found")

        await session.delete(workflow)
        await session.commit()

        return {"status": "deleted", "workflow_id": workflow_id}


class TriggerWorkflowResponseV2(BaseModel):
    """Response model for triggering a workflow (v2 with conversation)."""
    status: str
    task_id: str
    workflow_id: str
    conversation_id: Optional[str] = None  # New: conversation to navigate to


@router.post("/{organization_id}/{workflow_id}/trigger", response_model=TriggerWorkflowResponseV2)
async def trigger_workflow(
    organization_id: str,
    workflow_id: str,
) -> TriggerWorkflowResponseV2:
    """Manually trigger a workflow execution."""
    from models.conversation import Conversation
    
    try:
        org_uuid = UUID(organization_id)
        wf_uuid = UUID(workflow_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ID format")

    async with get_session(organization_id=organization_id) as session:
        result = await session.execute(
            select(Workflow).where(
                and_(
                    Workflow.id == wf_uuid,
                    Workflow.organization_id == org_uuid,
                )
            )
        )
        workflow = result.scalar_one_or_none()

        if not workflow:
            raise HTTPException(status_code=404, detail="Workflow not found")

        if not workflow.is_enabled:
            raise HTTPException(status_code=400, detail="Workflow is disabled")
        
        # For prompt-based workflows, create conversation upfront so we can return its ID
        conversation_id: str | None = None
        if workflow.prompt and workflow.prompt.strip():
            conversation = Conversation(
                user_id=workflow.created_by_user_id,
                organization_id=workflow.organization_id,
                type="workflow",
                workflow_id=workflow.id,
                title=f"Workflow: {workflow.name}",
            )
            session.add(conversation)
            await session.commit()
            await session.refresh(conversation)
            conversation_id = str(conversation.id)

    # Queue execution via Celery
    from workers.tasks.workflows import execute_workflow
    task = execute_workflow.delay(workflow_id, "manual", None, conversation_id, organization_id)

    return TriggerWorkflowResponseV2(
        status="queued",
        task_id=task.id,
        workflow_id=workflow_id,
        conversation_id=conversation_id,
    )


@router.get("/{organization_id}/{workflow_id}/runs", response_model=list[WorkflowRunResponse])
async def list_workflow_runs(
    organization_id: str,
    workflow_id: str,
    limit: int = 20,
) -> list[WorkflowRunResponse]:
    """List recent runs for a workflow."""
    try:
        org_uuid = UUID(organization_id)
        wf_uuid = UUID(workflow_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ID format")

    async with get_session(organization_id=organization_id) as session:
        result = await session.execute(
            select(WorkflowRun)
            .where(
                and_(
                    WorkflowRun.workflow_id == wf_uuid,
                    WorkflowRun.organization_id == org_uuid,
                )
            )
            .order_by(WorkflowRun.started_at.desc())
            .limit(limit)
        )
        runs = result.scalars().all()

        return [WorkflowRunResponse(**r.to_dict()) for r in runs]
