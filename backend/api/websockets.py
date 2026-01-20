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
        if not user:
            await websocket.close(code=1008, reason="User not found")
            return

        if user.customer_id is None:
            await websocket.close(code=1008, reason="User has no associated customer")
            return

        orchestrator = ChatOrchestrator(
            user_id=str(user.id), customer_id=str(user.customer_id)
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
