"""
WebSocket handler for chat interface.

Responsibilities:
- Accept WebSocket connections
- Stream messages from user to Claude
- Stream Claude responses back to user
- Handle tool calls during conversation
- Save conversation history
- Support conversation-based chat
"""

import json
from datetime import datetime
from fastapi import WebSocket, WebSocketDisconnect
from uuid import UUID

from agents.orchestrator import ChatOrchestrator
from models.database import get_session
from models.user import User


async def websocket_endpoint(websocket: WebSocket, user_id: str) -> None:
    """
    WebSocket endpoint for chat communication.

    Protocol:
    - Client sends JSON: {"message": "...", "conversation_id": "..." (optional)}
    - Server streams text chunks back
    - Server sends JSON for metadata: {"type": "conversation_created", "conversation_id": "..."}

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

        # Auto-create user if they don't exist (came from Supabase Auth)
        if not user:
            user = User(
                id=user_uuid,
                email=f"user-{user_id[:8]}@placeholder.local",  # Placeholder, updated on sync
                name=None,
                last_login=datetime.utcnow(),
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            print(f"Auto-created user {user_uuid} for WebSocket connection")

        organization_id = str(user.organization_id) if user.organization_id else None

        try:
            while True:
                # Receive message from client
                raw_message = await websocket.receive_text()

                # Parse message - support both plain text and JSON
                conversation_id: str | None = None
                user_message: str

                try:
                    data = json.loads(raw_message)
                    user_message = data.get("message", raw_message)
                    conversation_id = data.get("conversation_id")
                except json.JSONDecodeError:
                    # Plain text message (backwards compatibility)
                    user_message = raw_message

                # Create orchestrator with conversation context
                orchestrator = ChatOrchestrator(
                    user_id=str(user.id),
                    organization_id=organization_id,
                    conversation_id=conversation_id,
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
