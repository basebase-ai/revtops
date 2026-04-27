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

import asyncio
import base64
import json
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import Response
import logging
import redis.asyncio as aioredis


from pydantic import BaseModel
from sqlalchemy import and_, column, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth_middleware import AuthContext, get_current_auth
from config import get_redis_connection_kwargs, settings
from models.chat_attachment import ChatAttachment
from models.chat_message import ChatMessage
from models.conversation import Conversation
from models.database import get_admin_session, get_session
from models.integration import Integration
from models.org_member import OrgMember
from models.user import User
from connectors.slack import SlackConnector
from services.file_handler import store_file, MAX_FILE_SIZE
from services.slack_identity import get_slack_user_ids_for_revtops_user


router = APIRouter()
logger = logging.getLogger(__name__)

_redis_client: aioredis.Redis | None = None
_SLACK_USER_IDS_TTL = 300  # 5 minutes
_SLACK_WORKSPACE_ID_TTL = 300  # 5 minutes
_CHANNEL_NAME_SOFT_TTL_SECONDS = 15 * 60
_CHANNEL_NAME_HARD_TTL_SECONDS = 24 * 60 * 60
_CHANNEL_NAME_TTL_JITTER_RATIO = 0.10
_CHANNEL_NAME_SINGLE_FLIGHT_LOCK_SECONDS = 30
_WEB_PLATFORM_SLUG = "web"
_slack_channel_refresh_tasks: dict[str, asyncio.Task[None]] = {}


async def _get_redis() -> aioredis.Redis:
    """Lazy-initialize a module-level async Redis client."""
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(
            settings.REDIS_URL, **get_redis_connection_kwargs(decode_responses=True)
        )
    return _redis_client


def _register_slack_refresh_task(task_key: str, task: asyncio.Task[None]) -> None:
    """Track a single refresh task per key and clean up after completion."""
    _slack_channel_refresh_tasks[task_key] = task

    def _cleanup(done_task: asyncio.Task[None]) -> None:
        if _slack_channel_refresh_tasks.get(task_key) is done_task:
            _slack_channel_refresh_tasks.pop(task_key, None)

    task.add_done_callback(_cleanup)


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


async def _record_web_query_outcome(
    *,
    was_success: bool,
    failure_reason: str | None,
    conversation_id: str | None,
    user_id: str,
) -> None:
    """Best-effort metric recording for web-app turns."""
    from services.query_outcome_metrics import normalize_failure_reason, record_query_outcome

    try:
        await record_query_outcome(
            platform=_WEB_PLATFORM_SLUG,
            was_success=was_success,
            failure_reason=normalize_failure_reason(failure_reason) if not was_success else None,
            conversation_id=conversation_id,
        )
    except Exception:
        logger.exception(
            "[chat] Failed to record query outcome platform=%s was_success=%s conversation_id=%s user_id=%s failure_reason=%s",
            _WEB_PLATFORM_SLUG,
            was_success,
            conversation_id,
            user_id,
            failure_reason,
        )


def _build_conversation_access_filter(
    auth: AuthContext,
    slack_user_ids: set[str] | None = None,
):
    """Build an OR filter for conversations the user may access.

    When *slack_user_ids* is ``None`` (or empty) the Slack branch is omitted,
    making the query cheaper for the common non-Slack path.
    """
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
    agent_responding: bool = True
    participants: list[ParticipantResponse] = []
    match_snippet: Optional[str] = None  # Context around search match
    match_count: int = 0  # Number of times search term appears in conversation
    workspace_id: Optional[str] = None
    source: Optional[str] = None
    source_channel_id: Optional[str] = None
    normalized_channel_id: Optional[str] = None
    resolved_channel_name: Optional[str] = None
    group_bucket_type: str = "uncategorized"
    group_bucket_key: str = "uncategorized"


class ConversationListResponse(BaseModel):
    """Response model for listing conversations."""
    conversations: list[ConversationResponse]
    total: int
    search_term: Optional[str] = None  # Echo back the search term for highlighting
    next_cursor: Optional[str] = None
    has_more: bool = False
    server_time: str


def _normalize_channel_id(source: str | None, source_channel_id: str | None) -> str | None:
    if source != "slack" or not source_channel_id:
        return None
    normalized = str(source_channel_id).strip()
    if not normalized:
        return None
    return normalized.split(":", 1)[0].strip().upper() or None


def _derive_bucket(
    *,
    source: str | None,
    scope: str | None,
    normalized_channel_id: str | None,
) -> tuple[str, str]:
    if scope == "private":
        return ("direct", "direct")
    if source == _WEB_PLATFORM_SLUG:
        return ("direct", "direct")
    if source == "slack" and normalized_channel_id:
        if normalized_channel_id.startswith("D"):
            return ("direct", "direct")
        return ("channel", f"channel:{normalized_channel_id}")
    return ("uncategorized", "uncategorized")


def _encode_cursor(updated_at: datetime, conversation_id: UUID) -> str:
    payload = {"updated_at": updated_at.isoformat(), "conversation_id": str(conversation_id)}
    return base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("utf-8")


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor.encode("utf-8")).decode("utf-8"))
        updated_at = datetime.fromisoformat(str(payload["updated_at"]))
        conv_id = UUID(str(payload["conversation_id"]))
        return updated_at, conv_id
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid cursor: {exc}") from exc


def _channel_cache_key(workspace_id: str, channel_id: str) -> str:
    return f"chat:slack_channel_name:{workspace_id}:{channel_id}"


async def _get_workspace_id_for_org(session: AsyncSession, org_id: str | None) -> str | None:
    if not org_id:
        return None

    cache_key = f"chat:slack_workspace_id:{org_id}"
    try:
        r = await _get_redis()
        cached = await r.get(cache_key)
        if cached:
            return str(cached)
    except Exception:
        pass

    result = await session.execute(
        select(Integration)
        .where(Integration.organization_id == UUID(org_id))
        .where(Integration.connector == "slack")
        .where(Integration.is_active == True)  # noqa: E712
        .order_by(Integration.updated_at.desc().nullslast(), Integration.created_at.desc().nullslast())
        .limit(1)
    )
    integration = result.scalar_one_or_none()
    if not integration:
        return None
    extra = integration.extra_data or {}
    workspace_id = str(extra.get("team_id") or extra.get("workspace_id") or "").strip() or None
    if workspace_id:
        try:
            r = await _get_redis()
            await r.set(cache_key, workspace_id, ex=_SLACK_WORKSPACE_ID_TTL)
        except Exception:
            pass
    return workspace_id


async def _refresh_slack_channel_name(
    workspace_id: str,
    channel_id: str,
    org_id: str,
    existing_fetched_at_iso: str | None = None,
) -> None:
    lock_key = f"chat:slack_channel_name_refresh_lock:{workspace_id}:{channel_id}"
    cache_key = _channel_cache_key(workspace_id, channel_id)
    try:
        r = await _get_redis()
    except Exception:
        return

    try:
        lock_acquired = await r.set(
            lock_key, "1", ex=_CHANNEL_NAME_SINGLE_FLIGHT_LOCK_SECONDS, nx=True
        )
    except Exception:
        return

    if not lock_acquired:
        return
    try:
        connector = SlackConnector(organization_id=org_id, team_id=workspace_id)
        started_at = datetime.utcnow()
        info = await connector.get_channel_info(channel_id)
        name = str((info or {}).get("name") or "").strip() or None
        if not name:
            return
        fetched_at = datetime.utcnow()
        payload = {
            "name": name,
            "fetched_at": fetched_at.isoformat(),
            "source": "slack",
        }
        current = await r.get(cache_key)
        if current:
            try:
                cur_payload = json.loads(current)
                cur_fetched = str(cur_payload.get("fetched_at") or "")
                if existing_fetched_at_iso and cur_fetched and cur_fetched > existing_fetched_at_iso:
                    return
            except Exception:
                pass
        ttl_seconds = int(_CHANNEL_NAME_HARD_TTL_SECONDS * (1 + _CHANNEL_NAME_TTL_JITTER_RATIO))
        await r.set(cache_key, json.dumps(payload), ex=ttl_seconds)
        logger.info(
            "[chat.resolver] refreshed workspace=%s channel=%s latency_ms=%d",
            workspace_id,
            channel_id,
            int((datetime.utcnow() - started_at).total_seconds() * 1000),
        )
    except Exception as exc:
        logger.warning(
            "[chat.resolver] refresh failed workspace=%s channel=%s error=%s",
            workspace_id,
            channel_id,
            exc,
        )
    finally:
        try:
            await r.delete(lock_key)
        except Exception:
            pass


async def _resolve_slack_channel_name(
    org_id: str | None,
    workspace_id: str | None,
    channel_id: str | None,
) -> str | None:
    if not org_id or not workspace_id or not channel_id:
        return None

    cache_key = _channel_cache_key(workspace_id, channel_id)
    now = datetime.utcnow()
    try:
        r = await _get_redis()
    except Exception:
        return None
    try:
        cached = await r.get(cache_key)
    except Exception:
        cached = None
    if cached:
        try:
            payload = json.loads(cached)
            cached_name = str(payload.get("name") or "").strip() or None
            fetched_at_iso = str(payload.get("fetched_at") or "").strip()
            fetched_at = datetime.fromisoformat(fetched_at_iso) if fetched_at_iso else None
            if cached_name and fetched_at:
                age_seconds = (now - fetched_at).total_seconds()
                if age_seconds <= _CHANNEL_NAME_SOFT_TTL_SECONDS:
                    return cached_name
                if age_seconds <= _CHANNEL_NAME_HARD_TTL_SECONDS:
                    task_key = f"{workspace_id}:{channel_id}"
                    if task_key not in _slack_channel_refresh_tasks or _slack_channel_refresh_tasks[task_key].done():
                        _register_slack_refresh_task(
                            task_key,
                            asyncio.create_task(
                                _refresh_slack_channel_name(
                                    workspace_id, channel_id, org_id, fetched_at_iso
                                ),
                            ),
                        )
                    return cached_name
        except Exception:
            pass

    # Blocking fallback path.
    await _refresh_slack_channel_name(workspace_id, channel_id, org_id)
    try:
        latest = await r.get(cache_key)
    except Exception:
        latest = None
    if latest:
        try:
            payload = json.loads(latest)
            return str(payload.get("name") or "").strip() or None
        except Exception:
            return None
    return None


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
    agent_responding: bool = True
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
    cursor: Optional[str] = None,
    scope: Optional[str] = None,
    mine: bool = False,
    search: Optional[str] = None,
) -> ConversationListResponse:
    """List conversations for the authenticated user, ordered by most recent.

    Args:
        scope: Optional filter - "shared" or "private". If not provided, returns all.
        mine: If true, only return conversations created by the current user.
        search: Optional text search across title, summary, preview, and message content.
    """
    org_id = auth.organization_id_str
    normalized_search = (search or "").strip()

    async with get_session(organization_id=org_id, user_id=auth.user_id) as session:
        # Fast path: query without Slack filter first
        query = (
            select(Conversation)
            .where(Conversation.type != "workflow")
            .where(_build_conversation_access_filter(auth))
        )

        if scope in ("shared", "private"):
            query = query.where(Conversation.scope == scope)

        if mine and auth.user_id:
            query = query.where(Conversation.user_id == auth.user_id)

        if normalized_search:
            search_term = f"%{normalized_search}%"
            # Search conversation title, summary, preview, AND message text content.
            # For content_blocks, only search inside text blocks (type='text') to
            # avoid matching tool call metadata, JSON keys, SQL queries, etc.
            from sqlalchemy import text as sa_text
            message_match_subq = sa_text(
                "SELECT DISTINCT conversation_id FROM chat_messages "
                "WHERE content ILIKE :st "
                "UNION "
                "SELECT DISTINCT conversation_id FROM chat_messages, "
                "jsonb_array_elements(content_blocks) AS block "
                "WHERE block->>'type' = 'text' "
                "AND block->>'text' ILIKE :st"
            ).bindparams(st=search_term).columns(column("conversation_id"))
            query = query.where(
                or_(
                    Conversation.title.ilike(search_term),
                    Conversation.summary.ilike(search_term),
                    Conversation.last_message_preview.ilike(search_term),
                    Conversation.id.in_(message_match_subq),
                )
            )

        limit = max(1, min(limit, 200))
        cursor_updated_at = None
        cursor_conversation_id = None
        if cursor:
            cursor_updated_at, cursor_conversation_id = _decode_cursor(cursor)
            query = query.where(
                or_(
                    Conversation.updated_at < cursor_updated_at,
                    and_(
                        Conversation.updated_at == cursor_updated_at,
                        Conversation.id < cursor_conversation_id,
                    ),
                )
            )

        ordered_query = (
            query.order_by(Conversation.updated_at.desc(), Conversation.id.desc())
            .offset(offset if not cursor else 0)
            .limit(limit + 1)
        )
        result = await session.execute(ordered_query)
        primary_rows: list[Conversation] = list(result.scalars().all())
        primary_has_more = len(primary_rows) > limit
        has_more = primary_has_more
        conversations: list[Conversation] = primary_rows[:limit]

        # Optimistically preserve has_more from the primary query so the caller
        # can keep pagination controls enabled immediately while we reconcile
        # Slack-only rows that may need to be merged into the visible page.
        slack_user_ids = await _get_slack_user_ids(auth, session=session)
        # Slack-only rows must be considered before advancing cursor pagination.
        # For non-cursor requests, we only reconcile on the first page (offset=0)
        # to avoid reordering/duplication issues on legacy offset pages. Older
        # clients should refresh to cursor-based pagination for fully consistent
        # Slack-aware paging across pages.
        should_merge_slack = bool(slack_user_ids) and (
            cursor is not None or offset == 0
        )

        if should_merge_slack:
            logger.info(
                "[chat] Listing conversations for user=%s with Slack IDs %s",
                auth.user_id_str,
                sorted(slack_user_ids),
            )
            seen_ids = {c.id for c in conversations}
            slack_query = (
                select(Conversation)
                .where(Conversation.type != "workflow")
                .where(Conversation.source == "slack")
                .where(Conversation.source_user_id.in_(slack_user_ids))
            )
            if auth.organization_id:
                slack_query = slack_query.where(
                    Conversation.organization_id == auth.organization_id
                )
            if scope in ("shared", "private"):
                slack_query = slack_query.where(Conversation.scope == scope)
            if cursor_updated_at is not None and cursor_conversation_id is not None:
                slack_query = slack_query.where(
                    or_(
                        Conversation.updated_at < cursor_updated_at,
                        and_(
                            Conversation.updated_at == cursor_updated_at,
                            Conversation.id < cursor_conversation_id,
                        ),
                    )
                )

            if cursor and has_more and conversations:
                page_tail = conversations[-1]
                slack_query = slack_query.where(
                    or_(
                        Conversation.updated_at > page_tail.updated_at,
                        and_(
                            Conversation.updated_at == page_tail.updated_at,
                            Conversation.id > page_tail.id,
                        ),
                    )
                )

            # Apply same search filter to Slack fallback
            if normalized_search:
                slack_search = f"%{normalized_search}%"
                slack_msg_subq = sa_text(
                    "SELECT DISTINCT conversation_id FROM chat_messages "
                    "WHERE content ILIKE :st "
                    "UNION "
                    "SELECT DISTINCT conversation_id FROM chat_messages, "
                    "jsonb_array_elements(content_blocks) AS block "
                    "WHERE block->>'type' = 'text' "
                    "AND block->>'text' ILIKE :st"
                ).bindparams(st=slack_search).columns(column("conversation_id"))
                slack_query = slack_query.where(
                    or_(
                        Conversation.title.ilike(slack_search),
                        Conversation.summary.ilike(slack_search),
                        Conversation.last_message_preview.ilike(slack_search),
                        Conversation.id.in_(slack_msg_subq),
                    )
                )

            slack_result = await session.execute(
                slack_query.order_by(
                    Conversation.updated_at.desc(), Conversation.id.desc()
                ).limit(limit + 1)
            )
            for conv in slack_result.scalars().all():
                if conv.id not in seen_ids:
                    conversations.append(conv)
                    seen_ids.add(conv.id)

            conversations.sort(
                key=lambda c: (c.updated_at, c.id),
                reverse=True,
            )

            has_more = primary_has_more or len(conversations) > limit
            if has_more:
                conversations = conversations[:limit]
        next_cursor = None
        if conversations and has_more:
            last = conversations[-1]
            next_cursor = _encode_cursor(last.updated_at, last.id)

        # Collect all participant user IDs to fetch in one query
        all_participant_ids: set[UUID] = set()
        for conv in conversations:
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

        # When searching, fetch a matching snippet and count per conversation
        snippet_by_conv_id: dict[UUID, str] = {}
        count_by_conv_id: dict[UUID, int] = {}
        if normalized_search and conversations:
            search_lower = normalized_search.lower()
            conv_ids = [c.id for c in conversations]
            snippet_result = await session.execute(
                select(ChatMessage.conversation_id, ChatMessage.content, ChatMessage.content_blocks)
                .where(ChatMessage.conversation_id.in_(conv_ids))
                .order_by(ChatMessage.created_at.desc())
            )
            for row in snippet_result:
                cid = row.conversation_id
                # Collect all text: legacy content + all text blocks
                candidates: list[str] = []
                if row.content:
                    candidates.append(row.content)
                if row.content_blocks:
                    for block in (row.content_blocks or []):
                        if isinstance(block, dict) and block.get("type") == "text":
                            candidates.append(block.get("text", ""))
                # Count occurrences across all text in this message
                for text_content in candidates:
                    occurrences = text_content.lower().count(search_lower)
                    if occurrences > 0:
                        count_by_conv_id[cid] = count_by_conv_id.get(cid, 0) + occurrences
                # Extract snippet from first match (once per conversation)
                if cid not in snippet_by_conv_id:
                    for text_content in candidates:
                        idx = text_content.lower().find(search_lower)
                        if idx >= 0:
                            start = max(0, idx - 40)
                            end = min(len(text_content), idx + len(search_lower) + 40)
                            snippet = text_content[start:end].strip()
                            if start > 0:
                                snippet = "..." + snippet
                            if end < len(text_content):
                                snippet = snippet + "..."
                            snippet_by_conv_id[cid] = snippet
                            break

        # Build response using cached fields
        workspace_id = await _get_workspace_id_for_org(session, org_id)
        response_items: list[ConversationResponse] = []
        for conv in conversations:
            normalized_channel_id = _normalize_channel_id(conv.source, conv.source_channel_id)
            bucket_type, bucket_key = _derive_bucket(
                source=conv.source,
                scope=conv.scope,
                normalized_channel_id=normalized_channel_id,
            )
            resolved_channel_name = await _resolve_slack_channel_name(
                org_id=org_id,
                workspace_id=workspace_id if conv.source == "slack" else None,
                channel_id=normalized_channel_id,
            ) if bucket_type == "channel" else None
            if bucket_type == "channel" and not resolved_channel_name:
                bucket_type = "uncategorized"
                bucket_key = "uncategorized"

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

            # Build participants list (exclude guest/system accounts)
            participants: list[ParticipantResponse] = []
            for uid in (conv.participating_user_ids or []):
                user = participants_by_id.get(uid)
                if user and not getattr(user, "is_guest", False):
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
                agent_responding=getattr(conv, "agent_responding", True),
                participants=participants,
                match_snippet=snippet_by_conv_id.get(conv.id),
                match_count=count_by_conv_id.get(conv.id, 0),
                workspace_id=workspace_id if conv.source == "slack" else None,
                source=conv.source,
                source_channel_id=conv.source_channel_id,
                normalized_channel_id=normalized_channel_id,
                resolved_channel_name=resolved_channel_name,
                group_bucket_type=bucket_type,
                group_bucket_key=bucket_key,
            ))

        return ConversationListResponse(
            conversations=response_items,
            total=len(response_items),
            search_term=normalized_search if normalized_search else None,
            next_cursor=next_cursor,
            has_more=has_more,
            server_time=f"{datetime.utcnow().isoformat()}Z",
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

    async with get_session(organization_id=org_id, user_id=auth.user_id) as session:
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
            agent_responding=True,
            participants=participants,
        )


@router.get("/conversations/{conversation_id}", response_model=ConversationDetailResponse)
async def get_conversation(
    conversation_id: str,
    auth: AuthContext = Depends(get_current_auth),
    limit: int = 15,
    before: Optional[str] = None,
) -> ConversationDetailResponse:
    """Get a conversation with its messages (paginated).

    Args:
        limit: Number of messages to return (default 15).
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

    async with get_session(organization_id=org_id, user_id=auth.user_id) as session:
        # Fast path: try without Slack lookup (covers web chats + shared org chats)
        result = await session.execute(
            select(Conversation)
            .where(Conversation.id == conv_uuid)
            .where(_build_conversation_access_filter(auth))
        )
        conversation = result.scalar_one_or_none()

        # Slow path: conversation not found — may be a Slack DM visible only via source_user_id
        if not conversation:
            slack_user_ids = await _get_slack_user_ids(auth, session=session)
            if slack_user_ids:
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

        # Fetch participants (exclude guest/system accounts)
        participants: list[ParticipantResponse] = []
        if conversation.participating_user_ids:
            users_result = await session.execute(
                select(User).where(User.id.in_(conversation.participating_user_ids))
            )
            for user in users_result.scalars().all():
                if not getattr(user, "is_guest", False):
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
            agent_responding=getattr(conversation, "agent_responding", True),
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

    async with get_session(organization_id=org_id, user_id=auth.user_id) as session:
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

        # Fetch participants (exclude guest/system accounts)
        participants: list[ParticipantResponse] = []
        if conv_participant_ids:
            users_result = await session.execute(
                select(User).where(User.id.in_(conv_participant_ids))
            )
            for user in users_result.scalars().all():
                if not getattr(user, "is_guest", False):
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
            agent_responding=getattr(conversation, "agent_responding", True),
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

    async with get_session(organization_id=org_id, user_id=auth.user_id) as session:
        result = await session.execute(
            select(Conversation)
            .where(Conversation.id == conv_uuid)
        )
        conversation = result.scalar_one_or_none()

        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found")

        is_owner = conversation.user_id == auth.user_id
        is_admin = False
        if not is_owner:
            async with get_admin_session() as admin_session:
                membership = (
                    await admin_session.execute(
                        select(OrgMember).where(
                            OrgMember.user_id == auth.user_id,
                            OrgMember.organization_id == auth.organization_id,
                            OrgMember.role == "admin",
                            OrgMember.status.in_(("active", "onboarding", "invited")),
                        )
                    )
                ).scalar_one_or_none()
                is_admin = membership is not None or auth.is_global_admin

        if not is_owner and not is_admin:
            raise HTTPException(status_code=403, detail="Only the conversation creator or an org admin can delete it")

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
    """Add a participant to a conversation (shared or private)."""
    try:
        conv_uuid = UUID(conversation_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid conversation ID format")

    if not request.user_id and not request.email:
        raise HTTPException(status_code=400, detail="Must provide user_id or email")

    org_id = auth.organization_id_str

    async with get_session(organization_id=org_id, user_id=auth.user_id) as session:
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

        # Match team roster: membership in the conversation's org (not users.guest_organization_id alone)
        conv_org_id = conversation.organization_id
        if conv_org_id is None:
            raise HTTPException(status_code=400, detail="Conversation has no organization")
        membership_row = await session.execute(
            select(OrgMember.id).where(
                OrgMember.user_id == target_user.id,
                OrgMember.organization_id == conv_org_id,
                OrgMember.status.in_(("active", "onboarding", "invited")),
            )
        )
        if membership_row.scalar_one_or_none() is None and not (
            getattr(target_user, "is_guest", False)
            and target_user.guest_organization_id == conv_org_id
        ):
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
    """Remove a participant from a conversation (shared or private)."""
    try:
        conv_uuid = UUID(conversation_id)
        target_user_uuid = UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ID format")

    org_id = auth.organization_id_str

    async with get_session(organization_id=org_id, user_id=auth.user_id) as session:
        slack_user_ids = await _get_slack_user_ids(auth, session=session)
        result = await session.execute(
            select(Conversation)
            .where(Conversation.id == conv_uuid)
            .where(_build_conversation_access_filter(auth, slack_user_ids))
        )
        conversation = result.scalar_one_or_none()

        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found")

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

    async with get_session(organization_id=org_id, user_id=auth.user_id) as session:
        slack_user_ids = await _get_slack_user_ids(auth, session=session)
        result = await session.execute(
            select(Conversation)
            .where(Conversation.id == conv_uuid)
            .where(_build_conversation_access_filter(auth, slack_user_ids))
        )
        conversation = result.scalar_one_or_none()

        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found")

        # Only the conversation creator may change visibility (private ↔ shared)
        if conversation.user_id is None or str(conversation.user_id) != str(auth.user_id):
            raise HTTPException(
                status_code=403,
                detail="Only the chat creator can change conversation visibility",
            )

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

        # Fetch participants (exclude guest/system accounts)
        participants: list[ParticipantResponse] = []
        if conv_participant_ids:
            users_result = await session.execute(
                select(User).where(User.id.in_(conv_participant_ids))
            )
            for user in users_result.scalars().all():
                if not getattr(user, "is_guest", False):
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
            agent_responding=getattr(conversation, "agent_responding", True),
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

    async with get_session(organization_id=org_id, user_id=auth.user_id) as session:
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

    async with get_session(organization_id=org_id, user_id=auth.user_id) as session:
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
        was_success = False
        failure_reason: str | None = None
        try:
            async for chunk in orchestrator.process_message(request.content):
                response_content += chunk
            was_success = True
        except Exception as exc:
            failure_reason = str(exc)
            logger.exception(
                "send_message turn processing failed conversation_id=%s user_id=%s",
                conv_uuid,
                auth.user_id,
            )
            raise HTTPException(
                status_code=500,
                detail=f"Failed to process message: {exc}",
            ) from exc
        finally:
            await _record_web_query_outcome(
                was_success=was_success,
                failure_reason=failure_reason,
                conversation_id=str(conv_uuid) if conv_uuid else None,
                user_id=auth.user_id_str,
            )

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


@router.get("/attachments/{attachment_id}", response_class=Response)
async def get_chat_attachment(
    attachment_id: str,
    auth: AuthContext = Depends(get_current_auth),
) -> Response:
    """
    Get a chat message attachment by ID (file bytes).

    Returns the file with Content-Type and Content-Disposition.
    Access is restricted to users who can see the conversation.
    """
    try:
        attachment_uuid = UUID(attachment_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid attachment ID format")

    org_id: str | None = auth.organization_id_str
    if not org_id:
        raise HTTPException(status_code=403, detail="Organization context required")

    async with get_session(organization_id=org_id, user_id=auth.user_id) as session:
        stmt = (
            select(ChatAttachment)
            .join(Conversation, ChatAttachment.conversation_id == Conversation.id)
            .where(ChatAttachment.id == attachment_uuid)
            .where(_build_conversation_access_filter(auth))
        )
        result = await session.execute(stmt)
        attachment: ChatAttachment | None = result.scalar_one_or_none()

        if not attachment:
            slack_user_ids = await _get_slack_user_ids(auth, session=session)
            if slack_user_ids:
                stmt_slack = (
                    select(ChatAttachment)
                    .join(Conversation, ChatAttachment.conversation_id == Conversation.id)
                    .where(ChatAttachment.id == attachment_uuid)
                    .where(_build_conversation_access_filter(auth, slack_user_ids))
                )
                result_slack = await session.execute(stmt_slack)
                attachment = result_slack.scalar_one_or_none()

        if not attachment:
            raise HTTPException(status_code=404, detail="Attachment not found")

        safe_filename: str = attachment.filename.replace('"', "%22")
        return Response(
            content=attachment.content,
            media_type=attachment.mime_type,
            headers={
                "Content-Disposition": f'inline; filename="{safe_filename}"',
            },
        )
