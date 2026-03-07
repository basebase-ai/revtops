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
            )
            .order_by(Memory.updated_at.desc().nullslast(), Memory.created_at.desc().nullslast())
        )
        memories = memory_result.scalars().all()

    return MemoryDashboardResponse(
        memories=[
            MemoryResponse(
                id=str(memory.id),
                entity_type=memory.entity_type,
                category=memory.category,
                content=memory.content,
                created_by_user_id=str(memory.created_by_user_id) if memory.created_by_user_id else None,
                created_at=f"{memory.created_at.isoformat()}Z" if memory.created_at else None,
                updated_at=f"{memory.updated_at.isoformat()}Z" if memory.updated_at else None,
            )
            for memory in memories
        ],
    )


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

    async with get_session(organization_id=organization_id) as session:
        memory = Memory(
            entity_type="user",
            entity_id=user_uuid,
            organization_id=org_uuid,
            category=(request.category.strip() or None) if request.category else None,
            content=content,
            created_by_user_id=user_uuid,
        )
        session.add(memory)
        await session.commit()
        await session.refresh(memory)

    logger.info("[Memories API] Created memory %s for user %s", memory.id, user_id)
    return MemoryResponse(
        id=str(memory.id),
        entity_type=memory.entity_type,
        category=memory.category,
        content=memory.content,
        created_by_user_id=str(memory.created_by_user_id) if memory.created_by_user_id else None,
        created_at=f"{memory.created_at.isoformat()}Z" if memory.created_at else None,
        updated_at=f"{memory.updated_at.isoformat()}Z" if memory.updated_at else None,
    )


@router.patch("/{organization_id}/user/{memory_id}", response_model=MemoryResponse)
async def update_user_memory(organization_id: str, memory_id: str, user_id: str, request: UpdateMemoryRequest) -> MemoryResponse:
    """Update a user-stored memory's content."""
    if not request.content.strip():
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

        memory.content = request.content.strip()
        await session.commit()
        await session.refresh(memory)

        logger.info("[Memories API] Updated memory %s for user %s", memory_id, user_id)
        return MemoryResponse(
            id=str(memory.id),
            entity_type=memory.entity_type,
            category=memory.category,
            content=memory.content,
            created_by_user_id=str(memory.created_by_user_id) if memory.created_by_user_id else None,
            created_at=f"{memory.created_at.isoformat()}Z" if memory.created_at else None,
            updated_at=f"{memory.updated_at.isoformat()}Z" if memory.updated_at else None,
        )


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
