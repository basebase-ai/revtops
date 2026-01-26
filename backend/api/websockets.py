"""
WebSocket handler for chat interface.

Responsibilities:
- Accept WebSocket connections
- Manage subscriptions to background agent tasks
- Send active task state on connect for catchup
- Handle CRM operation approvals
- Stream task updates to subscribed clients

Architecture:
- WebSocket is a subscription mechanism, not the driver of agent processes
- Agent tasks run as background asyncio tasks managed by TaskManager
- Tasks persist to database and continue even if client disconnects
- Clients can reconnect and catch up on missed updates
"""

import json
import logging
from uuid import UUID

from fastapi import WebSocket, WebSocketDisconnect

from agents.orchestrator import ChatOrchestrator
from agents.tools import execute_crm_operation, cancel_crm_operation, update_tool_call_result
from models.conversation import Conversation
from models.database import get_session
from models.user import User
from services.task_manager import task_manager


def _generate_title(message: str) -> str:
    """Generate a conversation title from the first message."""
    cleaned = message.strip().replace("\n", " ")
    words = cleaned.split()[:8]
    title = " ".join(words)
    if len(title) > 40:
        title = title[:40]
    if len(cleaned) > len(title):
        title += "..."
    return title or "New Chat"

logger = logging.getLogger(__name__)


async def websocket_endpoint(websocket: WebSocket, user_id: str) -> None:
    """
    WebSocket endpoint for chat communication.

    Protocol (client -> server):
    - {"type": "send_message", "message": "...", "conversation_id": "..."}
    - {"type": "subscribe", "task_id": "...", "since_index": 0}
    - {"type": "cancel", "task_id": "..."}
    - {"type": "crm_approval", "operation_id": "...", "approved": true/false}

    Protocol (server -> client):
    - {"type": "active_tasks", "tasks": [...]} - sent on connect
    - {"type": "task_started", "task_id": "...", "conversation_id": "..."}
    - {"type": "task_chunk", "task_id": "...", "chunk": {...}}
    - {"type": "task_complete", "task_id": "...", "status": "..."}
    - {"type": "catchup", "task_id": "...", "chunks": [...]}
    - {"type": "crm_approval_result", ...}

    Args:
        websocket: The WebSocket connection
        user_id: UUID of the authenticated user
    """
    await websocket.accept()

    try:
        user_uuid = UUID(user_id)
    except ValueError:
        await websocket.close(code=1008, reason="Invalid user ID format")
        return

    async with get_session() as session:
        user = await session.get(User, user_uuid)

        if not user:
            await websocket.close(code=1008, reason="User not found. Please sign in first.")
            return

        if user.status == "waitlist":
            await websocket.close(code=1008, reason="You're on the waitlist. We'll notify you when you have access.")
            return
        if user.status == "crm_only":
            await websocket.close(code=1008, reason="Please sign up to use Revtops.")
            return

        organization_id = str(user.organization_id) if user.organization_id else None
        user_id_str = str(user.id)

    try:
        # Send active tasks on connect for client catchup
        active_tasks = await task_manager.get_active_tasks(user_id_str)
        await websocket.send_text(json.dumps({
            "type": "active_tasks",
            "tasks": active_tasks,
        }))

        # Auto-subscribe to all active tasks
        for task in active_tasks:
            await task_manager.subscribe(task["id"], websocket)
        while True:
            raw_message = await websocket.receive_text()

            try:
                data = json.loads(raw_message)
            except json.JSONDecodeError:
                # Legacy: plain text treated as send_message
                data = {"type": "send_message", "message": raw_message}

            message_type = data.get("type", "send_message")

            # Handle send_message - start a new background task
            if message_type == "send_message" or message_type == "chat":
                user_message = data.get("message")
                conversation_id = data.get("conversation_id")
                local_time = data.get("local_time")
                timezone = data.get("timezone")

                if not user_message:
                    continue

                # Create conversation if needed
                if not conversation_id:
                    # Generate title from first message
                    title = _generate_title(user_message)
                    
                    async with get_session() as session:
                        conversation = Conversation(user_id=UUID(user_id_str), title=title)
                        session.add(conversation)
                        await session.commit()
                        await session.refresh(conversation)
                        conversation_id = str(conversation.id)

                    await websocket.send_text(json.dumps({
                        "type": "conversation_created",
                        "conversation_id": conversation_id,
                        "title": title,
                    }))

                if not organization_id:
                    await websocket.send_text(json.dumps({
                        "type": "error",
                        "error": "No organization found. Please complete onboarding.",
                    }))
                    continue

                # Start background task
                task_id = await task_manager.start_task(
                    conversation_id=conversation_id,
                    user_id=user_id_str,
                    organization_id=organization_id,
                    user_message=user_message,
                    local_time=local_time,
                    timezone=timezone,
                )

                # Subscribe this websocket to the task
                await task_manager.subscribe(task_id, websocket)

                # Notify client that task started
                await websocket.send_text(json.dumps({
                    "type": "task_started",
                    "task_id": task_id,
                    "conversation_id": conversation_id,
                }))

            # Handle subscribe - client wants to subscribe to a task (e.g., after reconnect)
            elif message_type == "subscribe":
                task_id = data.get("task_id")
                since_index = data.get("since_index", 0)

                if not task_id:
                    continue

                await task_manager.subscribe(task_id, websocket)

                # Send catchup chunks
                chunks = await task_manager.get_task_chunks(task_id, since_index)
                task = await task_manager.get_task(task_id)

                await websocket.send_text(json.dumps({
                    "type": "catchup",
                    "task_id": task_id,
                    "chunks": chunks,
                    "task_status": task.get("status") if task else "unknown",
                }))

            # Handle cancel - cancel a running task
            elif message_type == "cancel":
                task_id = data.get("task_id")
                if task_id:
                    cancelled = await task_manager.cancel_task(task_id)
                    await websocket.send_text(json.dumps({
                        "type": "task_cancelled",
                        "task_id": task_id,
                        "success": cancelled,
                    }))

            # Handle CRM approval messages
            elif message_type == "crm_approval":
                operation_id = data.get("operation_id")
                approved = data.get("approved", False)
                skip_duplicates = data.get("skip_duplicates", True)
                crm_conversation_id = data.get("conversation_id")

                if not operation_id:
                    await websocket.send_text(json.dumps({
                        "type": "crm_approval_result",
                        "status": "error",
                        "error": "Missing operation_id",
                    }))
                    continue

                if approved:
                    result = await execute_crm_operation(operation_id, skip_duplicates)
                else:
                    result = await cancel_crm_operation(operation_id)

                await update_tool_call_result(operation_id, {
                    "type": "crm_approval_result",
                    "status": result.get("status", "unknown"),
                    "operation_id": operation_id,
                    **result,
                })

                await websocket.send_text(json.dumps({
                    "type": "crm_approval_result",
                    "operation_id": operation_id,
                    **result,
                }))

                # If operation failed, start a task for the agent to handle the error
                if result.get("status") == "failed" and result.get("error") and crm_conversation_id and organization_id:
                    error_feedback = (
                        f"[CRM Operation Failed] The operation you requested was approved "
                        f"but failed with this error:\n\n{result.get('error')}\n\n"
                        f"Please analyze the error, explain what went wrong to the user, "
                        f"and offer to retry with corrected parameters."
                    )

                    task_id = await task_manager.start_task(
                        conversation_id=crm_conversation_id,
                        user_id=user_id_str,
                        organization_id=organization_id,
                        user_message=error_feedback,
                    )
                    await task_manager.subscribe(task_id, websocket)

                    await websocket.send_text(json.dumps({
                        "type": "task_started",
                        "task_id": task_id,
                        "conversation_id": crm_conversation_id,
                    }))

    except WebSocketDisconnect:
        logger.info("User %s disconnected", user_id)
    finally:
        # Clean up subscriptions
        await task_manager.unsubscribe_all(websocket)
