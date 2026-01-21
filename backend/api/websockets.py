"""
WebSocket handler for chat interface.

Responsibilities:
- Accept WebSocket connections
- Stream messages from user to Claude
- Stream Claude responses back to user
- Handle tool calls during conversation
- Save conversation history
"""

from datetime import datetime
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
        
        if user.organization_id is None:
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
