"""
WebSocket handler for chat interface.

Responsibilities:
- Accept WebSocket connections
- Manage subscriptions to background agent tasks
- Send active task state on connect for catchup
- Handle CRM operation approvals
- Stream task updates to subscribed clients
- Broadcast sync progress events to clients

Architecture:
- WebSocket is a subscription mechanism, not the driver of agent processes
- Agent tasks run as background asyncio tasks managed by TaskManager
- Tasks persist to database and continue even if client disconnects
- Clients can reconnect and catch up on missed updates
"""

import json
import logging
from collections import defaultdict
from typing import Dict, Set
from uuid import UUID, uuid4

from fastapi import WebSocket, WebSocketDisconnect


# =============================================================================
# Sync Progress Broadcasting
# =============================================================================

class SyncProgressBroadcaster:
    """
    Manages WebSocket connections for broadcasting sync progress events.
    
    Clients are grouped by organization_id so we only send events to
    users who belong to that organization.
    """
    
    def __init__(self) -> None:
        # org_id -> set of websockets
        self._connections: Dict[str, Set[WebSocket]] = defaultdict(set)
    
    def register(self, organization_id: str, websocket: WebSocket) -> None:
        """Register a websocket for sync progress updates."""
        self._connections[organization_id].add(websocket)
    
    def unregister(self, organization_id: str, websocket: WebSocket) -> None:
        """Unregister a websocket."""
        self._connections[organization_id].discard(websocket)
        if not self._connections[organization_id]:
            del self._connections[organization_id]
    
    async def broadcast(
        self,
        organization_id: str,
        event_type: str,
        data: dict,
    ) -> None:
        """Broadcast an event to all connected clients for an organization."""
        websockets = self._connections.get(organization_id, set())
        if not websockets:
            return
        
        message = json.dumps({
            "type": event_type,
            **data,
        })
        
        # Send to all, collect any dead connections
        dead: Set[WebSocket] = set()
        for ws in websockets:
            try:
                await ws.send_text(message)
            except Exception:
                dead.add(ws)
        
        # Clean up dead connections
        for ws in dead:
            self._connections[organization_id].discard(ws)


# Global broadcaster instance
sync_broadcaster = SyncProgressBroadcaster()


async def broadcast_sync_progress(
    organization_id: str,
    provider: str,
    count: int,
    status: str = "syncing",
) -> None:
    """
    Broadcast sync progress to all connected clients for an organization.
    
    Called from connectors during sync to update the UI in real-time.
    
    Args:
        organization_id: The organization UUID
        provider: The provider name (e.g., "google_calendar")
        count: Current count of synced items
        status: "syncing" or "completed"
    """
    await sync_broadcaster.broadcast(
        organization_id=organization_id,
        event_type="sync_progress",
        data={
            "provider": provider,
            "count": count,
            "status": status,
        },
    )


async def broadcast_tool_progress(
    organization_id: str,
    conversation_id: str,
    tool_id: str,
    tool_name: str,
    result: dict,
    status: str = "running",
) -> None:
    """
    Broadcast tool progress to all connected clients for an organization.
    
    Called from tools during execution to update the UI with progress.
    
    Args:
        organization_id: The organization UUID
        conversation_id: The conversation containing the tool call
        tool_id: The tool_use block ID
        tool_name: Name of the tool (e.g., "create_artifact")
        result: Progress result dict
        status: "running" for progress, "complete" when done
    """
    await sync_broadcaster.broadcast(
        organization_id=organization_id,
        event_type="tool_progress",
        data={
            "conversation_id": conversation_id,
            "tool_id": tool_id,
            "tool_name": tool_name,
            "result": result,
            "status": status,
        },
    )


# =============================================================================
# Chat WebSocket Handler
# =============================================================================

from agents.orchestrator import ChatOrchestrator
from agents.tools import (
    execute_crm_operation, 
    cancel_crm_operation, 
    update_tool_call_result,
    execute_send_email_from,
    execute_send_slack,
)
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


async def _execute_tool_approval(
    operation_id: str,
    approved: bool,
    options: dict,
    organization_id: str | None,
    user_id: str,
) -> dict:
    """
    Execute or cancel a pending tool approval.
    
    Routes to the appropriate execution function based on the tool type.
    
    Args:
        operation_id: The pending operation ID
        approved: Whether the user approved the operation
        options: Tool-specific options (e.g., skip_duplicates for CRM)
        organization_id: Organization UUID
        user_id: User UUID
        
    Returns:
        Execution result dict with status, message, etc.
    """
    from models.pending_operation import PendingOperation, CrmOperation
    from models.database import get_session
    from agents.tools import (
        get_pending_operation,
        remove_pending_operation,
        execute_send_email_from,
        execute_send_slack,
    )
    
    # First check if this is in our in-memory pending operations store
    pending_op = get_pending_operation(operation_id)
    
    if pending_op:
        tool_name = pending_op["tool_name"]
        params = pending_op["params"]
        op_org_id = pending_op["organization_id"]
        op_user_id = pending_op["user_id"]
        
        # Remove from pending store
        remove_pending_operation(operation_id)
        
        if not approved:
            return {
                "status": "canceled",
                "message": "Operation canceled by user",
                "tool_name": tool_name,
            }
        
        # Execute based on tool type
        if tool_name == "send_email_from":
            result = await execute_send_email_from(params, op_org_id, op_user_id)
            result["tool_name"] = tool_name
            return result
        elif tool_name == "send_slack":
            result = await execute_send_slack(params, op_org_id)
            result["tool_name"] = tool_name
            return result
        else:
            return {
                "status": "failed",
                "error": f"Unknown tool type: {tool_name}",
                "tool_name": tool_name,
            }
    
    # Check if this is a CRM operation (stored in database)
    async with get_session() as session:
        crm_op = await session.get(CrmOperation, UUID(operation_id))
        
        if crm_op:
            # It's a CRM operation
            skip_duplicates = options.get("skip_duplicates", True)
            if approved:
                result = await execute_crm_operation(operation_id, skip_duplicates)
            else:
                result = await cancel_crm_operation(operation_id)
            result["tool_name"] = "crm_write"
            return result
    
    # Operation not found
    return {
        "status": "failed",
        "error": f"Pending operation {operation_id} not found. It may have expired or already been processed.",
        "tool_name": "unknown",
    }


async def websocket_endpoint(websocket: WebSocket) -> None:
    """
    WebSocket endpoint for chat communication.
    
    SECURITY: Authentication is done via JWT token passed as query parameter.
    The token is verified before accepting the connection.

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
    """
    # Verify JWT token BEFORE accepting the connection
    from api.auth_middleware import verify_websocket_token
    
    try:
        auth = await verify_websocket_token(websocket)
    except Exception as e:
        # verify_websocket_token already closed the WebSocket with appropriate code
        logger.warning(f"WebSocket auth failed: {e}")
        return
    
    await websocket.accept()
    
    # User is authenticated - extract values from verified auth context
    user_id_str = auth.user_id_str
    organization_id = auth.organization_id_str
    user_email = auth.email
    
    # Check user status (already done in auth middleware, but double-check waitlist)
    if auth.role == "waitlist":
        await websocket.close(code=1008, reason="You're on the waitlist. We'll notify you when you have access.")
        return

    try:
        # Register for sync progress broadcasts
        if organization_id:
            sync_broadcaster.register(organization_id, websocket)
        
        # Send active tasks on connect for client catchup
        active_tasks = await task_manager.get_active_tasks(user_id_str, organization_id)
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
                    
                    # Generate UUID upfront - SQLAlchemy default isn't populated until flush
                    conv_uuid = uuid4()
                    
                    async with get_session(organization_id=organization_id) as session:
                        conversation = Conversation(
                            id=conv_uuid,
                            user_id=UUID(user_id_str), 
                            organization_id=UUID(organization_id) if organization_id else None,
                            title=title,
                        )
                        session.add(conversation)
                        await session.commit()

                    conversation_id = str(conv_uuid)

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
                    user_email=user_email,
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

            # Handle tool approval messages (generic for all tools)
            elif message_type == "tool_approval":
                operation_id = data.get("operation_id")
                approved = data.get("approved", False)
                options = data.get("options", {})
                tool_conversation_id = data.get("conversation_id")

                if not operation_id:
                    await websocket.send_text(json.dumps({
                        "type": "tool_approval_result",
                        "status": "error",
                        "error": "Missing operation_id",
                    }))
                    continue

                # Execute the appropriate tool based on stored operation
                result = await _execute_tool_approval(
                    operation_id=operation_id,
                    approved=approved,
                    options=options,
                    organization_id=organization_id,
                    user_id=user_id_str,
                )

                await update_tool_call_result(operation_id, {
                    "type": "tool_approval_result",
                    "status": result.get("status", "unknown"),
                    "operation_id": operation_id,
                    **result,
                })

                await websocket.send_text(json.dumps({
                    "type": "tool_approval_result",
                    "operation_id": operation_id,
                    **result,
                }))

                # If operation failed, start a task for the agent to handle the error
                if result.get("status") == "failed" and result.get("error") and tool_conversation_id and organization_id:
                    tool_name = result.get("tool_name", "tool")
                    error_feedback = (
                        f"[{tool_name} Operation Failed] The operation you requested was approved "
                        f"but failed with this error:\n\n{result.get('error')}\n\n"
                        f"Please analyze the error, explain what went wrong to the user, "
                        f"and offer to retry with corrected parameters."
                    )

                    task_id = await task_manager.start_task(
                        conversation_id=tool_conversation_id,
                        user_id=user_id_str,
                        organization_id=organization_id,
                        user_message=error_feedback,
                    )
                    await task_manager.subscribe(task_id, websocket)

                    await websocket.send_text(json.dumps({
                        "type": "task_started",
                        "task_id": task_id,
                        "conversation_id": tool_conversation_id,
                    }))

            # Legacy: Handle CRM approval messages (for backward compatibility)
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
        logger.info("User %s disconnected", user_id_str)
    finally:
        # Clean up subscriptions
        await task_manager.unsubscribe_all(websocket)
        # Unregister from sync progress broadcasts
        if organization_id:
            sync_broadcaster.unregister(organization_id, websocket)