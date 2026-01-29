"""
Chat endpoints for REST-based interactions.

Endpoints:
- GET /api/chat/conversations - List conversations for user
- POST /api/chat/conversations - Create a new conversation
- GET /api/chat/conversations/{id} - Get conversation with messages
- PATCH /api/chat/conversations/{id} - Update conversation (title, etc.)
- DELETE /api/chat/conversations/{id} - Delete a conversation
- GET /api/chat/history - Get chat history for user (legacy, deprecated)
- POST /api/chat/message - Send a message (non-streaming alternative)
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, update, func

from models.database import get_session
from models.chat_message import ChatMessage
from models.conversation import Conversation
from models.user import User

router = APIRouter()


# =============================================================================
# Request/Response Models
# =============================================================================

class ConversationCreate(BaseModel):
    """Request model for creating a conversation."""
    user_id: str
    title: Optional[str] = None


class ConversationUpdate(BaseModel):
    """Request model for updating a conversation."""
    title: Optional[str] = None


class ConversationResponse(BaseModel):
    """Response model for a conversation."""
    id: str
    user_id: str
    title: Optional[str]
    summary: Optional[str]
    created_at: str
    updated_at: str
    message_count: int = 0
    last_message_preview: Optional[str] = None


class ConversationListResponse(BaseModel):
    """Response model for listing conversations."""
    conversations: list[ConversationResponse]
    total: int


class ChatMessageResponse(BaseModel):
    """Response model for chat messages."""
    id: str
    conversation_id: Optional[str]
    role: str
    content_blocks: list[dict]
    created_at: str


class ConversationDetailResponse(BaseModel):
    """Response model for conversation with messages."""
    id: str
    user_id: str
    title: Optional[str]
    summary: Optional[str]
    created_at: str
    updated_at: str
    messages: list[ChatMessageResponse]


class ChatHistoryResponse(BaseModel):
    """Response model for chat history (legacy)."""
    messages: list[ChatMessageResponse]


class SendMessageRequest(BaseModel):
    """Request model for sending a message."""
    user_id: str
    conversation_id: Optional[str] = None
    content: str


class SendMessageResponse(BaseModel):
    """Response model for sent message."""
    conversation_id: str
    user_message_id: str
    assistant_message_id: str
    assistant_content: str


# =============================================================================
# Conversation Endpoints
# =============================================================================

@router.get("/conversations", response_model=ConversationListResponse)
async def list_conversations(
    user_id: str,
    limit: int = 50,
    offset: int = 0
) -> ConversationListResponse:
    """List conversations for a user, ordered by most recent."""
    try:
        user_uuid = UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID")

    async with get_session() as session:
        # Simple query - message_count and last_message_preview are cached on the conversation
        result = await session.execute(
            select(Conversation, func.count(Conversation.id).over().label("total_count"))
            .where(Conversation.user_id == user_uuid)
            .order_by(Conversation.updated_at.desc())
            .offset(offset)
            .limit(limit)
        )
        rows = result.all()

        # Extract total from first row (window function returns same value for all rows)
        total: int = rows[0][1] if rows else 0

        # Build response using cached fields
        response_items: list[ConversationResponse] = []
        for row in rows:
            conv: Conversation = row[0]

            response_items.append(ConversationResponse(
                id=str(conv.id),
                user_id=str(conv.user_id),
                title=conv.title,
                summary=conv.summary,
                created_at=f"{conv.created_at.isoformat()}Z" if conv.created_at else "",
                updated_at=f"{conv.updated_at.isoformat()}Z" if conv.updated_at else "",
                message_count=conv.message_count,
                last_message_preview=conv.last_message_preview[:100] if conv.last_message_preview else None,
            ))

        return ConversationListResponse(
            conversations=response_items,
            total=total,
        )


@router.post("/conversations", response_model=ConversationResponse)
async def create_conversation(request: ConversationCreate) -> ConversationResponse:
    """Create a new conversation."""
    try:
        user_uuid = UUID(request.user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID")

    async with get_session() as session:
        # Get user's organization_id
        user = await session.get(User, user_uuid)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        conversation = Conversation(
            user_id=user_uuid,
            organization_id=user.organization_id,
            title=request.title,
        )
        session.add(conversation)
        await session.commit()
        await session.refresh(conversation)

        return ConversationResponse(
            id=str(conversation.id),
            user_id=str(conversation.user_id),
            title=conversation.title,
            summary=conversation.summary,
            created_at=f"{conversation.created_at.isoformat()}Z" if conversation.created_at else "",
            updated_at=f"{conversation.updated_at.isoformat()}Z" if conversation.updated_at else "",
            message_count=0,
            last_message_preview=None,
        )


@router.get("/conversations/{conversation_id}", response_model=ConversationDetailResponse)
async def get_conversation(
    conversation_id: str,
    user_id: str,
) -> ConversationDetailResponse:
    """Get a conversation with all its messages."""
    try:
        conv_uuid = UUID(conversation_id)
        user_uuid = UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ID format")

    async with get_session() as session:
        result = await session.execute(
            select(Conversation)
            .where(Conversation.id == conv_uuid)
            .where(Conversation.user_id == user_uuid)
        )
        conversation = result.scalar_one_or_none()

        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found")

        # Get messages
        msg_result = await session.execute(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conv_uuid)
            .order_by(ChatMessage.created_at.asc())
        )
        messages = msg_result.scalars().all()

        return ConversationDetailResponse(
            id=str(conversation.id),
            user_id=str(conversation.user_id),
            title=conversation.title,
            summary=conversation.summary,
            created_at=f"{conversation.created_at.isoformat()}Z" if conversation.created_at else "",
            updated_at=f"{conversation.updated_at.isoformat()}Z" if conversation.updated_at else "",
            messages=[
                ChatMessageResponse(**msg.to_dict())
                for msg in messages
            ],
        )


@router.patch("/conversations/{conversation_id}", response_model=ConversationResponse)
async def update_conversation(
    conversation_id: str,
    user_id: str,
    request: ConversationUpdate,
) -> ConversationResponse:
    """Update a conversation (title, etc.)."""
    try:
        conv_uuid = UUID(conversation_id)
        user_uuid = UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ID format")

    async with get_session() as session:
        result = await session.execute(
            select(Conversation)
            .where(Conversation.id == conv_uuid)
            .where(Conversation.user_id == user_uuid)
        )
        conversation = result.scalar_one_or_none()

        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found")

        # Update fields
        if request.title is not None:
            conversation.title = request.title
        conversation.updated_at = datetime.utcnow()

        await session.commit()
        await session.refresh(conversation)

        return ConversationResponse(
            id=str(conversation.id),
            user_id=str(conversation.user_id),
            title=conversation.title,
            summary=conversation.summary,
            created_at=f"{conversation.created_at.isoformat()}Z" if conversation.created_at else "",
            updated_at=f"{conversation.updated_at.isoformat()}Z" if conversation.updated_at else "",
            message_count=0,
            last_message_preview=None,
        )


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: str,
    user_id: str,
) -> dict[str, bool]:
    """Delete a conversation and all its messages."""
    try:
        conv_uuid = UUID(conversation_id)
        user_uuid = UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ID format")

    async with get_session() as session:
        result = await session.execute(
            select(Conversation)
            .where(Conversation.id == conv_uuid)
            .where(Conversation.user_id == user_uuid)
        )
        conversation = result.scalar_one_or_none()

        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found")

        await session.delete(conversation)
        await session.commit()

        return {"success": True}


# =============================================================================
# Legacy Endpoints (for backwards compatibility)
# =============================================================================

@router.get("/history", response_model=ChatHistoryResponse)
async def get_chat_history(
    user_id: str,
    conversation_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0
) -> ChatHistoryResponse:
    """Get chat history for a user (optionally filtered by conversation)."""
    try:
        user_uuid = UUID(user_id)
        conv_uuid = UUID(conversation_id) if conversation_id else None
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ID format")

    async with get_session() as session:
        query = select(ChatMessage).where(ChatMessage.user_id == user_uuid)
        
        if conv_uuid:
            query = query.where(ChatMessage.conversation_id == conv_uuid)
        
        query = query.order_by(ChatMessage.created_at.desc()).offset(offset).limit(limit)
        
        result = await session.execute(query)
        messages = result.scalars().all()

        return ChatHistoryResponse(
            messages=[
                ChatMessageResponse(
                    id=str(msg.id),
                    conversation_id=str(msg.conversation_id) if msg.conversation_id else None,
                    role=msg.role,
                    content=msg.content,
                    created_at=f"{msg.created_at.isoformat()}Z" if msg.created_at else "",
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
        conv_uuid = UUID(request.conversation_id) if request.conversation_id else None
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ID format")

    async with get_session() as session:
        user = await session.get(User, user_uuid)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # Create conversation if not provided
        if not conv_uuid:
            conversation = Conversation(
                user_id=user_uuid,
                organization_id=user.organization_id,
                title=None,  # Will be set after first message
            )
            session.add(conversation)
            await session.commit()
            await session.refresh(conversation)
            conv_uuid = conversation.id

        # Allow users without organization to chat with limited functionality
        orchestrator = ChatOrchestrator(
            user_id=str(user.id),
            organization_id=str(user.organization_id) if user.organization_id else None,
            conversation_id=str(conv_uuid),
        )

        # Collect all chunks into a single response
        response_content = ""
        async for chunk in orchestrator.process_message(request.content):
            response_content += chunk

        # Get the message IDs from the database
        result = await session.execute(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conv_uuid)
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
            conversation_id=str(conv_uuid),
            user_message_id=user_msg_id,
            assistant_message_id=assistant_msg_id,
            assistant_content=response_content,
        )
