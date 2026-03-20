"""
Workstreams API for semantic Home: clusters of shared conversations by topic.
"""

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from api.auth_middleware import AuthContext, get_current_auth, require_organization
from api.websockets import sync_broadcaster
from models.chat_message import ChatMessage
from models.conversation import Conversation
from models.database import get_session
from models.user import User
from models.workstream import Workstream
from models.workstream_snapshot import WorkstreamSnapshot
from services.workstream_clustering import compute_workstream_clusters

router = APIRouter()

_STALE_MINUTES = 60  # Cache workstream result; slow recompute only when stale or after embedding updates


class WorkstreamParticipant(BaseModel):
    id: str
    name: str | None
    avatar_url: str | None
    message_count_in_window: int = 0


class WorkstreamConversation(BaseModel):
    id: str
    title: str | None
    message_count: int
    messages_in_window: int
    last_message_at: str
    participants: list[WorkstreamParticipant]
    position: list[float] | None = None


class WorkstreamItem(BaseModel):
    id: str
    label: str
    description: str
    position: list[float]
    conversations: list[WorkstreamConversation]


class WorkstreamsResponse(BaseModel):
    workstreams: list[WorkstreamItem]
    unclustered: list[WorkstreamConversation]
    computed_at: str


def _computed_at_old(snapshot: WorkstreamSnapshot, stale_minutes: int) -> bool:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=stale_minutes)
    at = snapshot.computed_at
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return at < cutoff


@router.get("", response_model=WorkstreamsResponse)
async def get_workstreams(
    auth: AuthContext = Depends(require_organization),
    window: int = Query(24, ge=1, le=168),
) -> WorkstreamsResponse:
    """Get workstream clusters for the org. Recomputed if snapshot is stale or missing."""
    org_id: str = auth.organization_id_str or ""
    window_hours = window
    now: datetime = datetime.now(timezone.utc)

    # Do not hold a DB session across compute_workstream_clusters: it can take 60s+ (UMAP + LLM),
    # and pooled connections may be closed as idle before commit (asyncpg: connection is closed).
    snapshot_id: UUID | None = None
    should_recompute: bool = False
    cached_data: dict[str, Any] = {}

    async with get_session(organization_id=org_id) as session:
        result = await session.execute(
            select(WorkstreamSnapshot)
            .where(
                WorkstreamSnapshot.organization_id == UUID(org_id),
                WorkstreamSnapshot.window_hours == window_hours,
            )
        )
        snapshot = result.scalar_one_or_none()

        should_recompute = (
            snapshot is None
            or snapshot.stale_since is not None
            or (snapshot is not None and _computed_at_old(snapshot, _STALE_MINUTES))
        )

        if snapshot is not None:
            snapshot_id = snapshot.id
        if not should_recompute and snapshot is not None:
            raw = snapshot.data
            cached_data = raw if isinstance(raw, dict) else {}

    data: dict[str, Any]
    if should_recompute:
        data = await compute_workstream_clusters(org_id, window_hours=window_hours)
        from uuid import uuid4

        async with get_session(organization_id=org_id) as session:
            if snapshot_id is not None:
                row = await session.get(WorkstreamSnapshot, snapshot_id)
                if row is not None:
                    row.computed_at = now
                    row.stale_since = None
                    row.data = data
                    session.add(row)
                else:
                    new_snap = WorkstreamSnapshot(
                        id=uuid4(),
                        organization_id=UUID(org_id),
                        window_hours=window_hours,
                        computed_at=now,
                        stale_since=None,
                        data=data,
                    )
                    session.add(new_snap)
            else:
                new_snap = WorkstreamSnapshot(
                    id=uuid4(),
                    organization_id=UUID(org_id),
                    window_hours=window_hours,
                    computed_at=now,
                    stale_since=None,
                    data=data,
                )
                session.add(new_snap)
            await session.commit()
    else:
        data = cached_data

    # Enrich with conversation details and participants
    all_conv_ids: list[str] = []
    for ws in data.get("workstreams", []):
        all_conv_ids.extend(ws.get("conversation_ids", []))
    all_conv_ids.extend(data.get("unclustered_ids", []))
    all_conv_ids = list(dict.fromkeys(all_conv_ids))
    positions = data.get("conversation_positions", {})

    # chat_messages.created_at is TIMESTAMP WITHOUT TIME ZONE (naive UTC); asyncpg errors if bound value is tz-aware.
    since: datetime = (now - timedelta(hours=window_hours)).replace(tzinfo=None)
    conv_details: dict[str, WorkstreamConversation] = {}
    convs: dict[str, Conversation] = {}

    async with get_session(organization_id=org_id) as session:
        if all_conv_ids:
            conv_result = await session.execute(
                select(Conversation)
                .where(Conversation.id.in_([UUID(cid) for cid in all_conv_ids]))
            )
            convs = {str(c.id): c for c in conv_result.scalars().all()}

            msg_count_result = await session.execute(
                select(ChatMessage.conversation_id, func.count(ChatMessage.id))
                .where(
                    ChatMessage.conversation_id.in_([UUID(cid) for cid in all_conv_ids]),
                    ChatMessage.created_at >= since,
                )
                .group_by(ChatMessage.conversation_id)
            )
            messages_in_window_by_conv: dict[UUID, int] = dict(msg_count_result.all())

            last_msg_result = await session.execute(
                select(ChatMessage.conversation_id, func.max(ChatMessage.created_at))
                .where(
                    ChatMessage.conversation_id.in_([UUID(cid) for cid in all_conv_ids]),
                )
                .group_by(ChatMessage.conversation_id)
            )
            last_message_at_by_conv: dict[UUID, datetime] = dict(last_msg_result.all())

            participant_ids: set[UUID] = set()
            for c in convs.values():
                if c.user_id:
                    participant_ids.add(c.user_id)
                for uid in c.participating_user_ids or []:
                    participant_ids.add(uid)
            users_result = await session.execute(
                select(User).where(User.id.in_(participant_ids))
            )
            users_by_id = {u.id: u for u in users_result.scalars().all()}

            for cid in all_conv_ids:
                conv = convs.get(cid)
                if not conv:
                    continue
                seen_uids: set[UUID] = set()
                participants: list[WorkstreamParticipant] = []
                # Always include the conversation creator first
                if conv.user_id and conv.user_id not in seen_uids:
                    seen_uids.add(conv.user_id)
                    u = users_by_id.get(conv.user_id)
                    participants.append(
                        WorkstreamParticipant(
                            id=str(conv.user_id),
                            name=u.name if u else None,
                            avatar_url=u.avatar_url if u else None,
                            message_count_in_window=0,
                        )
                    )
                for uid in conv.participating_user_ids or []:
                    if uid in seen_uids:
                        continue
                    seen_uids.add(uid)
                    u = users_by_id.get(uid)
                    participants.append(
                        WorkstreamParticipant(
                            id=str(uid),
                            name=u.name if u else None,
                            avatar_url=u.avatar_url if u else None,
                            message_count_in_window=0,
                        )
                    )
                last_at: datetime | None = last_message_at_by_conv.get(UUID(cid)) or conv.updated_at
                conv_details[cid] = WorkstreamConversation(
                    id=cid,
                    title=conv.title,
                    message_count=conv.message_count,
                    messages_in_window=messages_in_window_by_conv.get(UUID(cid), 0),
                    last_message_at=last_at.isoformat() + "Z" if last_at else "",
                    participants=participants,
                    position=positions.get(cid),
                )
        else:
            convs = {}

    workstreams_out: list[WorkstreamItem] = []
    for ws in data.get("workstreams", []):
        convs_list = [conv_details[cid] for cid in ws.get("conversation_ids", []) if cid in conv_details]
        workstreams_out.append(
            WorkstreamItem(
                id=ws.get("id", ""),
                label=ws.get("label", ""),
                description=ws.get("description", ""),
                position=ws.get("position", [0.5, 0.5]),
                conversations=convs_list,
            )
        )
    unclustered_out = [conv_details[cid] for cid in data.get("unclustered_ids", []) if cid in conv_details]

    return WorkstreamsResponse(
        workstreams=workstreams_out,
        unclustered=unclustered_out,
        computed_at=data.get("computed_at", now.isoformat()),
    )


class WorkstreamRenameBody(BaseModel):
    label: str


@router.patch("/{workstream_id}")
async def rename_workstream(
    workstream_id: UUID,
    body: WorkstreamRenameBody,
    auth: AuthContext = Depends(require_organization),
) -> dict[str, str]:
    """Update workstream label; propagates to all clients via workstreams_stale broadcast."""
    org_id: str = auth.organization_id_str or ""
    label = (body.label or "").strip()
    if not label:
        raise HTTPException(status_code=400, detail="label is required and cannot be empty")

    async with get_session(organization_id=org_id) as session:
        row = await session.get(Workstream, workstream_id)
        if not row or str(row.organization_id) != org_id:
            raise HTTPException(status_code=404, detail="Workstream not found")
        row.label = label
        row.label_overridden = True
        session.add(row)

        # Mark snapshot stale so next GET recomputes and returns updated label
        snap_result = await session.execute(
            select(WorkstreamSnapshot).where(
                WorkstreamSnapshot.organization_id == UUID(org_id),
                WorkstreamSnapshot.window_hours == row.window_hours,
            )
        )
        snap = snap_result.scalar_one_or_none()
        if snap:
            snap.stale_since = datetime.now(timezone.utc)
            session.add(snap)

        await session.commit()

    await sync_broadcaster.broadcast(org_id, "workstreams_stale", {})
    return {"id": str(workstream_id), "label": label}
