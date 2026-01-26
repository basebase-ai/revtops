"""
Workflow tasks for Celery workers.

These tasks handle:
- Checking for scheduled workflows that need to run
- Processing event-triggered workflows
- Executing workflow steps
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from workers.celery_app import celery_app

logger = logging.getLogger(__name__)


def run_async(coro: Any) -> Any:
    """Run an async function in a sync context (for Celery tasks)."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _check_scheduled_workflows() -> dict[str, Any]:
    """
    Check for workflows scheduled to run now.
    
    Queries the database for enabled workflows with schedule triggers
    that are due to run.
    """
    from sqlalchemy import select, and_
    from models.database import get_session
    from models.workflow import Workflow
    from croniter import croniter
    
    now = datetime.utcnow()
    triggered: list[str] = []
    
    async with get_session() as session:
        # Get all enabled scheduled workflows
        result = await session.execute(
            select(Workflow).where(
                and_(
                    Workflow.is_enabled == True,
                    Workflow.trigger_type == "schedule",
                )
            )
        )
        workflows = result.scalars().all()
        
        for workflow in workflows:
            try:
                # Check if workflow should run based on cron schedule
                cron_expr = workflow.trigger_config.get("cron")
                if not cron_expr:
                    continue
                
                # Get the last scheduled run time
                cron = croniter(cron_expr, workflow.last_run_at or workflow.created_at)
                next_run = cron.get_next(datetime)
                
                if next_run <= now:
                    # Queue workflow for execution
                    execute_workflow.delay(str(workflow.id), "schedule")
                    triggered.append(str(workflow.id))
                    
                    # Update last_run_at
                    workflow.last_run_at = now
                    
            except Exception as e:
                logger.error(f"Error checking workflow {workflow.id}: {e}")
        
        if triggered:
            await session.commit()
    
    return {
        "checked_at": now.isoformat(),
        "workflows_triggered": triggered,
    }


async def _process_pending_events() -> dict[str, Any]:
    """
    Process pending events and trigger matching workflows.
    """
    from sqlalchemy import select, and_
    from models.database import get_session
    from models.workflow import Workflow
    from workers.events import get_pending_events
    
    events = await get_pending_events(limit=100)
    if not events:
        return {"events_processed": 0, "workflows_triggered": []}
    
    triggered: list[dict[str, str]] = []
    
    async with get_session() as session:
        for event in events:
            event_type = event["type"]
            org_id = event["organization_id"]
            
            # Find workflows triggered by this event type
            result = await session.execute(
                select(Workflow).where(
                    and_(
                        Workflow.organization_id == UUID(org_id),
                        Workflow.is_enabled == True,
                        Workflow.trigger_type == "event",
                    )
                )
            )
            workflows = result.scalars().all()
            
            for workflow in workflows:
                trigger_event = workflow.trigger_config.get("event")
                if trigger_event == event_type:
                    # Queue workflow for execution with event data
                    execute_workflow.delay(
                        str(workflow.id),
                        f"event:{event_type}",
                        event["data"],
                    )
                    triggered.append({
                        "workflow_id": str(workflow.id),
                        "event_type": event_type,
                    })
    
    return {
        "events_processed": len(events),
        "workflows_triggered": triggered,
    }


async def _execute_workflow(
    workflow_id: str,
    triggered_by: str,
    trigger_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Execute a workflow and its steps.
    
    This is the main workflow execution engine that:
    1. Loads the workflow definition
    2. Creates a WorkflowRun record
    3. Executes each step in sequence
    4. Records results and handles errors
    """
    from sqlalchemy import select
    from models.database import get_session
    from models.workflow import Workflow, WorkflowRun
    
    started_at = datetime.utcnow()
    steps_completed: list[dict[str, Any]] = []
    
    async with get_session() as session:
        # Load workflow
        result = await session.execute(
            select(Workflow).where(Workflow.id == UUID(workflow_id))
        )
        workflow = result.scalar_one_or_none()
        
        if not workflow:
            return {
                "status": "failed",
                "error": f"Workflow {workflow_id} not found",
            }
        
        if not workflow.is_enabled:
            return {
                "status": "skipped",
                "reason": "Workflow is disabled",
            }
        
        # Create run record
        run = WorkflowRun(
            workflow_id=workflow.id,
            organization_id=workflow.organization_id,
            triggered_by=triggered_by,
            trigger_data=trigger_data,
            status="running",
            started_at=started_at,
        )
        session.add(run)
        await session.flush()
        run_id = run.id
        
        try:
            # Execute each step
            context: dict[str, Any] = {
                "trigger_data": trigger_data or {},
                "organization_id": str(workflow.organization_id),
                "workflow_id": workflow_id,
            }
            
            for i, step in enumerate(workflow.steps):
                step_result = await _execute_step(step, context, workflow)
                steps_completed.append({
                    "step_index": i,
                    "action": step.get("action"),
                    "result": step_result,
                })
                
                # Pass step output to next step
                context[f"step_{i}_output"] = step_result
                
                if step_result.get("status") == "failed":
                    raise Exception(step_result.get("error", "Step failed"))
            
            # Update run as completed
            run.status = "completed"
            run.steps_completed = steps_completed
            run.completed_at = datetime.utcnow()
            
            # Update workflow last_run_at
            workflow.last_run_at = datetime.utcnow()
            
            await session.commit()
            
            logger.info(f"Workflow {workflow_id} completed successfully")
            return {
                "status": "completed",
                "workflow_id": workflow_id,
                "run_id": str(run_id),
                "steps_completed": len(steps_completed),
            }
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Workflow {workflow_id} failed: {error_msg}")
            
            run.status = "failed"
            run.error_message = error_msg
            run.steps_completed = steps_completed
            run.completed_at = datetime.utcnow()
            
            await session.commit()
            
            return {
                "status": "failed",
                "workflow_id": workflow_id,
                "run_id": str(run_id),
                "error": error_msg,
                "steps_completed": len(steps_completed),
            }


async def _execute_step(
    step: dict[str, Any],
    context: dict[str, Any],
    workflow: Any,
) -> dict[str, Any]:
    """
    Execute a single workflow step.
    
    Supported actions:
    - query: Query data from the database
    - llm: Call an LLM for processing
    - send_email: Send an email notification
    - send_slack: Post to Slack
    - sync: Trigger a data sync
    """
    action = step.get("action")
    params = step.get("params", {})
    
    logger.info(f"Executing step: {action}")
    
    try:
        if action == "query":
            return await _action_query(params, context)
        elif action == "llm":
            return await _action_llm(params, context)
        elif action == "send_email":
            return await _action_send_email(params, context, workflow)
        elif action == "send_slack":
            return await _action_send_slack(params, context, workflow)
        elif action == "sync":
            return await _action_sync(params, context)
        else:
            return {
                "status": "failed",
                "error": f"Unknown action: {action}",
            }
    except Exception as e:
        return {
            "status": "failed",
            "error": str(e),
        }


async def _action_query(
    params: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Execute a data query."""
    # TODO: Implement semantic search or SQL query
    query = params.get("query", "")
    return {
        "status": "completed",
        "action": "query",
        "query": query,
        "results": [],  # Placeholder
    }


async def _action_llm(
    params: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Call an LLM for processing."""
    import anthropic
    from config import settings
    
    prompt = params.get("prompt", "")
    model = params.get("model", "claude-sonnet-4-20250514")
    
    # Substitute context variables in prompt
    for key, value in context.items():
        prompt = prompt.replace(f"{{{key}}}", str(value))
    
    if not settings.ANTHROPIC_API_KEY:
        return {
            "status": "failed",
            "error": "ANTHROPIC_API_KEY not configured",
        }
    
    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    response = client.messages.create(
        model=model,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    
    output = response.content[0].text if response.content else ""
    
    return {
        "status": "completed",
        "action": "llm",
        "output": output,
    }


async def _action_send_email(
    params: dict[str, Any],
    context: dict[str, Any],
    workflow: Any,
) -> dict[str, Any]:
    """Send an email notification."""
    from services.email import send_email
    
    to = params.get("to", "")
    subject = params.get("subject", "Revtops Workflow Notification")
    body = params.get("body", "")
    
    # Substitute context variables
    for key, value in context.items():
        body = body.replace(f"{{{key}}}", str(value))
        subject = subject.replace(f"{{{key}}}", str(value))
    
    # Use previous step output if body references it
    if "{previous_output}" in body:
        prev_output = context.get("step_0_output", {}).get("output", "")
        body = body.replace("{previous_output}", prev_output)
    
    success = await send_email(to, subject, body)
    
    return {
        "status": "completed" if success else "failed",
        "action": "send_email",
        "to": to,
    }


async def _action_send_slack(
    params: dict[str, Any],
    context: dict[str, Any],
    workflow: Any,
) -> dict[str, Any]:
    """Post a message to Slack."""
    # TODO: Implement Slack posting via connector
    channel = params.get("channel", "")
    message = params.get("message", "")
    
    return {
        "status": "completed",
        "action": "send_slack",
        "channel": channel,
        "message": message,
    }


async def _action_sync(
    params: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Trigger a data sync."""
    from workers.tasks.sync import _sync_integration
    
    org_id = context.get("organization_id", "")
    provider = params.get("provider", "")
    
    if not provider:
        return {
            "status": "failed",
            "error": "No provider specified for sync",
        }
    
    result = await _sync_integration(org_id, provider)
    return result


@celery_app.task(bind=True, name="workers.tasks.workflows.check_scheduled_workflows")
def check_scheduled_workflows(self: Any) -> dict[str, Any]:
    """
    Celery task to check for scheduled workflows.
    
    This runs every minute via Beat to find workflows that are due to run.
    """
    return run_async(_check_scheduled_workflows())


@celery_app.task(bind=True, name="workers.tasks.workflows.process_pending_events")
def process_pending_events(self: Any) -> dict[str, Any]:
    """
    Celery task to process pending events.
    
    This runs frequently to handle event-triggered workflows.
    """
    return run_async(_process_pending_events())


@celery_app.task(bind=True, name="workers.tasks.workflows.execute_workflow")
def execute_workflow(
    self: Any,
    workflow_id: str,
    triggered_by: str,
    trigger_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Celery task to execute a workflow.
    
    Args:
        workflow_id: UUID of the workflow to execute
        triggered_by: What triggered this execution (e.g., 'schedule', 'event:sync.completed')
        trigger_data: Optional data from the trigger event
    
    Returns:
        Execution result with status and any errors
    """
    logger.info(f"Task {self.request.id}: Executing workflow {workflow_id}")
    return run_async(_execute_workflow(workflow_id, triggered_by, trigger_data))
