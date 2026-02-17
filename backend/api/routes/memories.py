"""Memory and workflow-note management routes for the left-nav Memories UI."""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import and_, desc, select

from models.database import get_session
from models.memory import Memory
from models.user import User
from models.workflow import Workflow, WorkflowRun

router = APIRouter()
logger = logging.getLogger(__name__)


class UserMemoryResponse(BaseModel):
    id: str
    content: str
    entity_type: str
    category: str | None
    created_by_user_id: str | None
    created_at: str | None
    updated_at: str | None


class WorkflowNoteResponse(BaseModel):
    note_id: str
    run_id: str
    workflow_id: str
    workflow_name: str
    note_index: int
    content: str
    created_at: str | None
    created_by_user_id: str | None


class MemoriesPageResponse(BaseModel):
    agent_global_commands: str | None
    user_memories: list[UserMemoryResponse]
    workflow_notes: list[WorkflowNoteResponse]


class UpdateMemoryRequest(BaseModel):
    content: str = Field(min_length=1)


@router.get("/{organization_id}", response_model=MemoriesPageResponse)
async def list_memories_and_workflow_notes(organization_id: str, user_id: str) -> MemoriesPageResponse:
    """Return user-stored memories and workflow-stored notes for memory management UI."""
    try:
        org_uuid = UUID(organization_id)
        user_uuid = UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ID format")

    async with get_session(organization_id=organization_id) as session:
        user = await session.get(User, user_uuid)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        memory_result = await session.execute(
            select(Memory)
            .where(
                and_(
                    Memory.organization_id == org_uuid,
                    Memory.created_by_user_id == user_uuid,
                )
            )
            .order_by(desc(Memory.updated_at), desc(Memory.created_at))
        )
        memories = memory_result.scalars().all()

        runs_result = await session.execute(
            select(WorkflowRun, Workflow.name)
            .join(Workflow, Workflow.id == WorkflowRun.workflow_id)
            .where(WorkflowRun.organization_id == org_uuid)
            .order_by(desc(WorkflowRun.started_at))
            .limit(200)
        )

        workflow_notes: list[WorkflowNoteResponse] = []
        for run, workflow_name in runs_result.all():
            notes = run.workflow_notes or []
            for idx, note in enumerate(notes):
                if not isinstance(note, dict):
                    continue
                content = str(note.get("content", "")).strip()
                if not content:
                    continue
                workflow_notes.append(
                    WorkflowNoteResponse(
                        note_id=f"{run.id}:{idx}",
                        run_id=str(run.id),
                        workflow_id=str(run.workflow_id),
                        workflow_name=workflow_name,
                        note_index=idx,
                        content=content,
                        created_at=note.get("created_at"),
                        created_by_user_id=note.get("created_by_user_id"),
                    )
                )

        return MemoriesPageResponse(
            agent_global_commands=user.agent_global_commands,
            user_memories=[
                UserMemoryResponse(
                    id=str(memory.id),
                    content=memory.content,
                    entity_type=memory.entity_type,
                    category=memory.category,
                    created_by_user_id=str(memory.created_by_user_id) if memory.created_by_user_id else None,
                    created_at=memory.created_at.isoformat() + "Z" if memory.created_at else None,
                    updated_at=memory.updated_at.isoformat() + "Z" if memory.updated_at else None,
                )
                for memory in memories
            ],
            workflow_notes=workflow_notes,
        )


@router.patch("/{organization_id}/{memory_id}", response_model=UserMemoryResponse)
async def update_memory(organization_id: str, memory_id: str, user_id: str, request: UpdateMemoryRequest) -> UserMemoryResponse:
    """Update a user memory created by this user."""
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

        logger.info("[Memories API] Updated memory %s", memory_id)
        return UserMemoryResponse(
            id=str(memory.id),
            content=memory.content,
            entity_type=memory.entity_type,
            category=memory.category,
            created_by_user_id=str(memory.created_by_user_id) if memory.created_by_user_id else None,
            created_at=memory.created_at.isoformat() + "Z" if memory.created_at else None,
            updated_at=memory.updated_at.isoformat() + "Z" if memory.updated_at else None,
        )


@router.delete("/{organization_id}/{memory_id}")
async def delete_memory(organization_id: str, memory_id: str, user_id: str) -> dict[str, str]:
    """Delete a user memory created by this user."""
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

    logger.info("[Memories API] Deleted memory %s", memory_id)
    return {"status": "deleted", "memory_id": memory_id}


@router.delete("/{organization_id}/workflow-notes/{run_id}/{note_index}")
async def delete_workflow_note(organization_id: str, run_id: str, note_index: int, user_id: str) -> dict[str, Any]:
    """Delete a single workflow note by run + index."""
    _ = user_id  # reserved for future permission checks
    try:
        org_uuid = UUID(organization_id)
        run_uuid = UUID(run_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ID format")

    async with get_session(organization_id=organization_id) as session:
        result = await session.execute(
            select(WorkflowRun).where(
                and_(
                    WorkflowRun.id == run_uuid,
                    WorkflowRun.organization_id == org_uuid,
                )
            )
        )
        run = result.scalar_one_or_none()
        if not run:
            raise HTTPException(status_code=404, detail="Workflow run not found")

        notes = list(run.workflow_notes or [])
        if note_index < 0 or note_index >= len(notes):
            raise HTTPException(status_code=404, detail="Workflow note not found")

        notes.pop(note_index)
        run.workflow_notes = notes
        await session.commit()

    logger.info("[Memories API] Deleted workflow note run=%s index=%s", run_id, note_index)
    return {"status": "deleted", "run_id": run_id, "note_index": note_index}
