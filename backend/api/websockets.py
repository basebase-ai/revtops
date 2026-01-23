"""
WebSocket handler for chat interface.

Responsibilities:
- Accept WebSocket connections
- Stream messages from user to Claude
- Stream Claude responses back to user
- Handle tool calls during conversation
- Save conversation history
- Support conversation-based chat
- Handle CRM operation approvals
"""

import json
from datetime import datetime
from fastapi import WebSocket, WebSocketDisconnect
from uuid import UUID

from agents.orchestrator import ChatOrchestrator
from agents.tools import execute_crm_operation, cancel_crm_operation, update_tool_call_result
from models.database import get_session
from models.user import User


async def websocket_endpoint(websocket: WebSocket, user_id: str) -> None:
    """
    WebSocket endpoint for chat communication.

    Protocol:
    - Client sends JSON: {"message": "...", "conversation_id": "..." (optional)}
    - Client sends JSON for approvals: {"type": "crm_approval", "operation_id": "...", "approved": true/false}
    - Server streams text chunks back
    - Server sends JSON for metadata: {"type": "conversation_created", "conversation_id": "..."}
    - Server sends JSON for approval results: {"type": "crm_approval_result", ...}

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

        # Reject if user doesn't exist - they must go through proper auth flow first
        if not user:
            await websocket.close(code=1008, reason="User not found. Please sign in first.")
            return

        # Reject users who are on the waitlist (not yet invited)
        if user.status == "waitlist":
            await websocket.close(code=1008, reason="You're on the waitlist. We'll notify you when you have access.")
            return

        organization_id = str(user.organization_id) if user.organization_id else None

        try:
            while True:
                # Receive message from client
                raw_message = await websocket.receive_text()

                # Parse message - support both plain text and JSON
                conversation_id: str | None = None
                user_message: str | None = None
                message_type: str = "chat"

                try:
                    data = json.loads(raw_message)
                    message_type = data.get("type", "chat")
                    
                    # Handle CRM approval messages
                    if message_type == "crm_approval":
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
                            # Execute the operation
                            result = await execute_crm_operation(operation_id, skip_duplicates)
                        else:
                            # Cancel the operation
                            result = await cancel_crm_operation(operation_id)
                        
                        # Update the stored tool call result so it persists on reload
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
                        
                        # If the operation failed, automatically send the error to the agent
                        # so it can understand what went wrong and potentially retry
                        if result.get("status") == "failed" and result.get("error") and crm_conversation_id:
                            error_feedback = f"[CRM Operation Failed] The operation you requested was approved but failed with this error:\n\n{result.get('error')}\n\nPlease analyze the error, explain what went wrong to the user, and offer to retry with corrected parameters."
                            
                            # Create orchestrator with the same conversation context
                            orchestrator = ChatOrchestrator(
                                user_id=str(user.id),
                                organization_id=organization_id,
                                conversation_id=crm_conversation_id,
                            )
                            
                            # Stream the agent's response to the error
                            async for chunk in orchestrator.process_message(error_feedback):
                                await websocket.send_text(chunk)
                            
                            # Send end-of-message marker
                            await websocket.send_text(json.dumps({
                                "type": "message_complete",
                                "conversation_id": orchestrator.conversation_id,
                            }))
                        
                        continue
                    
                    # Regular chat message
                    user_message = data.get("message", raw_message)
                    conversation_id = data.get("conversation_id")
                    local_time = data.get("local_time")
                    timezone = data.get("timezone")
                    
                except json.JSONDecodeError:
                    # Plain text message (backwards compatibility)
                    user_message = raw_message
                    local_time = None
                    timezone = None

                if not user_message:
                    continue

                # Create orchestrator with conversation context
                orchestrator = ChatOrchestrator(
                    user_id=str(user.id),
                    organization_id=organization_id,
                    conversation_id=conversation_id,
                    local_time=local_time,
                    timezone=timezone,
                )

                # Stream Claude's response
                first_chunk = True
                async for chunk in orchestrator.process_message(user_message):
                    # If this is the first chunk and we created a new conversation,
                    # send the conversation ID to the client
                    if first_chunk and not conversation_id and orchestrator.conversation_id:
                        await websocket.send_text(json.dumps({
                            "type": "conversation_created",
                            "conversation_id": orchestrator.conversation_id,
                        }))
                        first_chunk = False

                    await websocket.send_text(chunk)

                # Send end-of-message marker
                await websocket.send_text(json.dumps({
                    "type": "message_complete",
                    "conversation_id": orchestrator.conversation_id,
                }))

        except WebSocketDisconnect:
            print(f"User {user_id} disconnected")
