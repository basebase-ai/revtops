"""
Daily team digest API — per-member summaries for a calendar day (PT).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, select

from api.auth_middleware import AuthContext, require_organization
from models.daily_digest import DailyDigest
from models.daily_team_summary import DailyTeamSummary
from models.database import get_session
from models.org_member import OrgMember
from models.user import User
from services.daily_digest import digest_date_yesterday_pt, generate_org_digests_for_session

router = APIRouter()


class DigestSummaryJson(BaseModel):
    narrative: str = ""
    highlights: list[Any] = Field(default_factory=list)
    categories: dict[str, Any] = Field(default_factory=dict)


class DigestMemberRow(BaseModel):
    user_id: str
    name: str | None = None
    avatar_url: str | None = None
    digest_date: str
    summary: DigestSummaryJson | None = None
    generated_at: str | None = None
    active_sources: list[str] = Field(default_factory=list)


class DailyDigestsResponse(BaseModel):
    digest_date: str
    team_summary: str | None = None
    members: list[DigestMemberRow]
    all_active_sources: list[str] = Field(default_factory=list)


class DigestDatesResponse(BaseModel):
    dates: list[str]


class GenerateDigestRequest(BaseModel):
    date: str | None = Field(
        default=None,
        description="YYYY-MM-DD in calendar terms for digest; defaults to yesterday PT",
    )


class GenerateDigestResponse(BaseModel):
    status: str
    digest_date: str
    generated: int
    errors: list[str]


def _parse_digest_date(value: str | None) -> date:
    if value is None or not value.strip():
        return digest_date_yesterday_pt()
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid date; use YYYY-MM-DD") from exc


@router.get("", response_model=DailyDigestsResponse)
async def list_daily_digests(
    auth: AuthContext = Depends(require_organization),
    date: str | None = Query(None, description="YYYY-MM-DD; default yesterday PT"),
) -> DailyDigestsResponse:
    """Return one row per active org member; ``summary`` null when no digest stored."""
    org_str: str = auth.organization_id_str or ""
    if not org_str:
        raise HTTPException(status_code=400, detail="Organization required")
    org_uuid: UUID = UUID(org_str)
    target: date = _parse_digest_date(date)

    members_out: list[DigestMemberRow] = []
    async with get_session(organization_id=org_str) as session:
        stmt = (
            select(OrgMember, User, DailyDigest)
            .join(User, User.id == OrgMember.user_id)
            .outerjoin(
                DailyDigest,
                and_(
                    DailyDigest.organization_id == OrgMember.organization_id,
                    DailyDigest.user_id == OrgMember.user_id,
                    DailyDigest.digest_date == target,
                ),
            )
            .where(
                OrgMember.organization_id == org_uuid,
                OrgMember.status == "active",
                User.is_guest.is_(False),
            )
            .order_by(User.name.asc().nulls_last(), User.email.asc().nulls_last())
        )
        result = await session.execute(stmt)
        for row in result.all():
            om: OrgMember = row[0]
            u: User = row[1]
            dd: DailyDigest | None = row[2]
            summary_model: DigestSummaryJson | None = None
            gen_at: str | None = None
            member_sources: list[str] = []
            if dd is not None:
                s: dict[str, Any] = dd.summary if isinstance(dd.summary, dict) else {}
                summary_model = DigestSummaryJson(
                    narrative=str(s.get("narrative", "")),
                    highlights=list(s.get("highlights") or []),
                    categories=dict(s.get("categories") or {}),
                )
                ga: datetime = dd.generated_at
                if ga.tzinfo is None:
                    ga = ga.replace(tzinfo=timezone.utc)
                gen_at = ga.isoformat()
                rd: dict[str, Any] | None = dd.raw_data if isinstance(dd.raw_data, dict) else None
                if rd:
                    member_sources = list(rd.get("active_sources") or [])
            members_out.append(
                DigestMemberRow(
                    user_id=str(om.user_id),
                    name=u.name,
                    avatar_url=u.avatar_url,
                    digest_date=target.isoformat(),
                    summary=summary_model,
                    generated_at=gen_at,
                    active_sources=member_sources,
                )
            )

        team_summary_row = await session.execute(
            select(DailyTeamSummary).where(
                DailyTeamSummary.organization_id == org_uuid,
                DailyTeamSummary.digest_date == target,
            )
        )
        ts: DailyTeamSummary | None = team_summary_row.scalar_one_or_none()
        team_summary_text: str | None = ts.summary_text if ts is not None else None

    all_sources_set: set[str] = set()
    for m in members_out:
        for src in m.active_sources:
            all_sources_set.add(src)

    return DailyDigestsResponse(
        digest_date=target.isoformat(),
        team_summary=team_summary_text,
        members=members_out,
        all_active_sources=sorted(all_sources_set),
    )


@router.get("/dates", response_model=DigestDatesResponse)
async def list_digest_dates(
    auth: AuthContext = Depends(require_organization),
) -> DigestDatesResponse:
    org_str: str = auth.organization_id_str or ""
    if not org_str:
        raise HTTPException(status_code=400, detail="Organization required")
    org_uuid: UUID = UUID(org_str)
    async with get_session(organization_id=org_str) as session:
        stmt = (
            select(DailyDigest.digest_date)
            .where(DailyDigest.organization_id == org_uuid)
            .distinct()
            .order_by(DailyDigest.digest_date.desc())
        )
        rows = await session.execute(stmt)
        dates: list[str] = [r[0].isoformat() for r in rows.all()]
    return DigestDatesResponse(dates=dates)


@router.post("/generate", response_model=GenerateDigestResponse)
async def generate_daily_digests(
    body: GenerateDigestRequest,
    auth: AuthContext = Depends(require_organization),
) -> GenerateDigestResponse:
    """Regenerate digests for all active members (may take minutes; runs in request)."""
    org_str: str = auth.organization_id_str or ""
    if not org_str:
        raise HTTPException(status_code=400, detail="Organization required")
    org_uuid: UUID = UUID(org_str)
    target: date = _parse_digest_date(body.date)
    async with get_session(organization_id=org_str) as session:
        part: dict[str, Any] = await generate_org_digests_for_session(
            session, org_uuid, target
        )
    return GenerateDigestResponse(
        status="ok",
        digest_date=target.isoformat(),
        generated=int(part.get("generated", 0)),
        errors=list(part.get("errors") or []),
    )
