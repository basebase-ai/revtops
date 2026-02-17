"""Memory and workflow-note management endpoints for the UI."""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import and_, select

from models.database import get_session
from models.memory import Memory
from models.workflow import Workflow, WorkflowRun

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


class WorkflowNoteResponse(BaseModel):
    note_id: str
    run_id: str
    workflow_id: str
    workflow_name: str | None
    note_index: int
    content: str
    created_by_user_id: str | None
    created_at: str | None
    run_started_at: str | None


class MemoryDashboardResponse(BaseModel):
    memories: list[MemoryResponse]
    workflow_notes: list[WorkflowNoteResponse]


class UpdateMemoryRequest(BaseModel):
    content: str


@router.get("/{organization_id}", response_model=MemoryDashboardResponse)
async def list_memories_and_notes(organization_id: str, user_id: str) -> MemoryDashboardResponse:
    """Return user-stored memories and workflow notes for an org/user pair."""
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

        workflow_result = await session.execute(
            select(WorkflowRun, Workflow)
            .join(Workflow, Workflow.id == WorkflowRun.workflow_id)
            .where(
                WorkflowRun.organization_id == org_uuid,
                Workflow.created_by_user_id == user_uuid,
            )
            .order_by(WorkflowRun.started_at.desc())
        )

        workflow_notes: list[WorkflowNoteResponse] = []
        for run, workflow in workflow_result.all():
            notes: list[dict[str, Any]] = list(run.workflow_notes or [])
            for idx, note in enumerate(notes):
                content = str(note.get("content", "")).strip()
                if not content:
                    continue
                workflow_notes.append(
                    WorkflowNoteResponse(
                        note_id=f"{run.id}:{idx}",
                        run_id=str(run.id),
                        workflow_id=str(workflow.id),
                        workflow_name=workflow.name,
                        note_index=idx,
                        content=content,
                        created_by_user_id=note.get("created_by_user_id"),
                        created_at=note.get("created_at"),
                        run_started_at=f"{run.started_at.isoformat()}Z" if run.started_at else None,
                    )
                )

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
        workflow_notes=workflow_notes,
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


@router.delete("/{organization_id}/workflow-notes/{run_id}/{note_index}")
async def delete_workflow_note(organization_id: str, run_id: str, note_index: int, user_id: str) -> dict[str, str]:
    """Delete one workflow note by run ID and note index."""
    if note_index < 0:
        raise HTTPException(status_code=400, detail="note_index must be >= 0")

    try:
        org_uuid = UUID(organization_id)
        run_uuid = UUID(run_id)
        user_uuid = UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ID format")

    async with get_session(organization_id=organization_id) as session:
        result = await session.execute(
            select(WorkflowRun, Workflow)
            .join(Workflow, Workflow.id == WorkflowRun.workflow_id)
            .where(
                WorkflowRun.id == run_uuid,
                WorkflowRun.organization_id == org_uuid,
                Workflow.created_by_user_id == user_uuid,
            )
        )
        row = result.one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Workflow run not found")

        run, _workflow = row
        notes = list(run.workflow_notes or [])
        if note_index >= len(notes):
            raise HTTPException(status_code=404, detail="Workflow note not found")

        notes.pop(note_index)
        run.workflow_notes = notes
        await session.commit()

    logger.info("[Memories API] Deleted workflow note run=%s index=%s user=%s", run_id, note_index, user_id)
    return {"status": "deleted", "note_id": f"{run_id}:{note_index}"}
