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
- POST /api/chat/upload - Upload a file attachment for chat context
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
import logging


from pydantic import BaseModel
from sqlalchemy import and_, func, or_, select

from api.auth_middleware import AuthContext, get_current_auth
from models.database import get_session
from models.chat_message import ChatMessage
from models.conversation import Conversation
from services.file_handler import store_file, MAX_FILE_SIZE
from services.slack_conversations import get_slack_user_ids_for_revtops_user


router = APIRouter()
logger = logging.getLogger(__name__)

async def _get_slack_user_ids(auth: AuthContext) -> set[str]:
    org_id = auth.organization_id_str
    if not org_id:
        return set()
    try:
        return await get_slack_user_ids_for_revtops_user(org_id, auth.user_id_str)
    except Exception as exc:
        logger.warning(
            "[chat] Failed to resolve Slack user IDs for org=%s user=%s: %s",
            org_id,
            auth.user_id_str,
            exc,
            exc_info=True,
        )
    return set()


def _build_conversation_access_filter(
    auth: AuthContext,
    slack_user_ids: set[str],
):
    base_filter = or_(
        Conversation.user_id == auth.user_id,
        Conversation.participating_user_ids.any(auth.user_id),
    )
    if not slack_user_ids:
        return base_filter
    slack_filter = and_(
        Conversation.source == "slack",
        Conversation.source_user_id.in_(slack_user_ids),
    )
    if auth.organization_id:
        slack_filter = and_(slack_filter, Conversation.organization_id == auth.organization_id)
    return or_(base_filter, slack_filter)


# =============================================================================
# Request/Response Models
# =============================================================================

class ConversationCreate(BaseModel):
    """Request model for creating a conversation."""
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
    type: Optional[str]
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
    slack_user_ids = await _get_slack_user_ids(auth)

    async with get_session(organization_id=org_id) as session:
        # Simple query - message_count and last_message_preview are cached on the conversation
        # Filter out workflow conversations - they're accessed via Automations tab, not chat list
        query = (
            select(Conversation, func.count(Conversation.id).over().label("total_count"))
            .where(Conversation.type != "workflow")
        )
        query = query.where(_build_conversation_access_filter(auth, slack_user_ids))
        if slack_user_ids:
            logger.info(
                "[chat] Listing conversations for user=%s with Slack IDs %s",
                auth.user_id_str,
                sorted(slack_user_ids),
            )
        result = await session.execute(
            query.order_by(Conversation.updated_at.desc())
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

            if conv.source == "slack":
                preview_length = len(conv.last_message_preview or "")
                logger.debug(
                    "[chat] Slack conversation preview: id=%s source_user=%s length=%d message_count=%d",
                    conv.id,
                    conv.source_user_id,
                    preview_length,
                    conv.message_count,
                )
                if not conv.last_message_preview:
                    logger.info(
                        "[chat] Slack conversation missing preview: id=%s source_channel=%s",
                        conv.id,
                        conv.source_channel_id,
                    )

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
            participating_user_ids=[auth.user_id],
            title=request.title,
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
            message_count=0,
            last_message_preview=None,
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

    slack_user_ids = await _get_slack_user_ids(auth)
    async with get_session(organization_id=org_id) as session:
        result = await session.execute(
            select(Conversation)
            .where(Conversation.id == conv_uuid)
            .where(_build_conversation_access_filter(auth, slack_user_ids))
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
            type=conversation.type,
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

    slack_user_ids = await _get_slack_user_ids(auth)
    async with get_session(organization_id=org_id) as session:
        result = await session.execute(
            select(Conversation)
            .where(Conversation.id == conv_uuid)
            .where(_build_conversation_access_filter(auth, slack_user_ids))
        )
        conversation = result.scalar_one_or_none()

        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found")

        # Update fields
        if request.title is not None:
            conversation.title = request.title
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
            message_count=0,
            last_message_preview=None,
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

    slack_user_ids = await _get_slack_user_ids(auth)
    async with get_session(organization_id=org_id) as session:
        result = await session.execute(
            select(Conversation)
            .where(Conversation.id == conv_uuid)
            .where(_build_conversation_access_filter(auth, slack_user_ids))
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

    slack_user_ids = await _get_slack_user_ids(auth)
    async with get_session(organization_id=org_id) as session:
        query = (
            select(ChatMessage)
            .join(Conversation, ChatMessage.conversation_id == Conversation.id)
            .where(_build_conversation_access_filter(auth, slack_user_ids))
        )
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
    from services.credits import can_use_credits

    try:
        conv_uuid = UUID(request.conversation_id) if request.conversation_id else None
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid conversation ID format")

    org_id = auth.organization_id_str
    if org_id and not await can_use_credits(org_id):
        raise HTTPException(
            status_code=402,
            detail="Insufficient credits or no active subscription. Please upgrade your plan or add a payment method.",
        )

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
            user_email=auth.email,
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


# =============================================================================
# File Upload
# =============================================================================

class UploadResponse(BaseModel):
    upload_id: str
    filename: str
    mime_type: str
    size: int


@router.post("/upload", response_model=UploadResponse)
async def upload_file(
    file: UploadFile = File(...),
    auth: AuthContext = Depends(get_current_auth),
) -> UploadResponse:
    """
    Upload a file to attach to a chat message.

    Files are stored temporarily in memory and consumed when the
    message is sent via WebSocket. Max size: 10 MB.
    """
    if file.filename is None:
        raise HTTPException(status_code=400, detail="Filename is required")

    data: bytes = await file.read()

    if len(data) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds maximum size of {MAX_FILE_SIZE // (1024 * 1024)} MB",
        )

    try:
        stored = store_file(
            filename=file.filename,
            data=data,
            content_type=file.content_type,
        )
    except ValueError as e:
        raise HTTPException(status_code=413, detail=str(e))

    return UploadResponse(
        upload_id=stored.upload_id,
        filename=stored.filename,
        mime_type=stored.mime_type,
        size=stored.size,
    )
