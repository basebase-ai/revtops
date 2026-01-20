"""
Chat endpoints for REST-based interactions.

Endpoints:
- GET /api/chat/history - Get chat history for user
- POST /api/chat/message - Send a message (non-streaming alternative)
"""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from models.database import get_session
from models.chat_message import ChatMessage

router = APIRouter()


class ChatMessageResponse(BaseModel):
    """Response model for chat messages."""

    id: str
    role: str
    content: str
    created_at: str


class ChatHistoryResponse(BaseModel):
    """Response model for chat history."""

    messages: list[ChatMessageResponse]


class SendMessageRequest(BaseModel):
    """Request model for sending a message."""

    user_id: str
    content: str


class SendMessageResponse(BaseModel):
    """Response model for sent message."""

    user_message_id: str
    assistant_message_id: str
    assistant_content: str


@router.get("/history", response_model=ChatHistoryResponse)
async def get_chat_history(
    user_id: str, limit: int = 50, offset: int = 0
) -> ChatHistoryResponse:
    """Get chat history for a user."""
    try:
        user_uuid = UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID")

    async with get_session() as session:
        result = await session.execute(
            select(ChatMessage)
            .where(ChatMessage.user_id == user_uuid)
            .order_by(ChatMessage.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        messages = result.scalars().all()

        return ChatHistoryResponse(
            messages=[
                ChatMessageResponse(
                    id=str(msg.id),
                    role=msg.role,
                    content=msg.content,
                    created_at=msg.created_at.isoformat() if msg.created_at else "",
                )
                for msg in reversed(messages)
            ]
        )


@router.post("/message", response_model=SendMessageResponse)
async def send_message(request: SendMessageRequest) -> SendMessageResponse:
    """
    Send a message and get a response (non-streaming).

    For streaming responses, use the WebSocket endpoint.
    """
    from agents.orchestrator import ChatOrchestrator
    from models.user import User

    try:
        user_uuid = UUID(request.user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID")

    async with get_session() as session:
        user = await session.get(User, user_uuid)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        if user.customer_id is None:
            raise HTTPException(status_code=400, detail="User has no associated customer")

        orchestrator = ChatOrchestrator(
            user_id=str(user.id), customer_id=str(user.customer_id)
        )

        # Collect all chunks into a single response
        response_content = ""
        async for chunk in orchestrator.process_message(request.content):
            response_content += chunk

        # Get the message IDs from the database
        result = await session.execute(
            select(ChatMessage)
            .where(ChatMessage.user_id == user_uuid)
            .order_by(ChatMessage.created_at.desc())
            .limit(2)
        )
        recent_messages = result.scalars().all()

        user_msg_id = ""
        assistant_msg_id = ""
        for msg in recent_messages:
            if msg.role == "user":
                user_msg_id = str(msg.id)
            elif msg.role == "assistant":
                assistant_msg_id = str(msg.id)

        return SendMessageResponse(
            user_message_id=user_msg_id,
            assistant_message_id=assistant_msg_id,
            assistant_content=response_content,
        )
