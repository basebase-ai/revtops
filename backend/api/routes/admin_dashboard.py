"""
Admin dashboard routes for global admin analytics.

Endpoints:
- GET /api/admin-dashboard/credit-usage  — Credit usage by org per day (past 7 days)
- GET /api/admin-dashboard/top-conversations — Most active conversations for top customers
"""
from __future__ import annotations

import logging
from datetime import date, timedelta, timezone, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import cast, Date, desc, func, select

from api.auth_middleware import AuthContext, require_global_admin
from models.conversation import Conversation
from models.credit_transaction import CreditTransaction
from models.organization import Organization
from models.user import User
from models.database import get_admin_session

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/credit-usage")
async def get_credit_usage(
    auth: AuthContext = Depends(require_global_admin),
) -> dict[str, Any]:
    """
    Return daily credit consumption per org for the past 7 days.

    Only negative-amount transactions (deductions) are counted.
    """
    today: date = datetime.now(timezone.utc).date()
    start_date: date = today - timedelta(days=6)

    async with get_admin_session() as session:
        rows = (
            await session.execute(
                select(
                    CreditTransaction.organization_id,
                    Organization.name.label("org_name"),
                    cast(CreditTransaction.created_at, Date).label("day"),
                    func.sum(func.abs(CreditTransaction.amount)).label("total"),
                )
                .join(Organization, Organization.id == CreditTransaction.organization_id)
                .where(
                    CreditTransaction.amount < 0,
                    cast(CreditTransaction.created_at, Date) >= start_date,
                )
                .group_by(
                    CreditTransaction.organization_id,
                    Organization.name,
                    cast(CreditTransaction.created_at, Date),
                )
            )
        ).all()

    days: list[str] = [
        (start_date + timedelta(days=i)).isoformat() for i in range(7)
    ]
    day_set: set[str] = set(days)

    org_map: dict[str, dict[str, Any]] = {}
    for row in rows:
        oid: str = str(row.organization_id)
        day_str: str = row.day.isoformat()
        if day_str not in day_set:
            continue
        if oid not in org_map:
            org_map[oid] = {"org_id": oid, "org_name": row.org_name, "by_day": {}}
        org_map[oid]["by_day"][day_str] = int(row.total)

    series: list[dict[str, Any]] = []
    for entry in sorted(org_map.values(), key=lambda e: sum(e["by_day"].values()), reverse=True):
        values: list[int] = [entry["by_day"].get(d, 0) for d in days]
        series.append({"org_id": entry["org_id"], "org_name": entry["org_name"], "values": values})

    return {"days": days, "series": series}


@router.get("/top-conversations")
async def get_top_conversations(
    auth: AuthContext = Depends(require_global_admin),
) -> dict[str, Any]:
    """
    Return the 10 most recent conversations for each org that used credits in the past 7 days.
    """
    today: date = datetime.now(timezone.utc).date()
    start_date: date = today - timedelta(days=6)

    async with get_admin_session() as session:
        top_org_rows = (
            await session.execute(
                select(
                    CreditTransaction.organization_id,
                    Organization.name.label("org_name"),
                    func.sum(func.abs(CreditTransaction.amount)).label("total"),
                )
                .join(Organization, Organization.id == CreditTransaction.organization_id)
                .where(
                    CreditTransaction.amount < 0,
                    cast(CreditTransaction.created_at, Date) >= start_date,
                )
                .group_by(CreditTransaction.organization_id, Organization.name)
                .order_by(desc("total"))
                .limit(10)
            )
        ).all()

        top_org_ids: list[UUID] = [r.organization_id for r in top_org_rows]
        org_name_map: dict[str, str] = {str(r.organization_id): r.org_name for r in top_org_rows}

        if not top_org_ids:
            return {"organizations": []}

        conv_rows = (
            await session.execute(
                select(
                    Conversation.id,
                    Conversation.organization_id,
                    Conversation.user_id,
                    Conversation.title,
                    Conversation.summary,
                    Conversation.last_message_preview,
                    Conversation.message_count,
                    Conversation.source,
                    Conversation.scope,
                    Conversation.updated_at,
                    Conversation.participating_user_ids,
                )
                .where(
                    Conversation.organization_id.in_(top_org_ids),
                )
                .order_by(desc(Conversation.updated_at))
            )
        ).all()

        all_user_ids: set[UUID] = set()
        for c in conv_rows:
            if c.user_id:
                all_user_ids.add(c.user_id)
            if c.participating_user_ids:
                all_user_ids.update(c.participating_user_ids)

        user_name_map: dict[str, str] = {}
        if all_user_ids:
            user_rows = (
                await session.execute(
                    select(User.id, User.name).where(User.id.in_(list(all_user_ids)))
                )
            ).all()
            user_name_map = {str(r.id): r.name for r in user_rows if r.name}

    org_convs: dict[str, list[dict[str, Any]]] = {str(oid): [] for oid in top_org_ids}
    for c in conv_rows:
        oid: str = str(c.organization_id)
        if oid in org_convs and len(org_convs[oid]) < 10:
            summary_text: str | None = (c.summary or "").strip() or None
            if not summary_text and c.last_message_preview:
                summary_text = c.last_message_preview

            participant_names: list[str] = []
            participant_ids: list[UUID] = c.participating_user_ids or []
            if not participant_ids and c.user_id:
                participant_ids = [c.user_id]
            for uid in participant_ids:
                name: str | None = user_name_map.get(str(uid))
                if name:
                    participant_names.append(name)

            org_convs[oid].append({
                "id": str(c.id),
                "title": c.title or "Untitled",
                "summary": summary_text,
                "message_count": c.message_count,
                "source": c.source,
                "scope": c.scope,
                "updated_at": c.updated_at.isoformat() + "Z" if c.updated_at else None,
                "participant_names": participant_names,
            })

    organizations: list[dict[str, Any]] = []
    for r in top_org_rows:
        oid = str(r.organization_id)
        organizations.append({
            "org_id": oid,
            "org_name": org_name_map[oid],
            "total_credits_used": int(r.total),
            "conversations": org_convs.get(oid, []),
        })

    return {"organizations": organizations}
