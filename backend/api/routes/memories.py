"""Memory management endpoints for the UI."""
from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import and_, select

from models.database import get_session
from models.memory import Memory

router = APIRouter()
logger = logging.getLogger(__name__)

GLOBAL_COMMAND_CATEGORY = "global_commands"
GLOBAL_COMMAND_CATEGORY_ALIASES = {"global_command", "global_commands"}
GLOBAL_COMMAND_MAX_LENGTH = 800
CHANNEL_PERSONALITY_CATEGORY = "channel_personality"
CHANNEL_PERSONALITY_MAX_LENGTH = GLOBAL_COMMAND_MAX_LENGTH


def normalize_channel_scope_channel_id(source: str, channel_id: str) -> str:
    normalized_source = source.strip().lower()
    normalized_channel_id = channel_id.strip()
    if not normalized_source or not normalized_channel_id:
        raise HTTPException(status_code=400, detail="source and channel_id are required")
    if normalized_source == "slack":
        return normalized_channel_id.split(":", maxsplit=1)[0].strip()
    return normalized_channel_id


def normalize_memory_category(category: str | None) -> str | None:
    if not category:
        return None
    normalized = category.strip().lower()
    if not normalized:
        return None
    if normalized in GLOBAL_COMMAND_CATEGORY_ALIASES:
        return GLOBAL_COMMAND_CATEGORY
    return normalized


def validate_memory_content(content: str, category: str | None) -> None:
    if category == GLOBAL_COMMAND_CATEGORY and len(content) > GLOBAL_COMMAND_MAX_LENGTH:
        logger.warning(
            "[Memories API] Validation failed category=%s length=%s max=%s",
            category,
            len(content),
            GLOBAL_COMMAND_MAX_LENGTH,
        )
        raise HTTPException(
            status_code=400,
            detail=f"Global command memories must be {GLOBAL_COMMAND_MAX_LENGTH} characters or fewer",
        )
    if category == CHANNEL_PERSONALITY_CATEGORY and len(content) > CHANNEL_PERSONALITY_MAX_LENGTH:
        logger.warning(
            "[Memories API] Validation failed category=%s length=%s max=%s",
            category,
            len(content),
            CHANNEL_PERSONALITY_MAX_LENGTH,
        )
        raise HTTPException(
            status_code=400,
            detail=f"Channel personality memories must be {CHANNEL_PERSONALITY_MAX_LENGTH} characters or fewer",
        )


class MemoryResponse(BaseModel):
    id: str
    entity_type: str
    category: str | None
    content: str
    created_by_user_id: str | None
    created_at: str | None
    updated_at: str | None


class MemoryDashboardResponse(BaseModel):
    memories: list[MemoryResponse]


class CreateMemoryRequest(BaseModel):
    content: str = Field(..., min_length=1)
    category: str | None = None


class UpdateMemoryRequest(BaseModel):
    content: str


def _build_memory_response(memory: Memory) -> MemoryResponse:
    """Materialize a response model from a Memory row while session is still active."""
    return MemoryResponse(
        id=str(memory.id),
        entity_type=memory.entity_type,
        category=memory.category,
        content=memory.content,
        created_by_user_id=str(memory.created_by_user_id) if memory.created_by_user_id else None,
        created_at=f"{memory.created_at.isoformat()}Z" if memory.created_at else None,
        updated_at=f"{memory.updated_at.isoformat()}Z" if memory.updated_at else None,
    )


@router.get("/{organization_id}", response_model=MemoryDashboardResponse)
async def list_memories(organization_id: str, user_id: str) -> MemoryDashboardResponse:
    """Return user-stored memories for an org/user pair."""
    try:
        org_uuid = UUID(organization_id)
        user_uuid = UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ID format")

    async with get_session(organization_id=organization_id) as session:
        memory_result = await session.execute(
            select(Memory)
            .where(
                Memory.organization_id == org_uuid,
                Memory.created_by_user_id == user_uuid,
                Memory.entity_type == "user",
            )
            .order_by(Memory.updated_at.desc().nullslast(), Memory.created_at.desc().nullslast())
        )
        memories = memory_result.scalars().all()

        memory_responses = [_build_memory_response(memory) for memory in memories]

    return MemoryDashboardResponse(memories=memory_responses)


@router.post("/{organization_id}/user", response_model=MemoryResponse)
async def create_user_memory(
    organization_id: str,
    user_id: str,
    request: CreateMemoryRequest,
) -> MemoryResponse:
    """Create a user-stored memory."""
    try:
        org_uuid = UUID(organization_id)
        user_uuid = UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ID format")

    content = request.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="content is required")
    category = normalize_memory_category(request.category)
    validate_memory_content(content, category)

    async with get_session(organization_id=organization_id) as session:
        memory = Memory(
            entity_type="user",
            entity_id=user_uuid,
            organization_id=org_uuid,
            category=category,
            content=content,
            created_by_user_id=user_uuid,
        )
        session.add(memory)
        await session.commit()
        await session.refresh(memory)

        response = _build_memory_response(memory)
        memory_id = str(memory.id)

    logger.info("[Memories API] Created memory %s for user %s", memory_id, user_id)
    return response


@router.patch("/{organization_id}/user/{memory_id}", response_model=MemoryResponse)
async def update_user_memory(organization_id: str, memory_id: str, user_id: str, request: UpdateMemoryRequest) -> MemoryResponse:
    """Update a user-stored memory's content."""
    content = request.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="content is required")

    try:
        org_uuid = UUID(organization_id)
        mem_uuid = UUID(memory_id)
        user_uuid = UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ID format")

    async with get_session(organization_id=organization_id) as session:
        result = await session.execute(
            select(Memory).where(
                and_(
                    Memory.id == mem_uuid,
                    Memory.organization_id == org_uuid,
                    Memory.created_by_user_id == user_uuid,
                )
            )
        )
        memory = result.scalar_one_or_none()
        if not memory:
            raise HTTPException(status_code=404, detail="Memory not found")

        validate_memory_content(content, normalize_memory_category(memory.category))

        memory.content = content
        await session.commit()
        await session.refresh(memory)
        response = _build_memory_response(memory)

        logger.info("[Memories API] Updated memory %s for user %s", memory_id, user_id)
        return response


@router.delete("/{organization_id}/user/{memory_id}")
async def delete_user_memory(organization_id: str, memory_id: str, user_id: str) -> dict[str, str]:
    """Delete a user-stored memory."""
    try:
        org_uuid = UUID(organization_id)
        mem_uuid = UUID(memory_id)
        user_uuid = UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ID format")

    async with get_session(organization_id=organization_id) as session:
        result = await session.execute(
            select(Memory).where(
                and_(
                    Memory.id == mem_uuid,
                    Memory.organization_id == org_uuid,
                    Memory.created_by_user_id == user_uuid,
                )
            )
        )
        memory = result.scalar_one_or_none()
        if not memory:
            raise HTTPException(status_code=404, detail="Memory not found")

        await session.delete(memory)
        await session.commit()

    logger.info("[Memories API] Deleted memory %s for user %s", memory_id, user_id)
    return {"status": "deleted", "memory_id": memory_id}


@router.get("/{organization_id}/channel", response_model=MemoryResponse | None)
async def get_channel_memory(organization_id: str, source: str, channel_id: str) -> MemoryResponse | None:
    """Get channel personality memory by source + normalized channel identifier."""
    try:
        org_uuid = UUID(organization_id)
        normalized_channel_id = normalize_channel_scope_channel_id(source, channel_id)
        normalized_source = source.strip().lower()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ID format")

    async with get_session(organization_id=organization_id) as session:
        result = await session.execute(
            select(Memory)
            .where(
                Memory.organization_id == org_uuid,
                Memory.scope_type == "channel",
                Memory.scope_source == normalized_source,
                Memory.scope_channel_id == normalized_channel_id,
                Memory.category == CHANNEL_PERSONALITY_CATEGORY,
            )
            .order_by(Memory.updated_at.desc().nullslast(), Memory.created_at.desc().nullslast())
            .limit(1)
        )
        memory = result.scalar_one_or_none()
        if not memory:
            logger.info(
                "[Memories API] Channel personality miss org=%s source=%s channel_id=%s",
                organization_id,
                normalized_source,
                normalized_channel_id,
            )
            return None

        logger.info(
            "[Memories API] Channel personality hit org=%s source=%s channel_id=%s memory_id=%s",
            organization_id,
            normalized_source,
            normalized_channel_id,
            memory.id,
        )
        return _build_memory_response(memory)


@router.put("/{organization_id}/channel", response_model=MemoryResponse)
async def upsert_channel_memory(
    organization_id: str,
    source: str,
    channel_id: str,
    request: UpdateMemoryRequest,
) -> MemoryResponse:
    """Create/update channel personality memory by source + channel identifier."""
    content = request.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="content is required")
    validate_memory_content(content, CHANNEL_PERSONALITY_CATEGORY)

    try:
        org_uuid = UUID(organization_id)
        normalized_source = source.strip().lower()
        normalized_channel_id = normalize_channel_scope_channel_id(source, channel_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ID format")

    async with get_session(organization_id=organization_id) as session:
        result = await session.execute(
            select(Memory).where(
                Memory.organization_id == org_uuid,
                Memory.scope_type == "channel",
                Memory.scope_source == normalized_source,
                Memory.scope_channel_id == normalized_channel_id,
                Memory.category == CHANNEL_PERSONALITY_CATEGORY,
            )
        )
        memory = result.scalar_one_or_none()
        if memory:
            memory.content = content
            action = "updated"
        else:
            memory = Memory(
                entity_type="channel",
                entity_id=None,
                organization_id=org_uuid,
                category=CHANNEL_PERSONALITY_CATEGORY,
                content=content,
                scope_type="channel",
                scope_source=normalized_source,
                scope_channel_id=normalized_channel_id,
                created_by_user_id=None,
            )
            session.add(memory)
            action = "created"

        await session.commit()
        await session.refresh(memory)
        logger.info(
            "[Memories API] Channel personality %s org=%s source=%s channel_id=%s memory_id=%s",
            action,
            organization_id,
            normalized_source,
            normalized_channel_id,
            memory.id,
        )
        return _build_memory_response(memory)


@router.delete("/{organization_id}/channel")
async def delete_channel_memory(organization_id: str, source: str, channel_id: str) -> dict[str, str]:
    """Delete channel personality memory by source + channel identifier."""
    try:
        org_uuid = UUID(organization_id)
        normalized_source = source.strip().lower()
        normalized_channel_id = normalize_channel_scope_channel_id(source, channel_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ID format")

    async with get_session(organization_id=organization_id) as session:
        result = await session.execute(
            select(Memory).where(
                Memory.organization_id == org_uuid,
                Memory.scope_type == "channel",
                Memory.scope_source == normalized_source,
                Memory.scope_channel_id == normalized_channel_id,
                Memory.category == CHANNEL_PERSONALITY_CATEGORY,
            )
        )
        memory = result.scalar_one_or_none()
        if not memory:
            logger.info(
                "[Memories API] Channel personality delete miss org=%s source=%s channel_id=%s",
                organization_id,
                normalized_source,
                normalized_channel_id,
            )
            raise HTTPException(status_code=404, detail="Memory not found")

        memory_id = str(memory.id)
        await session.delete(memory)
        await session.commit()

    logger.info(
        "[Memories API] Channel personality deleted org=%s source=%s channel_id=%s memory_id=%s",
        organization_id,
        normalized_source,
        normalized_channel_id,
        memory_id,
    )
    return {"status": "deleted", "memory_id": memory_id}
