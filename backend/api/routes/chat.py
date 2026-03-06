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

import json
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
import logging
import redis.asyncio as aioredis


from pydantic import BaseModel
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth_middleware import AuthContext, get_current_auth
from config import get_redis_connection_kwargs, settings
from models.database import get_session
from models.chat_message import ChatMessage
from models.conversation import Conversation
from models.user import User
from services.file_handler import store_file, MAX_FILE_SIZE
from services.slack_conversations import get_slack_user_ids_for_revtops_user


router = APIRouter()
logger = logging.getLogger(__name__)

_redis_client: aioredis.Redis | None = None
_SLACK_USER_IDS_TTL = 300  # 5 minutes


async def _get_redis() -> aioredis.Redis:
    """Lazy-initialize a module-level async Redis client."""
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(
            settings.REDIS_URL, **get_redis_connection_kwargs(decode_responses=True)
        )
    return _redis_client


async def _get_slack_user_ids(
    auth: AuthContext, session: AsyncSession | None = None,
) -> set[str]:
    org_id = auth.organization_id_str
    if not org_id:
        return set()

    cache_key = f"slack_user_ids:{org_id}:{auth.user_id_str}"

    # Try Redis cache first
    try:
        r = await _get_redis()
        cached = await r.get(cache_key)
        if cached is not None:
            return set(json.loads(cached))
    except Exception:
        # Redis unavailable — fall through to direct call
        pass

    # Cache miss (or Redis error): resolve from connector layer
    try:
        result = await get_slack_user_ids_for_revtops_user(
            org_id, auth.user_id_str, session=session,
        )
    except Exception as exc:
        logger.warning(
            "[chat] Failed to resolve Slack user IDs for org=%s user=%s: %s",
            org_id,
            auth.user_id_str,
            exc,
            exc_info=True,
        )
        return set()

    # Store in Redis (best-effort; don't break the request if Redis is down)
    try:
        r = await _get_redis()
        await r.set(cache_key, json.dumps(sorted(result)), ex=_SLACK_USER_IDS_TTL)
    except Exception:
        pass

    return result


def _build_conversation_access_filter(
    auth: AuthContext,
    slack_user_ids: set[str],
):
    # User's own conversations (private or shared)
    user_filter = or_(
        Conversation.user_id == auth.user_id,
        Conversation.participating_user_ids.any(auth.user_id),
    )
    
    # Shared conversations are visible to everyone in the org
    shared_org_filter = and_(
        Conversation.scope == "shared",
        Conversation.organization_id == auth.organization_id,
    ) if auth.organization_id else None
    
    # Slack conversations where user is the source
    slack_filter = None
    if slack_user_ids:
        slack_filter = and_(
            Conversation.source == "slack",
            Conversation.source_user_id.in_(slack_user_ids),
        )
        if auth.organization_id:
            slack_filter = and_(slack_filter, Conversation.organization_id == auth.organization_id)
    
    # Combine all filters
    filters = [user_filter]
    if shared_org_filter is not None:
        filters.append(shared_org_filter)
    if slack_filter is not None:
        filters.append(slack_filter)
    
    return or_(*filters)


# =============================================================================
# Request/Response Models
# =============================================================================

class ConversationCreate(BaseModel):
    """Request model for creating a conversation."""
    title: Optional[str] = None
    scope: Optional[str] = "shared"  # "private" or "shared" (default)


class ConversationUpdate(BaseModel):
    """Request model for updating a conversation."""
    title: Optional[str] = None


class ParticipantResponse(BaseModel):
    """Response model for a conversation participant."""
    id: str
    name: Optional[str]
    email: str
    avatar_url: Optional[str] = None


class ConversationResponse(BaseModel):
    """Response model for a conversation."""
    id: str
    user_id: Optional[str]
    title: Optional[str]
    summary: Optional[str]
    created_at: str
    updated_at: str
    message_count: int = 0
    last_message_preview: Optional[str] = None
    scope: str = "shared"
    participants: list[ParticipantResponse] = []


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
    user_id: Optional[str] = None
    sender_name: Optional[str] = None
    sender_email: Optional[str] = None
    sender_avatar_url: Optional[str] = None


class ConversationDetailResponse(BaseModel):
    """Response model for conversation with messages."""
    id: str
    user_id: Optional[str]
    title: Optional[str]
    summary: Optional[str]
    created_at: str
    updated_at: str
    type: Optional[str]
    scope: str = "shared"
    participants: list[ParticipantResponse] = []
    messages: list[ChatMessageResponse]
    has_more: bool = False


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
    offset: int = 0,
    scope: Optional[str] = None,
) -> ConversationListResponse:
    """List conversations for the authenticated user, ordered by most recent.
    
    Args:
        scope: Optional filter - "shared" or "private". If not provided, returns all.
    """
    org_id = auth.organization_id_str

    async with get_session(organization_id=org_id) as session:
        slack_user_ids = await _get_slack_user_ids(auth, session=session)
        # Simple query - message_count and last_message_preview are cached on the conversation
        # Filter out workflow conversations - they're accessed via Automations tab, not chat list
        query = (
            select(Conversation, func.count(Conversation.id).over().label("total_count"))
            .where(Conversation.type != "workflow")
        )
        query = query.where(_build_conversation_access_filter(auth, slack_user_ids))
        
        # Optional scope filter
        if scope in ("shared", "private"):
            query = query.where(Conversation.scope == scope)
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

        # Collect all participant user IDs to fetch in one query
        all_participant_ids: set[UUID] = set()
        for row in rows:
            conv: Conversation = row[0]
            for uid in (conv.participating_user_ids or []):
                all_participant_ids.add(uid)

        # Fetch all participants in one query
        participants_by_id: dict[UUID, User] = {}
        if all_participant_ids:
            users_result = await session.execute(
                select(User).where(User.id.in_(all_participant_ids))
            )
            for user in users_result.scalars().all():
                participants_by_id[user.id] = user

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

            # Build participants list
            participants: list[ParticipantResponse] = []
            for uid in (conv.participating_user_ids or []):
                user = participants_by_id.get(uid)
                if user:
                    participants.append(ParticipantResponse(
                        id=str(user.id),
                        name=user.name,
                        email=user.email,
                        avatar_url=user.avatar_url,
                    ))

            response_items.append(ConversationResponse(
                id=str(conv.id),
                user_id=str(conv.user_id) if conv.user_id else None,
                title=conv.title,
                summary=conv.summary,
                created_at=f"{conv.created_at.isoformat()}Z" if conv.created_at else "",
                updated_at=f"{conv.updated_at.isoformat()}Z" if conv.updated_at else "",
                message_count=conv.message_count,
                last_message_preview=conv.last_message_preview[:100] if conv.last_message_preview else None,
                scope=conv.scope,
                participants=participants,
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

    # Validate scope
    scope = request.scope or "shared"
    if scope not in ("private", "shared"):
        raise HTTPException(status_code=400, detail="Invalid scope. Must be 'private' or 'shared'")

    async with get_session(organization_id=org_id) as session:
        conversation = Conversation(
            user_id=auth.user_id,
            organization_id=auth.organization_id,
            participating_user_ids=[auth.user_id],
            title=request.title,
            scope=scope,
        )
        session.add(conversation)
        # Capture values before commit (model defaults are set on instantiation)
        conv_id = str(conversation.id)
        conv_title = conversation.title
        conv_summary = conversation.summary
        conv_scope = conversation.scope
        conv_created_at = conversation.created_at
        conv_updated_at = conversation.updated_at

        # Fetch user info for participant response
        user_result = await session.execute(
            select(User).where(User.id == auth.user_id)
        )
        user = user_result.scalar_one_or_none()

        await session.commit()
        # Note: don't call refresh() - it can fail due to RLS after commit

        # Build participant list (just the creator for new conversations)
        participants: list[ParticipantResponse] = []
        if user:
            participants.append(ParticipantResponse(
                id=str(user.id),
                name=user.name,
                email=user.email,
                avatar_url=user.avatar_url,
            ))

        return ConversationResponse(
            id=conv_id,
            user_id=auth.user_id_str,
            title=conv_title,
            summary=conv_summary,
            created_at=f"{conv_created_at.isoformat()}Z" if conv_created_at else "",
            updated_at=f"{conv_updated_at.isoformat()}Z" if conv_updated_at else "",
            message_count=0,
            last_message_preview=None,
            scope=conv_scope,
            participants=participants,
        )


@router.get("/conversations/{conversation_id}", response_model=ConversationDetailResponse)
async def get_conversation(
    conversation_id: str,
    auth: AuthContext = Depends(get_current_auth),
    limit: int = 30,
    before: Optional[str] = None,
) -> ConversationDetailResponse:
    """Get a conversation with its messages (paginated).

    Args:
        limit: Number of messages to return (default 30).
        before: ISO timestamp cursor — return messages created before this time
                (pass the oldest loaded message's ``created_at`` to page backwards).
    """
    try:
        conv_uuid = UUID(conversation_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid conversation ID format")

    # Parse the cursor timestamp when provided
    before_dt: Optional[datetime] = None
    if before is not None:
        try:
            # Accept ISO 8601 with or without trailing 'Z', strip tzinfo
            # since the DB column is TIMESTAMP WITHOUT TIME ZONE
            before_dt = datetime.fromisoformat(before.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid 'before' timestamp format")

    org_id = auth.organization_id_str

    async with get_session(organization_id=org_id) as session:
        slack_user_ids = await _get_slack_user_ids(auth, session=session)
        result = await session.execute(
            select(Conversation)
            .where(Conversation.id == conv_uuid)
            .where(_build_conversation_access_filter(auth, slack_user_ids))
        )
        conversation = result.scalar_one_or_none()

        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found")

        # Build paginated message query
        msg_query = (
            select(ChatMessage, User.name, User.email, User.avatar_url)
            .outerjoin(User, ChatMessage.user_id == User.id)
            .where(ChatMessage.conversation_id == conv_uuid)
        )

        if before_dt is not None:
            msg_query = msg_query.where(ChatMessage.created_at < before_dt)

        # Fetch limit+1 rows so we can detect whether older messages exist
        msg_query = msg_query.order_by(ChatMessage.created_at.desc()).limit(limit + 1)

        msg_result = await session.execute(msg_query)
        message_rows = msg_result.all()

        # Determine has_more and trim the extra probe row
        has_more = len(message_rows) > limit
        if has_more:
            message_rows = message_rows[:limit]

        # Reverse to chronological order (oldest first)
        message_rows = list(reversed(message_rows))

        # Fetch participants
        participants: list[ParticipantResponse] = []
        if conversation.participating_user_ids:
            users_result = await session.execute(
                select(User).where(User.id.in_(conversation.participating_user_ids))
            )
            for user in users_result.scalars().all():
                participants.append(ParticipantResponse(
                    id=str(user.id),
                    name=user.name,
                    email=user.email,
                    avatar_url=user.avatar_url,
                ))

        return ConversationDetailResponse(
            id=str(conversation.id),
            user_id=str(conversation.user_id) if conversation.user_id else None,
            title=conversation.title,
            summary=conversation.summary,
            created_at=f"{conversation.created_at.isoformat()}Z" if conversation.created_at else "",
            updated_at=f"{conversation.updated_at.isoformat()}Z" if conversation.updated_at else "",
            type=conversation.type,
            scope=conversation.scope,
            participants=participants,
            messages=[
                ChatMessageResponse(**msg.to_dict(sender_name=sender_name, sender_email=sender_email, sender_avatar_url=sender_avatar_url))
                for msg, sender_name, sender_email, sender_avatar_url in message_rows
            ],
            has_more=has_more,
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
        slack_user_ids = await _get_slack_user_ids(auth, session=session)
        result = await session.execute(
            select(Conversation)
            .where(Conversation.id == conv_uuid)
            .where(_build_conversation_access_filter(auth, slack_user_ids))
        )
        conversation = result.scalar_one_or_none()

        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found")

        # Only the creator can rename shared conversations
        if request.title is not None and conversation.scope == "shared":
            if str(conversation.user_id) != str(auth.user_id):
                raise HTTPException(
                    status_code=403,
                    detail="Only the chat creator can rename shared conversations",
                )

        # Update fields
        if request.title is not None:
            conversation.title = request.title
        
        # Capture values before commit
        conv_id = str(conversation.id)
        conv_user_id = str(conversation.user_id) if conversation.user_id else None
        conv_title = conversation.title
        conv_summary = conversation.summary
        conv_scope = conversation.scope
        conv_created_at = conversation.created_at
        conv_updated_at = conversation.updated_at
        conv_participant_ids = list(conversation.participating_user_ids or [])

        # Fetch participants
        participants: list[ParticipantResponse] = []
        if conv_participant_ids:
            users_result = await session.execute(
                select(User).where(User.id.in_(conv_participant_ids))
            )
            for user in users_result.scalars().all():
                participants.append(ParticipantResponse(
                    id=str(user.id),
                    name=user.name,
                    email=user.email,
                    avatar_url=user.avatar_url,
                ))

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
            scope=conv_scope,
            participants=participants,
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
        slack_user_ids = await _get_slack_user_ids(auth, session=session)
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
# Participant Management Endpoints
# =============================================================================

class AddParticipantRequest(BaseModel):
    """Request model for adding a participant."""
    user_id: Optional[str] = None
    email: Optional[str] = None


class AddParticipantResponse(BaseModel):
    """Response model for adding a participant."""
    success: bool
    participant: ParticipantResponse


@router.post("/conversations/{conversation_id}/participants", response_model=AddParticipantResponse)
async def add_participant(
    conversation_id: str,
    request: AddParticipantRequest,
    auth: AuthContext = Depends(get_current_auth),
) -> AddParticipantResponse:
    """Add a participant to a shared conversation."""
    try:
        conv_uuid = UUID(conversation_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid conversation ID format")

    if not request.user_id and not request.email:
        raise HTTPException(status_code=400, detail="Must provide user_id or email")

    org_id = auth.organization_id_str

    async with get_session(organization_id=org_id) as session:
        slack_user_ids = await _get_slack_user_ids(auth, session=session)
        # Get conversation
        result = await session.execute(
            select(Conversation)
            .where(Conversation.id == conv_uuid)
            .where(_build_conversation_access_filter(auth, slack_user_ids))
        )
        conversation = result.scalar_one_or_none()

        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found")

        if conversation.scope == "private":
            raise HTTPException(status_code=400, detail="Cannot add participants to a private conversation")

        # Find the user to add
        if request.user_id:
            try:
                target_user_uuid = UUID(request.user_id)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid user_id format")
            user_result = await session.execute(
                select(User).where(User.id == target_user_uuid)
            )
        else:
            user_result = await session.execute(
                select(User).where(User.email == request.email)
            )
        
        target_user = user_result.scalar_one_or_none()
        if not target_user:
            raise HTTPException(status_code=404, detail="User not found")

        # Verify user is in the same organization
        if target_user.organization_id != auth.organization_id:
            raise HTTPException(status_code=403, detail="User is not in your organization")

        # Check if already a participant
        current_participants = list(conversation.participating_user_ids or [])
        if target_user.id in current_participants:
            # Already a participant, just return success
            return AddParticipantResponse(
                success=True,
                participant=ParticipantResponse(
                    id=str(target_user.id),
                    name=target_user.name,
                    email=target_user.email,
                    avatar_url=target_user.avatar_url,
                ),
            )

        # Add participant
        current_participants.append(target_user.id)
        conversation.participating_user_ids = current_participants
        conversation.updated_at = datetime.utcnow()

        await session.commit()

        return AddParticipantResponse(
            success=True,
            participant=ParticipantResponse(
                id=str(target_user.id),
                name=target_user.name,
                email=target_user.email,
                avatar_url=target_user.avatar_url,
            ),
        )


@router.delete("/conversations/{conversation_id}/participants/{user_id}")
async def remove_participant(
    conversation_id: str,
    user_id: str,
    auth: AuthContext = Depends(get_current_auth),
) -> dict[str, bool]:
    """Remove a participant from a shared conversation."""
    try:
        conv_uuid = UUID(conversation_id)
        target_user_uuid = UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ID format")

    org_id = auth.organization_id_str

    async with get_session(organization_id=org_id) as session:
        slack_user_ids = await _get_slack_user_ids(auth, session=session)
        result = await session.execute(
            select(Conversation)
            .where(Conversation.id == conv_uuid)
            .where(_build_conversation_access_filter(auth, slack_user_ids))
        )
        conversation = result.scalar_one_or_none()

        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found")

        if conversation.scope == "private":
            raise HTTPException(status_code=400, detail="Cannot remove participants from a private conversation")

        # Cannot remove yourself if you're the only participant
        current_participants = list(conversation.participating_user_ids or [])
        if target_user_uuid not in current_participants:
            return {"success": True}  # Already not a participant

        if len(current_participants) == 1:
            raise HTTPException(status_code=400, detail="Cannot remove the last participant")

        # Remove participant
        current_participants.remove(target_user_uuid)
        conversation.participating_user_ids = current_participants
        conversation.updated_at = datetime.utcnow()

        await session.commit()

        return {"success": True}


class UpdateScopeRequest(BaseModel):
    """Request model for updating conversation scope."""
    scope: str  # "shared" or "private"


@router.patch("/conversations/{conversation_id}/scope", response_model=ConversationResponse)
async def update_scope(
    conversation_id: str,
    request: UpdateScopeRequest,
    auth: AuthContext = Depends(get_current_auth),
) -> ConversationResponse:
    """Toggle conversation scope between private and shared."""
    try:
        conv_uuid = UUID(conversation_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid conversation ID format")

    if request.scope not in ("shared", "private"):
        raise HTTPException(status_code=400, detail="Scope must be 'shared' or 'private'")

    org_id = auth.organization_id_str

    async with get_session(organization_id=org_id) as session:
        slack_user_ids = await _get_slack_user_ids(auth, session=session)
        result = await session.execute(
            select(Conversation)
            .where(Conversation.id == conv_uuid)
            .where(_build_conversation_access_filter(auth, slack_user_ids))
        )
        conversation = result.scalar_one_or_none()

        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found")

        # Only the creator can make a shared conversation private
        if request.scope == "private" and conversation.scope == "shared":
            if str(conversation.user_id) != str(auth.user_id):
                raise HTTPException(status_code=403, detail="Only the chat creator can make a shared conversation private")

        if conversation.scope == request.scope:
            # Already in the requested state, just return current state
            pass
        else:
            conversation.scope = request.scope
            conversation.updated_at = datetime.utcnow()

        # Capture values before commit
        conv_id = str(conversation.id)
        conv_user_id = str(conversation.user_id) if conversation.user_id else None
        conv_title = conversation.title
        conv_summary = conversation.summary
        conv_scope = conversation.scope
        conv_created_at = conversation.created_at
        conv_updated_at = conversation.updated_at
        conv_participant_ids = list(conversation.participating_user_ids or [])

        # Fetch participants
        participants: list[ParticipantResponse] = []
        if conv_participant_ids:
            users_result = await session.execute(
                select(User).where(User.id.in_(conv_participant_ids))
            )
            for user in users_result.scalars().all():
                participants.append(ParticipantResponse(
                    id=str(user.id),
                    name=user.name,
                    email=user.email,
                    avatar_url=user.avatar_url,
                ))

        await session.commit()

        return ConversationResponse(
            id=conv_id,
            user_id=conv_user_id,
            title=conv_title,
            summary=conv_summary,
            created_at=f"{conv_created_at.isoformat()}Z" if conv_created_at else "",
            updated_at=f"{conv_updated_at.isoformat()}Z" if conv_updated_at else "",
            message_count=0,
            last_message_preview=None,
            scope=conv_scope,
            participants=participants,
        )


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
        slack_user_ids = await _get_slack_user_ids(auth, session=session)
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
