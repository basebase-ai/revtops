"""
WebSocket handler for chat interface.

Responsibilities:
- Accept WebSocket connections
- Stream messages from user to Claude
- Stream Claude responses back to user
- Handle tool calls during conversation
- Save conversation history
"""

from fastapi import WebSocket, WebSocketDisconnect
from uuid import UUID

from agents.orchestrator import ChatOrchestrator
from models.database import get_session
from models.user import User


async def websocket_endpoint(websocket: WebSocket, user_id: str) -> None:
    """
    WebSocket endpoint for chat communication.

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
        
        # For MVP: if user doesn't exist in our DB yet (came from Supabase Auth),
        # we still allow the connection but with limited functionality
        if not user:
            # Create a temporary orchestrator without organization context
            # In production, this should sync with Supabase or require proper onboarding
            orchestrator = ChatOrchestrator(
                user_id=str(user_uuid), organization_id=None
            )
        elif user.organization_id is None:
            # User exists but has no organization yet
            orchestrator = ChatOrchestrator(
                user_id=str(user.id), organization_id=None
            )
        else:
            orchestrator = ChatOrchestrator(
                user_id=str(user.id), organization_id=str(user.organization_id)
            )

        try:
            while True:
                # Receive user message
                user_message = await websocket.receive_text()

                # Stream Claude's response
                async for chunk in orchestrator.process_message(user_message):
                    await websocket.send_text(chunk)

        except WebSocketDisconnect:
            print(f"User {user_id} disconnected")
