"""
Workflow tasks for Celery workers.

These tasks handle:
- Checking for scheduled workflows that need to run
- Processing event-triggered workflows
- Executing workflow steps
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure backend directory is in Python path for Celery forked workers
_backend_dir = Path(__file__).resolve().parent.parent.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

import asyncio
import json
import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from workers.celery_app import celery_app

logger = logging.getLogger(__name__)


# =============================================================================
# Schema Validation and Parameter Formatting
# =============================================================================

def validate_workflow_input(
    input_data: dict[str, Any] | None,
    input_schema: dict[str, Any] | None,
) -> tuple[bool, str | None]:
    """
    Validate input data against the workflow's input schema.
    
    Returns:
        (is_valid, error_message) - error_message is None if valid
    """
    if input_schema is None:
        # No schema defined = accept anything
        return True, None
    
    if input_data is None:
        input_data = {}
    
    try:
        import jsonschema
        jsonschema.validate(instance=input_data, schema=input_schema)
        return True, None
    except jsonschema.ValidationError as e:
        return False, f"Input validation failed: {e.message}"
    except jsonschema.SchemaError as e:
        logger.error(f"Invalid input_schema: {e}")
        return True, None  # Don't fail on bad schema, just skip validation


def format_typed_parameters(
    input_data: dict[str, Any] | None,
    input_schema: dict[str, Any] | None,
) -> str | None:
    """
    Format input data as typed parameters for injection into the prompt.
    
    Returns a formatted string like:
    
    Input parameters:
    - email (string, required): "john@acme.com"
    - company_domain (string, optional): "acme.com"
    - contact_id (uuid, optional): not provided
    
    Returns None if no schema is defined.
    """
    if input_schema is None:
        return None
    
    if input_data is None:
        input_data = {}
    
    properties: dict[str, Any] = input_schema.get("properties", {})
    required: list[str] = input_schema.get("required", [])
    
    if not properties:
        return None
    
    lines: list[str] = ["Input parameters:"]
    
    for prop_name, prop_schema in properties.items():
        prop_type = prop_schema.get("type", "any")
        prop_format = prop_schema.get("format")
        is_required = prop_name in required
        
        # Build type string
        type_str = prop_type
        if prop_format:
            type_str = prop_format
        
        req_str = "required" if is_required else "optional"
        
        # Get value
        if prop_name in input_data:
            value = input_data[prop_name]
            if isinstance(value, str):
                value_str = f'"{value}"'
            else:
                value_str = json.dumps(value)
        else:
            value_str = "not provided"
        
        lines.append(f"- {prop_name} ({type_str}, {req_str}): {value_str}")
    
    return "\n".join(lines)


async def resolve_child_workflows(
    child_workflow_ids: list[str],
    organization_id: str,
) -> list[dict[str, Any]]:
    """
    Resolve child workflow IDs to full metadata for prompt injection.
    
    Returns a list of workflow info dicts with id, name, description, 
    input_schema, and output_schema.
    """
    if not child_workflow_ids:
        return []
    
    from sqlalchemy import select
    from models.database import get_session
    from models.workflow import Workflow
    
    resolved: list[dict[str, Any]] = []
    
    async with get_session(organization_id=organization_id) as session:
        for wf_id in child_workflow_ids:
            try:
                result = await session.execute(
                    select(Workflow).where(Workflow.id == UUID(wf_id))
                )
                workflow = result.scalar_one_or_none()
                
                if workflow and workflow.is_enabled:
                    resolved.append({
                        "id": str(workflow.id),
                        "name": workflow.name,
                        "description": workflow.description,
                        "input_schema": workflow.input_schema,
                        "output_schema": workflow.output_schema,
                    })
            except Exception as e:
                logger.warning(f"Failed to resolve child workflow {wf_id}: {e}")
    
    return resolved


def format_child_workflows_for_prompt(child_workflows: list[dict[str, Any]]) -> str | None:
    """
    Format resolved child workflows as instructions for the agent.
    
    Returns a string like:
    
    Available child workflows (use with run_workflow or loop_over):
    
    1. "Enrich Single Contact" (id: 9645564e-...)
       Input: {email: string (required), first_name: string}
       Output: {enriched: boolean, company_name: string}
    """
    if not child_workflows:
        return None
    
    lines: list[str] = [
        "Available child workflows (use with run_workflow or loop_over):",
        "",
    ]
    
    for i, wf in enumerate(child_workflows, 1):
        lines.append(f'{i}. "{wf["name"]}" (id: {wf["id"]})')
        
        if wf.get("description"):
            lines.append(f'   Description: {wf["description"]}')
        
        # Format input schema
        input_schema = wf.get("input_schema")
        if input_schema and input_schema.get("properties"):
            props = input_schema["properties"]
            required = input_schema.get("required", [])
            prop_strs: list[str] = []
            for prop_name, prop_def in props.items():
                prop_type = prop_def.get("type", "any")
                req_str = " (required)" if prop_name in required else ""
                prop_strs.append(f"{prop_name}: {prop_type}{req_str}")
            lines.append(f'   Input: {{{", ".join(prop_strs)}}}')
        else:
            lines.append("   Input: any object")
        
        # Format output schema
        output_schema = wf.get("output_schema")
        if output_schema and output_schema.get("properties"):
            props = output_schema["properties"]
            prop_strs = [f"{k}: {v.get('type', 'any')}" for k, v in props.items()]
            lines.append(f'   Output: {{{", ".join(prop_strs)}}}')
        else:
            lines.append("   Output: string")
        
        lines.append("")  # Blank line between workflows
    
    return "\n".join(lines)


def format_output_schema_instruction(output_schema: dict[str, Any] | None) -> str | None:
    """
    Format output schema as instructions for the agent.

    Returns a string like:

    Expected output format (JSON):
    {
      "enriched": boolean,
      "company_name": string,
      "linkedin_url": string
    }

    Returns None if no schema is defined.
    """
    if output_schema is None:
        return None

    # Simple case: primitive type
    schema_type = output_schema.get("type")
    if schema_type in ("string", "number", "boolean", "integer"):
        return f"Expected output: Return a {schema_type} value."

    # Object type: format properties
    if schema_type == "object":
        properties = output_schema.get("properties", {})
        if not properties:
            return "Expected output: Return a JSON object."

        lines: list[str] = ["Expected output format (return as JSON):"]
        lines.append("{")

        prop_lines: list[str] = []
        for prop_name, prop_schema in properties.items():
            prop_type = prop_schema.get("type", "any")
            prop_desc = prop_schema.get("description", "")
            desc_str = f"  // {prop_desc}" if prop_desc else ""
            prop_lines.append(f'  "{prop_name}": {prop_type}{desc_str}')

        lines.append(",\n".join(prop_lines))
        lines.append("}")
        return "\n".join(lines)

    # Array or complex type: just show the schema
    return f"Expected output format:\n```json\n{json.dumps(output_schema, indent=2)}\n```"


def compute_effective_auto_approve_tools(
    workflow_auto_approve_tools: list[str] | None,
    parent_auto_approve_tools: list[str] | None,
) -> list[str]:
    """
    Compute effective auto-approve tools for a workflow run.

    Security invariant:
    - A child workflow can never gain tool permissions its parent did not have.
    - Root workflows (no parent restrictions) keep their configured permissions.

    Returns:
        Ordered list of effective tool names.
    """
    configured_tools: list[str] = list(workflow_auto_approve_tools or [])
    inherited_tools: list[str] | None = parent_auto_approve_tools

    # Root invocation: no parent restrictions to intersect with.
    if inherited_tools is None:
        return configured_tools

    inherited_set = set(inherited_tools)
    return [tool for tool in configured_tools if tool in inherited_set]


def extract_structured_output(response_text: str) -> dict[str, Any] | None:
    """
    Extract structured JSON output from agent response text.
    
    Looks for JSON in the following order:
    1. ```json ... ``` code blocks (last one takes precedence)
    2. Bare {...} at the end of the response
    
    Returns the parsed dict or None if no valid JSON found.
    """
    import re
    
    if not response_text:
        return None
    
    # 1. Look for ```json ... ``` blocks (take the last one)
    json_block_pattern = r'```json\s*([\s\S]*?)\s*```'
    matches = re.findall(json_block_pattern, response_text)
    
    if matches:
        # Try the last match first (most likely to be the final output)
        for match in reversed(matches):
            try:
                parsed = json.loads(match.strip())
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                continue
    
    # 2. Look for bare JSON object at end of response
    # Find the last {...} in the text
    brace_pattern = r'\{[^{}]*\}'
    brace_matches = re.findall(brace_pattern, response_text)
    
    if brace_matches:
        # Try the last match
        try:
            parsed = json.loads(brace_matches[-1])
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    
    # 3. Try nested JSON objects (more complex patterns)
    # Look for JSON that might span multiple lines at the end
    last_brace = response_text.rfind('}')
    if last_brace != -1:
        # Find matching opening brace
        depth = 0
        start_idx = -1
        for i in range(last_brace, -1, -1):
            if response_text[i] == '}':
                depth += 1
            elif response_text[i] == '{':
                depth -= 1
                if depth == 0:
                    start_idx = i
                    break
        
        if start_idx != -1:
            try:
                json_str = response_text[start_idx:last_brace + 1]
                parsed = json.loads(json_str)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass
    
    return None


def run_async(coro: Any) -> Any:
    """Run an async function in a sync context (for Celery tasks).
    
    Creates a fresh event loop and disposes any existing database connections
    to avoid 'Future attached to different loop' errors with asyncpg.
    """
    from models.database import dispose_engine
    
    # Dispose existing connections - they're tied to a previous (closed) event loop
    # and will cause "Future attached to different loop" errors if reused
    dispose_engine()
    
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
    from models.database import get_admin_session
    from models.workflow import Workflow
    from croniter import croniter
    
    now = datetime.utcnow()
    triggered: list[str] = []
    
    # Admin session: iterates across ALL organizations' workflows
    async with get_admin_session() as session:
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
                    execute_workflow.delay(
                        str(workflow.id), 
                        "schedule", 
                        None, 
                        None, 
                        str(workflow.organization_id)
                    )
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
    from models.database import get_admin_session
    from models.workflow import Workflow
    from workers.events import get_pending_events
    
    events = await get_pending_events(limit=100)
    if not events:
        return {"events_processed": 0, "workflows_triggered": []}
    
    triggered: list[dict[str, str]] = []
    
    # Admin session: iterates across events from ALL organizations
    async with get_admin_session() as session:
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
                        None,  # conversation_id
                        org_id,  # organization_id for RLS
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
    conversation_id: str | None = None,
    organization_id: str | None = None,
) -> dict[str, Any]:
    """
    Execute a workflow by creating a conversation and sending the prompt to the agent.
    
    NEW ARCHITECTURE (Phase 5): Workflows are "scheduled prompts to the agent".
    Instead of a rigid step-by-step execution engine, workflows:
    1. Create a conversation (type='workflow') - or use pre-created one
    2. Send the workflow prompt to the agent
    3. Agent uses tools to accomplish the task
    4. If agent hits an approval-required tool, conversation pauses
    5. Full execution is visible as a chat conversation
    
    For backward compatibility, workflows without a prompt fall back to legacy execution.
    """
    from sqlalchemy import select
    from models.database import get_session
    from models.conversation import Conversation
    from models.workflow import Workflow, WorkflowRun
    
    started_at = datetime.utcnow()
    
    async with get_session(organization_id=organization_id) as session:
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
        
        # Check if this workflow uses the new prompt-based execution
        if workflow.prompt and workflow.prompt.strip():
            # NEW: Execute via agent conversation
            try:
                result = await _execute_workflow_via_agent(
                    workflow=workflow,
                    run=run,
                    triggered_by=triggered_by,
                    trigger_data=trigger_data,
                    session=session,
                    existing_conversation_id=conversation_id,
                )
                return result
            except Exception as e:
                error_msg = str(e)
                logger.error(f"Workflow {workflow_id} agent execution failed: {error_msg}")
                
                run.status = "failed"
                run.error_message = error_msg
                run.completed_at = datetime.utcnow()
                await session.commit()
                
                return {
                    "status": "failed",
                    "workflow_id": workflow_id,
                    "run_id": str(run_id),
                    "error": error_msg,
                }
        
        # LEGACY: Fall back to step-by-step execution for workflows without prompts
        return await _execute_workflow_legacy(workflow, run, trigger_data, session)


async def _execute_workflow_via_agent(
    workflow: Any,
    run: Any,
    triggered_by: str,
    trigger_data: dict[str, Any] | None,
    session: Any,
    existing_conversation_id: str | None = None,
) -> dict[str, Any]:
    """
    Execute a workflow by sending its prompt to the agent.
    
    This creates a conversation visible in the chat UI, allowing users
    to see exactly what the agent did and intervene if needed.
    """
    from sqlalchemy import select
    from agents.orchestrator import ChatOrchestrator
    from models.conversation import Conversation
    
    # Extract parent_conversation_id from trigger_data if this is a child workflow
    parent_conversation_id: UUID | None = None
    if trigger_data and "_parent_context" in trigger_data:
        parent_context = trigger_data["_parent_context"]
        parent_conv_id_str = parent_context.get("parent_conversation_id")
        if parent_conv_id_str:
            try:
                parent_conversation_id = UUID(parent_conv_id_str)
            except ValueError:
                logger.warning(f"Invalid parent_conversation_id: {parent_conv_id_str}")
    
    # Use existing conversation or create a new one
    if existing_conversation_id:
        # Load the pre-created conversation
        result = await session.execute(
            select(Conversation).where(Conversation.id == UUID(existing_conversation_id))
        )
        conversation = result.scalar_one_or_none()
        if not conversation:
            # Fallback to creating new if not found
            conversation = Conversation(
                user_id=workflow.created_by_user_id,
                organization_id=workflow.organization_id,
                type="workflow",
                workflow_id=workflow.id,
                title=f"Workflow: {workflow.name}",
                parent_conversation_id=parent_conversation_id,
            )
            session.add(conversation)
            await session.flush()
    else:
        # Create a new workflow conversation
        conversation = Conversation(
            user_id=workflow.created_by_user_id,
            organization_id=workflow.organization_id,
            type="workflow",
            workflow_id=workflow.id,
            title=f"Workflow: {workflow.name}",
            parent_conversation_id=parent_conversation_id,
        )
        session.add(conversation)
        await session.flush()

    # Set root_conversation_id so change sessions group all CRM changes from this run (parent + children)
    if conversation.parent_conversation_id:
        parent_result = await session.execute(
            select(Conversation).where(Conversation.id == conversation.parent_conversation_id)
        )
        parent_conv = parent_result.scalar_one_or_none()
        if parent_conv:
            conversation.root_conversation_id = (
                parent_conv.root_conversation_id or parent_conv.id
            )
        else:
            conversation.root_conversation_id = conversation.parent_conversation_id
    else:
        conversation.root_conversation_id = conversation.id

    # IMPORTANT: Commit the conversation so the orchestrator's separate session can see it
    # The orchestrator uses its own sessions for saving messages, which won't see uncommitted data
    await session.commit()
    
    # Set conversation_id in output early so UI can link to it while running
    run.output = {"conversation_id": str(conversation.id)}
    await session.commit()
    
    logger.info(
        f"[Workflow] Starting agent execution for workflow {workflow.id} "
        f"conversation {conversation.id}"
    )
    
    # Extract user-facing trigger data (filter out internal context and None values)
    user_trigger_data: dict[str, Any] | None = None
    if trigger_data:
        user_trigger_data = {
            k: v for k, v in trigger_data.items() 
            if not k.startswith("_") and v is not None  # Exclude internal keys and None values
        }
    
    # Validate input against schema if defined
    input_schema: dict[str, Any] | None = getattr(workflow, "input_schema", None)
    output_schema: dict[str, Any] | None = getattr(workflow, "output_schema", None)
    
    if input_schema is not None:
        is_valid, error_msg = validate_workflow_input(user_trigger_data, input_schema)
        if not is_valid:
            run.status = "failed"
            run.error_message = error_msg
            run.completed_at = datetime.utcnow()
            await session.commit()
            return {
                "status": "failed",
                "workflow_id": str(workflow.id),
                "run_id": str(run.id),
                "error": error_msg,
            }
    
    # Build the prompt with typed parameters or raw trigger data
    prompt = workflow.prompt
    
    # If schema is defined, inject typed parameters; otherwise use raw trigger data
    typed_params = format_typed_parameters(user_trigger_data, input_schema)
    if typed_params:
        prompt += f"\n\n{typed_params}"
    elif user_trigger_data:
        prompt += f"\n\nTrigger data: {user_trigger_data}"
    
    # Add output format instructions if schema is defined
    output_instruction = format_output_schema_instruction(output_schema)
    if output_instruction:
        prompt += f"\n\n{output_instruction}"
    
    # Resolve and inject child workflows
    child_workflow_ids: list[str] = getattr(workflow, "child_workflows", []) or []
    if child_workflow_ids:
        resolved_children = await resolve_child_workflows(
            child_workflow_ids, 
            str(workflow.organization_id)
        )
        child_workflows_text = format_child_workflows_for_prompt(resolved_children)
        if child_workflows_text:
            prompt += f"\n\n{child_workflows_text}"
    
    # Extract call_stack from parent context for recursion detection
    call_stack: list[str] = []
    parent_auto_approve_tools: list[str] | None = None
    if trigger_data and "_parent_context" in trigger_data:
        parent_context = trigger_data["_parent_context"]
        call_stack = parent_context.get("call_stack", [])
        parent_auto_approve_tools = parent_context.get("auto_approve_tools")

    effective_auto_approve_tools = compute_effective_auto_approve_tools(
        workflow_auto_approve_tools=workflow.auto_approve_tools,
        parent_auto_approve_tools=parent_auto_approve_tools,
    )
    if parent_auto_approve_tools is not None:
        logger.info(
            "[Workflow] Auto-approve restriction applied for child workflow %s: configured=%s inherited=%s effective=%s",
            workflow.id,
            workflow.auto_approve_tools or [],
            parent_auto_approve_tools,
            effective_auto_approve_tools,
        )
    
    # Create orchestrator with workflow context for auto-approvals
    workflow_context: dict[str, Any] = {
        "is_workflow": True,
        "workflow_id": str(workflow.id),
        "auto_approve_tools": effective_auto_approve_tools,
        "call_stack": call_stack,  # For nested workflow recursion detection
    }
    
    orchestrator = ChatOrchestrator(
        user_id=str(workflow.created_by_user_id),
        organization_id=str(workflow.organization_id),
        conversation_id=str(conversation.id),
        workflow_context=workflow_context,
    )
    
    # Process the prompt (this streams through the agent)
    # Since we're in a background worker, we consume the generator fully
    response_text = ""
    async for chunk in orchestrator.process_message(prompt, save_user_message=True):
        # Collect text chunks (JSON chunks are tool events)
        if not chunk.startswith("{"):
            response_text += chunk
    
    # Extract structured output from response if output_schema is defined
    structured_output: dict[str, Any] | None = None
    if output_schema is not None:
        structured_output = extract_structured_output(response_text)
        if structured_output:
            logger.info(f"[Workflow] Extracted structured output: {structured_output}")
    
    # Update run record
    run.status = "completed"
    run.output = {
        "conversation_id": str(conversation.id),
        "response_preview": response_text[:500] if response_text else None,
        "structured_output": structured_output,
    }
    run.completed_at = datetime.utcnow()
    
    # Update workflow last_run_at
    workflow.last_run_at = datetime.utcnow()
    
    await session.commit()
    
    logger.info(f"[Workflow] Completed workflow {workflow.id} via agent")
    
    return {
        "status": "completed",
        "workflow_id": str(workflow.id),
        "run_id": str(run.id),
        "conversation_id": str(conversation.id),
        "execution_type": "agent",
        "structured_output": structured_output,
    }


async def _execute_workflow_legacy(
    workflow: Any,
    run: Any,
    trigger_data: dict[str, Any] | None,
    session: Any,
) -> dict[str, Any]:
    """
    Legacy step-by-step workflow execution.
    
    This is kept for backward compatibility with existing workflows
    that use the steps[] array instead of the new prompt field.
    """
    steps_completed: list[dict[str, Any]] = []
    
    try:
        # Execute each step
        context: dict[str, Any] = {
            "trigger_data": trigger_data or {},
            "organization_id": str(workflow.organization_id),
            "workflow_id": str(workflow.id),
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
        
        logger.info(f"[Workflow] Completed legacy workflow {workflow.id}")
        return {
            "status": "completed",
            "workflow_id": str(workflow.id),
            "run_id": str(run.id),
            "steps_completed": len(steps_completed),
            "execution_type": "legacy",
        }
        
    except Exception as e:
        error_msg = str(e)
        logger.error(f"[Workflow] Legacy workflow {workflow.id} failed: {error_msg}")
        
        run.status = "failed"
        run.error_message = error_msg
        run.steps_completed = steps_completed
        run.completed_at = datetime.utcnow()
        
        await session.commit()
        
        return {
            "status": "failed",
            "workflow_id": str(workflow.id),
            "run_id": str(run.id),
            "error": error_msg,
            "steps_completed": len(steps_completed),
            "execution_type": "legacy",
        }


async def _execute_step(
    step: dict[str, Any],
    context: dict[str, Any],
    workflow: Any,
) -> dict[str, Any]:
    """
    Execute a single workflow step.
    
    DEPRECATED: This is the legacy step-by-step execution engine.
    New workflows should use the prompt field for agent-based execution.
    This function is kept for backward compatibility with existing workflows.
    
    Supported actions:
    - query: Query data from the database
    - llm: Call an LLM for processing
    - send_email: Send an email notification
    - send_slack: Post to Slack
    - sync: Trigger a data sync
    """
    import warnings
    warnings.warn(
        "Legacy workflow step execution is deprecated. "
        "Use prompt-based workflows for new automations.",
        DeprecationWarning,
    )
    action = step.get("action")
    params = step.get("params", {})
    
    logger.info(f"Executing step: {action}")
    
    try:
        if action == "query":
            return await _action_query(params, context)
        elif action == "run_query":
            return await _action_run_query(params, context)
        elif action == "llm":
            return await _action_llm(params, context)
        elif action == "send_email":
            return await _action_send_email(params, context, workflow)
        elif action == "send_system_email":
            return await _action_send_system_email(params, context)
        elif action == "send_email_from":
            return await _action_send_email_from(params, context, workflow)
        elif action == "send_slack":
            return await _action_send_slack(params, context, workflow)
        elif action == "send_system_sms":
            return await _action_send_system_sms(params, context)
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
    """Execute a data query (legacy - use run_query instead)."""
    query = params.get("query", "")
    return {
        "status": "completed",
        "action": "query",
        "query": query,
        "results": [],
    }


async def _action_run_query(
    params: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """
    Execute a SQL query against the organization's data.
    
    Security:
    - Only SELECT statements allowed
    - org_id is always injected and available as :org_id
    - Query timeout enforced
    """
    from sqlalchemy import text
    from models.database import get_session
    
    sql = params.get("sql", "")
    org_id = context.get("organization_id")
    
    if not sql.strip():
        return {
            "status": "failed",
            "error": "No SQL query provided",
        }
    
    # Security: Only allow SELECT statements
    sql_upper = sql.strip().upper()
    if not sql_upper.startswith("SELECT"):
        return {
            "status": "failed",
            "error": "Only SELECT queries are allowed",
        }
    
    # Block dangerous keywords even in SELECT
    dangerous_keywords = ["INSERT", "UPDATE", "DELETE", "DROP", "TRUNCATE", "ALTER", "CREATE", "GRANT", "REVOKE"]
    for keyword in dangerous_keywords:
        if keyword in sql_upper:
            return {
                "status": "failed",
                "error": f"Query contains disallowed keyword: {keyword}",
            }
    
    try:
        async with get_session(organization_id=org_id) as session:
            # Execute with org_id parameter always available
            result = await session.execute(
                text(sql),
                {"org_id": org_id}
            )
            
            # Convert rows to list of dicts
            rows = [dict(row._mapping) for row in result.fetchall()]
            
            # Serialize UUIDs and datetimes for JSON
            serialized_rows: list[dict[str, Any]] = []
            for row in rows:
                serialized_row: dict[str, Any] = {}
                for key, value in row.items():
                    if hasattr(value, "isoformat"):  # datetime
                        serialized_row[key] = value.isoformat()
                    elif hasattr(value, "hex"):  # UUID
                        serialized_row[key] = str(value)
                    else:
                        serialized_row[key] = value
                serialized_rows.append(serialized_row)
        
        logger.info(f"Query returned {len(serialized_rows)} rows")
        return {
            "status": "completed",
            "action": "run_query",
            "row_count": len(serialized_rows),
            "data": serialized_rows,
        }
        
    except Exception as e:
        logger.error(f"Query failed: {e}")
        return {
            "status": "failed",
            "error": str(e),
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
    """
    Post a message to Slack.
    
    Params:
        channel: Channel name (e.g., "#general") or ID
        message: Message text to post
        thread_ts: Optional - reply in thread
    
    Uses the organization's Slack integration.
    """
    from sqlalchemy import select, and_
    from models.database import get_session
    from models.integration import Integration
    from connectors.slack import SlackConnector
    
    channel = params.get("channel", "")
    message = params.get("message", "")
    thread_ts = params.get("thread_ts")
    org_id = context.get("organization_id", "")
    
    if not channel:
        return {
            "status": "failed",
            "error": "No channel specified",
        }
    
    if not message:
        return {
            "status": "failed",
            "error": "No message specified",
        }
    
    # Substitute context variables in message
    for key, value in context.items():
        if isinstance(value, str):
            message = message.replace(f"{{{key}}}", value)
    
    # Handle {previous_output} or {step_N_output} references
    for key, value in context.items():
        if key.startswith("step_") and key.endswith("_output"):
            if isinstance(value, dict):
                # If it's an LLM output, get the text
                output_text = value.get("output", str(value))
                message = message.replace(f"{{{key}}}", str(output_text))
                # Also support {previous_output} as alias for last step
                message = message.replace("{previous_output}", str(output_text))
    
    # Note: SlackConnector.post_message auto-converts markdown to mrkdwn
    
    try:
        # Get the Slack integration for this org
        async with get_session(organization_id=org_id) as session:
            result = await session.execute(
                select(Integration).where(
                    and_(
                        Integration.organization_id == UUID(org_id),
                        Integration.provider == "slack",
                        Integration.is_active == True,
                    )
                )
            )
            integration = result.scalar_one_or_none()
            
            if not integration:
                return {
                    "status": "failed",
                    "error": "No active Slack integration found for organization",
                }
        
        # Create connector and post message
        connector = SlackConnector(
            organization_id=org_id,
            nango_connection_id=integration.nango_connection_id,
        )
        
        result = await connector.post_message(
            channel=channel,
            text=message,
            thread_ts=thread_ts,
        )
        
        logger.info(f"Posted to Slack channel {channel}: {result.get('ts')}")
        return {
            "status": "completed",
            "action": "send_slack",
            "channel": channel,
            "ts": result.get("ts"),
            "message_preview": message[:100],
        }
        
    except Exception as e:
        logger.error(f"Failed to post to Slack: {e}")
        return {
            "status": "failed",
            "error": str(e),
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


async def _action_send_system_sms(
    params: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """
    Send an SMS via Twilio (system account).
    
    Params:
        to: Phone number in E.164 format (e.g., +14155551234)
        body: Message text
    """
    from services.sms import send_sms
    
    to = params.get("to", "")
    body = params.get("body", "")
    
    if not to:
        return {
            "status": "failed",
            "error": "No phone number specified",
        }
    
    if not body:
        return {
            "status": "failed",
            "error": "No message body specified",
        }
    
    # Substitute context variables
    for key, value in context.items():
        if isinstance(value, str):
            body = body.replace(f"{{{key}}}", value)
        elif isinstance(value, dict) and "output" in value:
            body = body.replace(f"{{{key}}}", str(value.get("output", "")))
    
    result = await send_sms(to, body)
    
    if result.get("success"):
        return {
            "status": "completed",
            "action": "send_system_sms",
            "to": to,
            "message_sid": result.get("message_sid"),
        }
    else:
        return {
            "status": "failed",
            "error": result.get("error", "Unknown SMS error"),
        }


async def _action_send_system_email(
    params: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """
    Send an email via Resend (system account).
    
    Params:
        to: Recipient email address(es)
        subject: Email subject
        body: Email body text
        reply_to: Optional reply-to address
    """
    from services.email import send_email
    
    to = params.get("to", "")
    subject = params.get("subject", "Revtops Notification")
    body = params.get("body", "")
    reply_to = params.get("reply_to")
    
    if not to:
        return {
            "status": "failed",
            "error": "No recipient specified",
        }
    
    if not body:
        return {
            "status": "failed",
            "error": "No email body specified",
        }
    
    # Substitute context variables
    for key, value in context.items():
        if isinstance(value, str):
            subject = subject.replace(f"{{{key}}}", value)
            body = body.replace(f"{{{key}}}", value)
        elif isinstance(value, dict) and "output" in value:
            output_str = str(value.get("output", ""))
            subject = subject.replace(f"{{{key}}}", output_str)
            body = body.replace(f"{{{key}}}", output_str)
    
    success = await send_email(to, subject, body, reply_to=reply_to)
    
    return {
        "status": "completed" if success else "failed",
        "action": "send_system_email",
        "to": to,
        "subject": subject,
    }


async def _action_send_email_from(
    params: dict[str, Any],
    context: dict[str, Any],
    workflow: Any,
) -> dict[str, Any]:
    """
    Send an email from a user's connected email account (Gmail or Outlook).
    
    Params:
        provider: "gmail" or "microsoft_mail"
        user_id: Optional - specific user's connection to use (defaults to workflow creator)
        to: Recipient email address(es)
        subject: Email subject
        body: Email body text
        cc: Optional CC recipients
        bcc: Optional BCC recipients
    """
    from sqlalchemy import select, and_
    from models.database import get_session
    from models.integration import Integration
    from connectors.gmail import GmailConnector
    from connectors.microsoft_mail import MicrosoftMailConnector
    
    provider = params.get("provider", "gmail")
    user_id = params.get("user_id")
    to = params.get("to", "")
    subject = params.get("subject", "")
    body = params.get("body", "")
    cc = params.get("cc", [])
    bcc = params.get("bcc", [])
    
    org_id = context.get("organization_id", "")
    
    if not to:
        return {
            "status": "failed",
            "error": "No recipient specified",
        }
    
    if not body:
        return {
            "status": "failed",
            "error": "No email body specified",
        }
    
    if provider not in ("gmail", "microsoft_mail"):
        return {
            "status": "failed",
            "error": f"Unsupported email provider: {provider}. Use 'gmail' or 'microsoft_mail'.",
        }
    
    # Substitute context variables
    for key, value in context.items():
        if isinstance(value, str):
            subject = subject.replace(f"{{{key}}}", value)
            body = body.replace(f"{{{key}}}", value)
        elif isinstance(value, dict) and "output" in value:
            output_str = str(value.get("output", ""))
            subject = subject.replace(f"{{{key}}}", output_str)
            body = body.replace(f"{{{key}}}", output_str)
    
    try:
        async with get_session(organization_id=org_id) as session:
            # Find the user's email integration
            query = select(Integration).where(
                and_(
                    Integration.organization_id == UUID(org_id),
                    Integration.provider == provider,
                    Integration.is_active == True,
                )
            )
            
            # If user_id specified, filter to that user
            if user_id:
                query = query.where(Integration.user_id == UUID(user_id))
            else:
                # Default to workflow creator's connection
                query = query.where(Integration.user_id == workflow.created_by_user_id)
            
            result = await session.execute(query)
            integration = result.scalar_one_or_none()
            
            if not integration:
                return {
                    "status": "failed",
                    "error": f"No active {provider} integration found for user",
                }
        
        # Create appropriate connector and send
        if provider == "gmail":
            connector = GmailConnector(
                organization_id=org_id,
                nango_connection_id=integration.nango_connection_id,
            )
        else:
            connector = MicrosoftMailConnector(
                organization_id=org_id,
                nango_connection_id=integration.nango_connection_id,
            )
        
        result = await connector.send_email(
            to=to,
            subject=subject,
            body=body,
            cc=cc if cc else None,
            bcc=bcc if bcc else None,
        )
        
        if result.get("success"):
            logger.info(f"Sent email via {provider} to {to}")
            return {
                "status": "completed",
                "action": "send_email_from",
                "provider": provider,
                "to": to,
                "subject": subject,
            }
        else:
            return {
                "status": "failed",
                "error": result.get("error", "Failed to send email"),
            }
            
    except Exception as e:
        logger.error(f"Failed to send email via {provider}: {e}")
        return {
            "status": "failed",
            "error": str(e),
        }


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
    conversation_id: str | None = None,
    organization_id: str | None = None,
) -> dict[str, Any]:
    """
    Celery task to execute a workflow.
    
    Args:
        workflow_id: UUID of the workflow to execute
        triggered_by: What triggered this execution (e.g., 'schedule', 'event:sync.completed')
        trigger_data: Optional data from the trigger event
        conversation_id: Optional pre-created conversation ID (for immediate navigation)
        organization_id: Organization ID for RLS context (required for proper security)
    
    Returns:
        Execution result with status and any errors
    """
    logger.info(f"Task {self.request.id}: Executing workflow {workflow_id}")
    return run_async(_execute_workflow(workflow_id, triggered_by, trigger_data, conversation_id, organization_id))
