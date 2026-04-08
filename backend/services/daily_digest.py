"""
Collect per-member activity for a calendar day (America/Los_Angeles) and summarize with Claude Haiku.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
import uuid
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from anthropic import AsyncAnthropic
from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from config import settings
from models.activity import Activity
from models.daily_digest import DailyDigest
from models.daily_team_summary import DailyTeamSummary
from models.external_identity_mapping import ExternalIdentityMapping
from models.github_commit import GitHubCommit
from models.github_pull_request import GitHubPullRequest
from models.meeting import Meeting
from models.org_member import OrgMember
from models.organization import Organization
from models.shared_file import SharedFile
from models.tracker_issue import TrackerIssue
from models.user import User
from services.anthropic_health import report_anthropic_call_failure, report_anthropic_call_success

logger = logging.getLogger(__name__)

_PT: ZoneInfo = ZoneInfo("America/Los_Angeles")
_MODEL: str = settings.ANTHROPIC_CHEAP_MODEL

_SYSTEM_PROMPT_TEMPLATE: str = (
    "You summarize {name_possessive} work for exactly one calendar day ({date}) "
    "from raw system data. Use their first name (\"{first_name}\") in the narrative, "
    "not \"the team member\". "
    "Refer ONLY to that single date — never say a range like "
    "\"April 5-6\" or \"April 5th and 6th\"; just say \"{date_human}\".\n"
    "Return ONLY valid JSON (no markdown fences) with keys:\n"
    '- "narrative": 1-2 sentences, past tense, human-readable overview.\n'
    '- "highlights": array of short strings (max 8), most important concrete items.\n'
    '- "categories": object with optional keys "code", "issues", "meetings", "slack", '
    '"calendar", "crm", "documents" — each value is an array of short strings (can be empty).\n'
    "IMPORTANT: Only report actions the person actively took — commits, PR reviews, "
    "Slack messages they wrote, meetings they attended, issues they updated, etc. "
    "Never say the person \"received\" something (emails, notifications, digests). "
    "Ignore automated emails, third-party digests, recharge notifications, newsletters, "
    "and other passive/automated items that don't reflect real work. "
    "If after filtering out noise there is NO meaningful activity, the narrative MUST be "
    "something like \"{first_name} didn't have any {org_name}-related activity on {date_human}.\" "
    "and highlights should be an empty array. Do not fabricate a summary from noise.\n"
    "No markdown fences."
)


def digest_date_yesterday_pt(*, now_utc: datetime | None = None) -> date:
    """Calendar 'yesterday' in America/Los_Angeles."""
    now: datetime = now_utc or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now_pt: datetime = now.astimezone(_PT)
    return (now_pt.date() - timedelta(days=1))


def pt_calendar_day_utc_naive_bounds(d: date) -> tuple[datetime, datetime]:
    """Start (inclusive) and end (exclusive) of calendar day *d* in PT, as naive UTC datetimes."""
    start_pt: datetime = datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=_PT)
    end_pt: datetime = start_pt + timedelta(days=1)
    start_utc: datetime = start_pt.astimezone(timezone.utc).replace(tzinfo=None)
    end_utc: datetime = end_pt.astimezone(timezone.utc).replace(tzinfo=None)
    return start_utc, end_utc


def _normalize_email(e: str | None) -> str | None:
    if e is None:
        return None
    stripped: str = e.strip().lower()
    return stripped if stripped else None


async def _load_identity_blobs(
    session: Any,
    organization_id: UUID,
    user_id: UUID,
) -> tuple[list[str], list[str], list[str]]:
    """Slack user IDs, GitHub logins, lowercase emails from mappings + user row."""
    slack_ids: list[str] = []
    github_logins: list[str] = []
    emails: set[str] = set()

    u_result = await session.execute(select(User).where(User.id == user_id))
    user: User | None = u_result.scalar_one_or_none()
    if user and user.email:
        ne: str | None = _normalize_email(user.email)
        if ne:
            emails.add(ne)

    m_result = await session.execute(
        select(ExternalIdentityMapping).where(
            ExternalIdentityMapping.organization_id == organization_id,
            ExternalIdentityMapping.user_id == user_id,
        )
    )
    mappings: list[ExternalIdentityMapping] = list(m_result.scalars().all())
    for m in mappings:
        src: str = (m.source or "").lower()
        if m.external_userid:
            if src == "slack":
                slack_ids.append(str(m.external_userid))
            elif src == "github":
                github_logins.append(str(m.external_userid))
        for em in (m.external_email, m.revtops_email):
            ne2: str | None = _normalize_email(em)
            if ne2:
                emails.add(ne2)

    return slack_ids, github_logins, sorted(emails)


async def collect_member_raw_data(
    session: Any,
    organization_id: UUID,
    user_id: UUID,
    digest_date: date,
) -> dict[str, Any]:
    """Gather structured raw rows for LLM input."""
    start_naive: datetime
    end_naive: datetime
    start_naive, end_naive = pt_calendar_day_utc_naive_bounds(digest_date)

    slack_ids: list[str]
    github_logins: list[str]
    email_list: list[str]
    slack_ids, github_logins, email_list = await _load_identity_blobs(
        session, organization_id, user_id
    )

    u_result = await session.execute(select(User).where(User.id == user_id))
    user_row: User | None = u_result.scalar_one_or_none()
    member_name: str = (user_row.name or "").strip() if user_row else ""

    org_result = await session.execute(
        select(Organization.name).where(Organization.id == organization_id)
    )
    org_name: str = (org_result.scalar_one_or_none() or "").strip()

    raw: dict[str, Any] = {
        "digest_date": digest_date.isoformat(),
        "member_name": member_name,
        "org_name": org_name,
        "slack_user_ids": slack_ids,
        "github_logins": github_logins,
        "emails": email_list,
        "activities": [],
        "meetings": [],
        "tracker_issues": [],
        "github_commits": [],
        "github_pull_requests": [],
        "shared_files": [],
    }

    act_filters: list[Any] = [
        Activity.organization_id == organization_id,
        Activity.activity_date.is_not(None),
        Activity.activity_date >= start_naive,
        Activity.activity_date < end_naive,
    ]
    owner_parts: list[Any] = [
        Activity.owner_user_id == user_id,
        Activity.created_by_id == user_id,
    ]
    if slack_ids:
        slack_or: list[Any] = [
            Activity.custom_fields.contains({"user_id": sid}) for sid in slack_ids
        ]
        owner_parts.append(
            and_(Activity.source_system == "slack", or_(*slack_or)),
        )
    act_stmt = (
        select(Activity)
        .where(and_(*act_filters, or_(*owner_parts)))
        .order_by(Activity.activity_date.desc())
        .limit(200)
    )
    act_rows = await session.execute(act_stmt)
    for a in act_rows.scalars().all():
        raw["activities"].append(
            {
                "source_system": a.source_system,
                "type": a.type,
                "subject": (a.subject or "")[:500],
                "description": (a.description or "")[:500],
                "activity_date": a.activity_date.isoformat() if a.activity_date else None,
            }
        )

    meet_stmt = select(Meeting).where(
        Meeting.organization_id == organization_id,
        Meeting.scheduled_start >= start_naive,
        Meeting.scheduled_start < end_naive,
    ).limit(100)
    meet_rows = await session.execute(meet_stmt)
    email_set: set[str] = set(email_list)
    for m in meet_rows.scalars().all():
        matched: bool = False
        if email_set and m.participants:
            for p in m.participants:
                if not isinstance(p, dict):
                    continue
                em: str | None = _normalize_email(str(p.get("email") or ""))
                if em and em in email_set:
                    matched = True
                    break
        if m.organizer_email:
            oem: str | None = _normalize_email(m.organizer_email)
            if oem and oem in email_set:
                matched = True
        if matched:
            raw["meetings"].append(
                {
                    "title": m.title,
                    "scheduled_start": m.scheduled_start.isoformat() if m.scheduled_start else None,
                    "status": m.status,
                    "summary": (m.summary or "")[:800],
                }
            )

    issue_user_filter: list[Any] = [TrackerIssue.user_id == user_id]
    if email_list:
        issue_user_filter.append(TrackerIssue.assignee_email.in_(email_list))
    issue_stmt = (
        select(TrackerIssue)
        .where(
            TrackerIssue.organization_id == organization_id,
            or_(*issue_user_filter),
            or_(
                and_(
                    TrackerIssue.completed_date.is_not(None),
                    TrackerIssue.completed_date >= start_naive,
                    TrackerIssue.completed_date < end_naive,
                ),
                and_(
                    TrackerIssue.updated_date.is_not(None),
                    TrackerIssue.updated_date >= start_naive,
                    TrackerIssue.updated_date < end_naive,
                    TrackerIssue.state_type == "completed",
                ),
            ),
        )
        .order_by(TrackerIssue.updated_date.desc().nulls_last())
        .limit(80)
    )
    issue_rows = await session.execute(issue_stmt)
    for iss in issue_rows.scalars().all():
        raw["tracker_issues"].append(
            {
                "identifier": iss.identifier,
                "title": iss.title[:300],
                "state_type": iss.state_type,
                "state_name": iss.state_name,
                "url": iss.url,
            }
        )

    gh_user_filter: list[Any] = [GitHubCommit.user_id == user_id]
    if email_list:
        gh_user_filter.append(GitHubCommit.author_email.in_(email_list))
    if github_logins:
        gh_user_filter.append(GitHubCommit.author_login.in_(github_logins))
    gc_stmt = (
        select(GitHubCommit)
        .where(
            GitHubCommit.organization_id == organization_id,
            GitHubCommit.author_date >= start_naive,
            GitHubCommit.author_date < end_naive,
            or_(*gh_user_filter),
        )
        .order_by(GitHubCommit.author_date.desc())
        .limit(80)
    )
    for c in (await session.execute(gc_stmt)).scalars().all():
        raw["github_commits"].append(
            {
                "sha": c.sha[:12],
                "message": (c.message or "")[:400],
                "author_date": c.author_date.isoformat() if c.author_date else None,
                "url": c.url,
            }
        )

    pr_user_filter: list[Any] = [GitHubPullRequest.user_id == user_id]
    if github_logins:
        pr_user_filter.append(GitHubPullRequest.author_login.in_(github_logins))
    pr_date_or: list[Any] = [
        and_(
            GitHubPullRequest.merged_date.is_not(None),
            GitHubPullRequest.merged_date >= start_naive,
            GitHubPullRequest.merged_date < end_naive,
        ),
        and_(
            GitHubPullRequest.created_date >= start_naive,
            GitHubPullRequest.created_date < end_naive,
        ),
    ]
    gpr_stmt = (
        select(GitHubPullRequest)
        .where(
            GitHubPullRequest.organization_id == organization_id,
            or_(*pr_user_filter),
            or_(*pr_date_or),
        )
        .order_by(GitHubPullRequest.updated_date.desc().nulls_last())
        .limit(80)
    )
    for pr in (await session.execute(gpr_stmt)).scalars().all():
        raw["github_pull_requests"].append(
            {
                "number": pr.number,
                "title": pr.title[:300],
                "state": pr.state,
                "merged_date": pr.merged_date.isoformat() if pr.merged_date else None,
                "created_date": pr.created_date.isoformat() if pr.created_date else None,
                "url": pr.url,
            }
        )

    sf_stmt = (
        select(SharedFile)
        .where(
            SharedFile.organization_id == organization_id,
            SharedFile.user_id == user_id,
            or_(
                and_(
                    SharedFile.source_modified_at.is_not(None),
                    SharedFile.source_modified_at >= start_naive,
                    SharedFile.source_modified_at < end_naive,
                ),
                and_(
                    SharedFile.synced_at.is_not(None),
                    SharedFile.synced_at >= start_naive,
                    SharedFile.synced_at < end_naive,
                ),
            ),
        )
        .limit(60)
    )
    for sf in (await session.execute(sf_stmt)).scalars().all():
        raw["shared_files"].append(
            {
                "name": sf.name[:300],
                "source": sf.source,
                "mime_type": sf.mime_type,
            }
        )

    # Track which sources contributed data
    active_sources: list[str] = []
    _source_systems_seen: set[str] = set()
    for a in raw["activities"]:
        ss: str = str(a.get("source_system", ""))
        if ss:
            _source_systems_seen.add(ss)
    if raw["meetings"]:
        _source_systems_seen.add("meetings")
    if raw["tracker_issues"]:
        _source_systems_seen.add("linear")
    if raw["github_commits"] or raw["github_pull_requests"]:
        _source_systems_seen.add("github")
    if raw["shared_files"]:
        _source_systems_seen.add("google_drive")
    active_sources = sorted(_source_systems_seen)
    raw["active_sources"] = active_sources

    return raw


def _is_raw_effectively_empty(raw: dict[str, Any]) -> bool:
    for key in (
        "activities",
        "meetings",
        "tracker_issues",
        "github_commits",
        "github_pull_requests",
        "shared_files",
    ):
        if raw.get(key):
            return False
    return True


def _empty_summary(member_name: str = "", digest_date: date | None = None, org_name: str = "") -> dict[str, Any]:
    first: str = member_name.split()[0] if member_name.strip() else "This person"
    label: str = org_name if org_name else "synced"
    if digest_date is not None:
        date_str: str = digest_date.strftime("%B %-d, %Y")
        narrative: str = f"{first} didn't have any {label}-related activity on {date_str}."
    else:
        narrative = f"{first} didn't have any {label}-related activity for this day."
    return {
        "narrative": narrative,
        "highlights": [],
        "categories": {
            "code": [],
            "issues": [],
            "meetings": [],
            "slack": [],
            "calendar": [],
            "crm": [],
            "documents": [],
        },
    }


async def _call_llm_for_summary(raw: dict[str, Any]) -> dict[str, Any]:
    digest_date_str: str = str(raw.get("digest_date", ""))
    member_name: str = str(raw.get("member_name", "")).strip() or "This team member"
    first_name: str = member_name.split()[0] if member_name else "They"
    name_possessive: str = f"{first_name}'s" if first_name != "They" else "this team member's"
    parsed_date: date | None = None
    try:
        parsed_date = date.fromisoformat(digest_date_str)
        date_human: str = parsed_date.strftime("%B %-d, %Y")
    except ValueError:
        date_human = digest_date_str
    org_name: str = str(raw.get("org_name", "")).strip() or "Basebase"
    system_prompt: str = _SYSTEM_PROMPT_TEMPLATE.format(
        date=digest_date_str,
        date_human=date_human,
        name_possessive=name_possessive,
        first_name=first_name,
        org_name=org_name,
    )
    client: AsyncAnthropic = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    payload: str = json.dumps(raw, default=str)[:120_000]
    user_msg: str = f"Raw activity data (JSON):\n{payload}"
    try:
        resp = await client.messages.create(
            model=_MODEL,
            max_tokens=2048,
            system=system_prompt,
            messages=[{"role": "user", "content": user_msg}],
        )
        await report_anthropic_call_success(source="daily_digest")
    except Exception as exc:
        await report_anthropic_call_failure(exc=exc, source="daily_digest")
        logger.exception("daily_digest LLM failed: %s", exc)
        return _empty_summary(member_name, parsed_date, org_name)

    text_parts: list[str] = []
    for block in resp.content:
        if hasattr(block, "text"):
            text_parts.append(str(block.text))
    combined: str = "".join(text_parts).strip()
    if combined.startswith("```"):
        first_newline: int = combined.find("\n")
        if first_newline != -1:
            combined = combined[first_newline + 1 :]
        if combined.endswith("```"):
            combined = combined[: -3]
        combined = combined.strip()

    fallback: dict[str, Any] = _empty_summary(member_name, parsed_date, org_name)
    try:
        parsed: Any = json.loads(combined)
    except json.JSONDecodeError:
        logger.warning("daily_digest LLM returned non-JSON, using fallback: %.200s", combined)
        return {
            "narrative": combined[:500] if combined else fallback["narrative"],
            "highlights": [],
            "categories": fallback["categories"],
        }
    if not isinstance(parsed, dict):
        return fallback
    out: dict[str, Any] = {
        "narrative": str(parsed.get("narrative", ""))[:2000],
        "highlights": parsed.get("highlights") if isinstance(parsed.get("highlights"), list) else [],
        "categories": parsed.get("categories")
        if isinstance(parsed.get("categories"), dict)
        else fallback["categories"],
    }
    return out


async def generate_member_digest(
    session: Any,
    organization_id: UUID,
    user_id: UUID,
    digest_date: date,
) -> DailyDigest:
    """Collect data, summarize, upsert ``DailyDigest`` row."""
    raw: dict[str, Any] = await collect_member_raw_data(
        session, organization_id, user_id, digest_date
    )
    raw_member_name: str = str(raw.get("member_name", ""))
    raw_org_name: str = str(raw.get("org_name", ""))
    if _is_raw_effectively_empty(raw):
        summary: dict[str, Any] = _empty_summary(raw_member_name, digest_date, raw_org_name)
    else:
        summary = await _call_llm_for_summary(raw)

    now: datetime = datetime.now(timezone.utc)
    new_id: UUID = uuid.uuid4()
    insert_stmt = pg_insert(DailyDigest).values(
        id=new_id,
        organization_id=organization_id,
        user_id=user_id,
        digest_date=digest_date,
        summary=summary,
        raw_data=raw,
        generated_at=now,
    )
    upsert_stmt = insert_stmt.on_conflict_do_update(
        constraint="uq_daily_digests_org_user_date",
        set_={
            "summary": insert_stmt.excluded.summary,
            "raw_data": insert_stmt.excluded.raw_data,
            "generated_at": insert_stmt.excluded.generated_at,
        },
    )
    await session.execute(upsert_stmt)
    await session.commit()

    res = await session.execute(
        select(DailyDigest).where(
            DailyDigest.organization_id == organization_id,
            DailyDigest.user_id == user_id,
            DailyDigest.digest_date == digest_date,
        )
    )
    row: DailyDigest | None = res.scalar_one_or_none()
    if row is None:
        raise RuntimeError("daily_digest upsert failed to read back row")
    return row


_TEAM_SUMMARY_SYSTEM_PROMPT: str = (
    "You are given individual daily work summaries for every member of a team on {date_human}. "
    "You may also receive the team's summaries from the previous few days for context.\n\n"
    "Identify the 1-4 shared goals or initiatives the team appears to be making progress on, "
    "based on today's activity and any recent patterns. Present each as a short markdown section:\n\n"
    "# Goal / Initiative Title\n"
    "1-2 sentences on what progress the team made toward this goal today.\n\n"
    "Use past tense. Focus on themes, key outcomes, and momentum — not individual names. "
    "Keep each section brief (1-2 sentences). You may use markdown formatting "
    "(headers, bold, inline code) but do NOT use bullet lists. "
    "If all members had no activity, just write a single section titled "
    "\"# Quiet Day\" with a note that there was no notable activity."
)

_PRIOR_TEAM_SUMMARIES_LOOKBACK_DAYS: int = 4


async def generate_team_summary(
    session: Any,
    organization_id: UUID,
    digest_date: date,
) -> DailyTeamSummary:
    """Summarize all member narratives into a single team paragraph and upsert."""
    res = await session.execute(
        select(DailyDigest).where(
            DailyDigest.organization_id == organization_id,
            DailyDigest.digest_date == digest_date,
        )
    )
    digests: list[DailyDigest] = list(res.scalars().all())

    narratives: list[str] = []
    for d in digests:
        raw_summary: dict[str, Any] = d.summary if isinstance(d.summary, dict) else {}
        narrative: str = str(raw_summary.get("narrative", "")).strip()
        if narrative and "didn't have any" not in narrative.lower():
            narratives.append(narrative)

    if not narratives:
        summary_text: str = "The team had a quiet day with no notable activity."
    else:
        try:
            parsed_date: date = digest_date
            date_human: str = parsed_date.strftime("%B %-d, %Y")
        except ValueError:
            date_human = digest_date.isoformat()

        prior_parts: list[str] = []
        try:
            lookback_start: date = digest_date - timedelta(days=_PRIOR_TEAM_SUMMARIES_LOOKBACK_DAYS)
            prior_res = await session.execute(
                select(DailyTeamSummary)
                .where(
                    DailyTeamSummary.organization_id == organization_id,
                    DailyTeamSummary.digest_date >= lookback_start,
                    DailyTeamSummary.digest_date < digest_date,
                )
                .order_by(DailyTeamSummary.digest_date.asc())
            )
            prior_rows: list[DailyTeamSummary] = list(prior_res.scalars().all())
            for pr in prior_rows:
                pr_date_str: str = pr.digest_date.strftime("%B %-d, %Y")
                prior_parts.append(f"{pr_date_str}: {pr.summary_text}")
        except Exception:
            logger.debug("Failed to load prior team summaries; proceeding without them")

        system_prompt: str = _TEAM_SUMMARY_SYSTEM_PROMPT.format(date_human=date_human)
        user_msg: str = ""
        if prior_parts:
            user_msg += "Previous days' team summaries:\n\n" + "\n\n".join(prior_parts) + "\n\n---\n\n"
        user_msg += "Today's member summaries:\n\n" + "\n\n".join(narratives)
        client: AsyncAnthropic = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        try:
            resp = await client.messages.create(
                model=_MODEL,
                max_tokens=512,
                system=system_prompt,
                messages=[{"role": "user", "content": user_msg}],
            )
            await report_anthropic_call_success(source="daily_team_summary")
            text_parts: list[str] = []
            for block in resp.content:
                if hasattr(block, "text"):
                    text_parts.append(str(block.text))
            summary_text = "".join(text_parts).strip()[:2000] or "The team had a quiet day with no notable activity."
        except Exception as exc:
            await report_anthropic_call_failure(exc=exc, source="daily_team_summary")
            logger.exception("team summary LLM failed: %s", exc)
            summary_text = "The team had a quiet day with no notable activity."

    now: datetime = datetime.now(timezone.utc)
    new_id: UUID = uuid.uuid4()
    insert_stmt = pg_insert(DailyTeamSummary).values(
        id=new_id,
        organization_id=organization_id,
        digest_date=digest_date,
        summary_text=summary_text,
        generated_at=now,
    )
    upsert_stmt = insert_stmt.on_conflict_do_update(
        constraint="uq_daily_team_summaries_org_date",
        set_={
            "summary_text": insert_stmt.excluded.summary_text,
            "generated_at": insert_stmt.excluded.generated_at,
        },
    )
    await session.execute(upsert_stmt)
    await session.commit()

    row_res = await session.execute(
        select(DailyTeamSummary).where(
            DailyTeamSummary.organization_id == organization_id,
            DailyTeamSummary.digest_date == digest_date,
        )
    )
    row: DailyTeamSummary | None = row_res.scalar_one_or_none()
    if row is None:
        raise RuntimeError("daily_team_summary upsert failed to read back row")
    return row


async def generate_org_digests_for_session(
    session: Any,
    organization_id: UUID,
    digest_date: date,
) -> dict[str, Any]:
    """Generate digests for every active org member, then a team summary."""
    mem_res = await session.execute(
        select(OrgMember)
        .join(User, User.id == OrgMember.user_id)
        .where(
            OrgMember.organization_id == organization_id,
            OrgMember.status == "active",
            User.is_guest.is_(False),
        )
    )
    members: list[OrgMember] = list(mem_res.scalars().all())
    generated: int = 0
    errors: list[str] = []
    for m in members:
        try:
            await generate_member_digest(session, organization_id, m.user_id, digest_date)
            generated += 1
        except Exception as exc:
            err_msg: str = f"user_id={m.user_id}: {exc}"
            errors.append(err_msg)
            logger.exception("daily_digest member failed %s", err_msg)

    try:
        await generate_team_summary(session, organization_id, digest_date)
    except Exception as exc:
        errors.append(f"team_summary: {exc}")
        logger.exception("daily_digest team summary failed: %s", exc)

    return {"generated": generated, "errors": errors}


async def run_daily_digests_all_organizations(
    digest_date: date | None = None,
) -> dict[str, Any]:
    """Admin session: list orgs; per org use tenant session and generate digests."""
    from models.database import get_admin_session, get_session

    target: date = digest_date if digest_date is not None else digest_date_yesterday_pt()
    summary: dict[str, Any] = {
        "digest_date": target.isoformat(),
        "organizations_processed": 0,
        "members_generated": 0,
        "errors": [],
    }
    async with get_admin_session() as admin:
        org_res = await admin.execute(select(Organization.id))
        org_ids: list[UUID] = [row[0] for row in org_res.all()]

    for oid in org_ids:
        org_str: str = str(oid)
        try:
            async with get_session(organization_id=org_str) as session:
                part: dict[str, Any] = await generate_org_digests_for_session(
                    session, oid, target
                )
            summary["organizations_processed"] += 1
            summary["members_generated"] += int(part.get("generated", 0))
            for e in part.get("errors", []):
                summary["errors"].append(f"org={org_str} {e}")
        except Exception as exc:
            summary["errors"].append(f"org={org_str}: {exc}")
            logger.exception("daily_digest org failed org=%s", org_str)
    return summary