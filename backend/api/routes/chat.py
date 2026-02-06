"""
Chat endpoints for REST-based interactions.

SECURITY: All endpoints use JWT authentication via the AuthContext dependency.
User and organization are verified from the JWT token, NOT from query parameters.

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

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func

from api.auth_middleware import AuthContext, get_current_auth
from models.database import get_session
from models.chat_message import ChatMessage
from models.conversation import Conversation
from models.user import User
from services.permissions import can_access_resource, can_edit_resource

router = APIRouter()


# =============================================================================
# Request/Response Models
# =============================================================================

class ConversationCreate(BaseModel):
    """Request model for creating a conversation."""
    title: Optional[str] = None
    access_tier: Optional[str] = "me"
    access_level: Optional[str] = "edit"


class ConversationUpdate(BaseModel):
    """Request model for updating a conversation."""
    title: Optional[str] = None
    access_tier: Optional[str] = None
    access_level: Optional[str] = None


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
    access_tier: str = "me"
    access_level: str = "edit"
    can_edit: bool = True


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
    type: Optional[str]
    access_tier: str = "me"
    access_level: str = "edit"
    can_edit: bool = True
    messages: list[ChatMessageResponse]


class ChatHistoryResponse(BaseModel):
    """Response model for chat history (legacy)."""
    messages: list[ChatMessageResponse]


class SendMessageRequest(BaseModel):
    """Request model for sending a message."""
    conversation_id: Optional[str] = None
    content: str
    local_time: Optional[str] = None
    timezone: Optional[str] = None


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
    auth: AuthContext = Depends(get_current_auth),
    limit: int = 50,
    offset: int = 0
) -> ConversationListResponse:
    """List conversations for the authenticated user, ordered by most recent."""
    org_id = auth.organization_id_str

    async with get_session(organization_id=org_id) as session:
        me = await session.get(User, UUID(auth.user_id_str))
        result = await session.execute(
            select(Conversation)
                        .order_by(Conversation.updated_at.desc())
        )
        all_rows = result.scalars().all()
        visible = [
            c for c in all_rows
            if me and can_access_resource(owner_id=c.user_id, viewer=me, tier=c.access_tier)
        ]
        page = visible[offset:offset+limit]
        response_items: list[ConversationResponse] = []
        for conv in page:
            response_items.append(ConversationResponse(
                id=str(conv.id),
                user_id=str(conv.user_id) if conv.user_id else "",
                title=conv.title,
                summary=conv.summary,
                created_at=f"{conv.created_at.isoformat()}Z" if conv.created_at else "",
                updated_at=f"{conv.updated_at.isoformat()}Z" if conv.updated_at else "",
                message_count=conv.message_count,
                last_message_preview=conv.last_message_preview[:100] if conv.last_message_preview else None,
                access_tier=conv.access_tier,
                access_level=conv.access_level,
                can_edit=can_edit_resource(owner_id=conv.user_id, viewer=me, tier=conv.access_tier, access_level=conv.access_level) if me else False,
            ))

        return ConversationListResponse(conversations=response_items, total=len(visible))


@router.post("/conversations", response_model=ConversationResponse)
async def create_conversation(
    request: ConversationCreate,
    auth: AuthContext = Depends(get_current_auth),
) -> ConversationResponse:
    """Create a new conversation for the authenticated user."""
    org_id = auth.organization_id_str

    async with get_session(organization_id=org_id) as session:
        conversation = Conversation(
            user_id=auth.user_id,
            organization_id=auth.organization_id,
            title=request.title,
            access_tier=request.access_tier or "me",
            access_level=request.access_level or "edit",
        )
        session.add(conversation)
        # Capture values before commit (model defaults are set on instantiation)
        conv_id = str(conversation.id)
        conv_title = conversation.title
        conv_summary = conversation.summary
        conv_created_at = conversation.created_at
        conv_updated_at = conversation.updated_at
        await session.commit()
        # Note: don't call refresh() - it can fail due to RLS after commit

        return ConversationResponse(
            id=conv_id,
            user_id=auth.user_id_str,
            title=conv_title,
            summary=conv_summary,
            created_at=f"{conv_created_at.isoformat()}Z" if conv_created_at else "",
            updated_at=f"{conv_updated_at.isoformat()}Z" if conv_updated_at else "",
            message_count=conversation.message_count,
            last_message_preview=conversation.last_message_preview[:100] if conversation.last_message_preview else None,
            access_tier=conversation.access_tier,
            access_level=conversation.access_level,
            can_edit=True,
        )


@router.get("/conversations/{conversation_id}", response_model=ConversationDetailResponse)
async def get_conversation(
    conversation_id: str,
    auth: AuthContext = Depends(get_current_auth),
) -> ConversationDetailResponse:
    """Get a conversation with all its messages."""
    try:
        conv_uuid = UUID(conversation_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid conversation ID format")

    org_id = auth.organization_id_str

    async with get_session(organization_id=org_id) as session:
        result = await session.execute(
            select(Conversation)
            .where(Conversation.id == conv_uuid)
        )
        conversation = result.scalar_one_or_none()
        me = await session.get(User, UUID(auth.user_id_str))

        if not conversation or not me or not can_access_resource(owner_id=conversation.user_id, viewer=me, tier=conversation.access_tier):
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
            type=conversation.type,
            access_tier=conversation.access_tier,
            access_level=conversation.access_level,
            can_edit=can_edit_resource(owner_id=conversation.user_id, viewer=me, tier=conversation.access_tier, access_level=conversation.access_level),
            messages=[
                ChatMessageResponse(**msg.to_dict())
                for msg in messages
            ],
        )


@router.patch("/conversations/{conversation_id}", response_model=ConversationResponse)
async def update_conversation(
    conversation_id: str,
    request: ConversationUpdate,
    auth: AuthContext = Depends(get_current_auth),
) -> ConversationResponse:
    """Update a conversation (title, etc.)."""
    try:
        conv_uuid = UUID(conversation_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid conversation ID format")

    org_id = auth.organization_id_str

    async with get_session(organization_id=org_id) as session:
        result = await session.execute(
            select(Conversation)
            .where(Conversation.id == conv_uuid)
        )
        conversation = result.scalar_one_or_none()
        me = await session.get(User, UUID(auth.user_id_str))

        if not conversation or not me or not can_access_resource(owner_id=conversation.user_id, viewer=me, tier=conversation.access_tier):
            raise HTTPException(status_code=404, detail="Conversation not found")

        # Update fields
        if request.title is not None:
            conversation.title = request.title
        if request.access_tier is not None:
            conversation.access_tier = request.access_tier
        if request.access_level is not None:
            conversation.access_level = request.access_level
        conversation.updated_at = datetime.utcnow()
        
        # Capture values before commit
        conv_id = str(conversation.id)
        conv_user_id = str(conversation.user_id)
        conv_title = conversation.title
        conv_summary = conversation.summary
        conv_created_at = conversation.created_at
        conv_updated_at = conversation.updated_at

        await session.commit()
        # Note: don't call refresh() - it can fail due to RLS after commit

        return ConversationResponse(
            id=conv_id,
            user_id=conv_user_id,
            title=conv_title,
            summary=conv_summary,
            created_at=f"{conv_created_at.isoformat()}Z" if conv_created_at else "",
            updated_at=f"{conv_updated_at.isoformat()}Z" if conv_updated_at else "",
            message_count=conversation.message_count,
            last_message_preview=conversation.last_message_preview[:100] if conversation.last_message_preview else None,
            access_tier=conversation.access_tier,
            access_level=conversation.access_level,
            can_edit=True,
        )


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: str,
    auth: AuthContext = Depends(get_current_auth),
) -> dict[str, bool]:
    """Delete a conversation and all its messages."""
    try:
        conv_uuid = UUID(conversation_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid conversation ID format")

    org_id = auth.organization_id_str

    async with get_session(organization_id=org_id) as session:
        result = await session.execute(
            select(Conversation)
            .where(Conversation.id == conv_uuid)
        )
        conversation = result.scalar_one_or_none()
        me = await session.get(User, UUID(auth.user_id_str))

        if not conversation or not me or not can_access_resource(owner_id=conversation.user_id, viewer=me, tier=conversation.access_tier):
            raise HTTPException(status_code=404, detail="Conversation not found")

        await session.delete(conversation)
        await session.commit()

        return {"success": True}


# =============================================================================
# Legacy Endpoints (for backwards compatibility)
# =============================================================================

@router.get("/history", response_model=ChatHistoryResponse)
async def get_chat_history(
    auth: AuthContext = Depends(get_current_auth),
    conversation_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0
) -> ChatHistoryResponse:
    """Get chat history for authenticated user (optionally filtered by conversation)."""
    try:
        conv_uuid = UUID(conversation_id) if conversation_id else None
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid conversation ID format")

    org_id = auth.organization_id_str

    async with get_session(organization_id=org_id) as session:
        query = select(ChatMessage).where(ChatMessage.user_id == auth.user_id)
        
        if conv_uuid:
            query = query.where(ChatMessage.conversation_id == conv_uuid)
        
        query = query.order_by(ChatMessage.created_at.desc()).offset(offset).limit(limit)
        
        result = await session.execute(query)
        messages = result.scalars().all()

        return ChatHistoryResponse(
            messages=[
                ChatMessageResponse(**msg.to_dict())
                for msg in reversed(messages)
            ]
        )


@router.post("/message", response_model=SendMessageResponse)
async def send_message(
    request: SendMessageRequest,
    auth: AuthContext = Depends(get_current_auth),
) -> SendMessageResponse:
    """
    Send a message and get a response (non-streaming).

    For streaming responses, use the WebSocket endpoint.
    """
    from agents.orchestrator import ChatOrchestrator

    try:
        conv_uuid = UUID(request.conversation_id) if request.conversation_id else None
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid conversation ID format")

    org_id = auth.organization_id_str

    async with get_session(organization_id=org_id) as session:
        # Create conversation if not provided
        if not conv_uuid:
            conversation = Conversation(
                user_id=auth.user_id,
                organization_id=auth.organization_id,
                title=None,  # Will be set after first message
            )
            session.add(conversation)
            # Get ID before commit (UUID is generated on model instantiation)
            conv_uuid = conversation.id
            await session.commit()
            # Note: don't call refresh() - it can fail due to RLS after commit

        # Allow users without organization to chat with limited functionality
        orchestrator = ChatOrchestrator(
            user_id=auth.user_id_str,
            organization_id=org_id,
            conversation_id=str(conv_uuid),
            local_time=request.local_time,
            timezone=request.timezone,
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

@router.post("/conversations/{conversation_id}/copy", response_model=ConversationResponse)
async def copy_conversation(
    conversation_id: str,
    auth: AuthContext = Depends(get_current_auth),
) -> ConversationResponse:
    try:
        conv_uuid = UUID(conversation_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid conversation ID format")

    org_id = auth.organization_id_str
    async with get_session(organization_id=org_id) as session:
        me = await session.get(User, UUID(auth.user_id_str))
        result = await session.execute(select(Conversation).where(Conversation.id == conv_uuid))
        source = result.scalar_one_or_none()
        if not source or not me or not can_access_resource(owner_id=source.user_id, viewer=me, tier=source.access_tier):
            raise HTTPException(status_code=404, detail="Conversation not found")

        clone = Conversation(
            user_id=auth.user_id,
            organization_id=auth.organization_id,
            type=source.type,
            title=f"Copy of {source.title or 'Chat'}",
            summary=source.summary,
            access_tier="me",
            access_level="edit",
        )
        session.add(clone)
        await session.flush()

        msg_rows = await session.execute(select(ChatMessage).where(ChatMessage.conversation_id == source.id).order_by(ChatMessage.created_at.asc()))
        for msg in msg_rows.scalars().all():
            session.add(ChatMessage(
                user_id=auth.user_id,
                organization_id=auth.organization_id,
                conversation_id=clone.id,
                role=msg.role,
                content=msg.content,
                content_blocks=msg.content_blocks,
            ))
        await session.commit()

        return ConversationResponse(
            id=str(clone.id),
            user_id=auth.user_id_str,
            title=clone.title,
            summary=clone.summary,
            created_at=f"{clone.created_at.isoformat()}Z" if clone.created_at else "",
            updated_at=f"{clone.updated_at.isoformat()}Z" if clone.updated_at else "",
            message_count=clone.message_count,
            last_message_preview=clone.last_message_preview,
            access_tier=clone.access_tier,
            access_level=clone.access_level,
            can_edit=True,
        )
