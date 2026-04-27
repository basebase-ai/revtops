"""
Sync tasks for Celery workers.

These tasks handle syncing data from external integrations (CRM, calendar, etc.)
on a scheduled or on-demand basis.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure backend directory is in Python path for Celery forked workers
_backend_dir = Path(__file__).resolve().parent.parent.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from config import settings
from workers.celery_app import celery_app
from services.anthropic_health import report_anthropic_call_failure, report_anthropic_call_success
from workers.run_async import run_async

logger = logging.getLogger(__name__)

# Provider-specific sync cadence for periodic global sync runs.
# Default cadence remains hourly unless explicitly overridden.
PROVIDER_SYNC_INTERVALS: dict[str, timedelta] = {
    "google_drive": timedelta(minutes=30),
}
DEFAULT_SYNC_INTERVAL: timedelta = timedelta(hours=1)
SYNC_TASK_MAX_RETRIES: int = 3
SYNC_TASK_BASE_RETRY_DELAY_SECONDS: int = 30
SYNC_TASK_MAX_RETRY_DELAY_SECONDS: int = 300


def _parse_sync_since_iso(iso_str: str | None) -> datetime | None:
    """Parse optional ISO8601 string to naive UTC (for connector sync_since_override)."""
    if iso_str is None:
        return None
    stripped: str = iso_str.strip()
    if not stripped:
        return None
    norm: str = stripped[:-1] + "+00:00" if stripped.endswith("Z") else stripped
    try:
        parsed: datetime = datetime.fromisoformat(norm)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _classify_sync_failure(error_message: str) -> tuple[str, int]:
    """
    Classify common connector sync failure modes for clearer logging.

    Returns:
        Tuple of (failure_case, suggested_log_level)
    """
    normalized = error_message.lower()
    if any(
        snippet in normalized
        for snippet in (
            "connection not found",
            "invalid_auth",
            "token_revoked",
            "auth revoked",
            "account_inactive",
            "not_authed",
            "revoked",
            "unauthorized",
            "forbidden",
            "401",
            "403",
        )
    ):
        return ("auth_or_connection_revoked", logging.WARNING)
    if any(
        snippet in normalized
        for snippet in (
            "rate limit",
            "rate_limit",
            "too many requests",
            "429",
        )
    ):
        return ("upstream_rate_limited", logging.WARNING)
    if any(
        snippet in normalized
        for snippet in (
            "timed out",
            "timeout",
            "temporarily unavailable",
            "service unavailable",
            "bad gateway",
            "gateway timeout",
            "502",
            "503",
            "504",
            "connection reset",
        )
    ):
        return ("upstream_transient_error", logging.WARNING)
    return ("unexpected_failure", logging.ERROR)


def _should_retry_sync_failure(failure_case: str) -> bool:
    """Whether a failed sync should be retried by Celery."""
    return failure_case in {"upstream_rate_limited", "upstream_transient_error"}


def _compute_sync_retry_delay_seconds(retries_so_far: int) -> int:
    """Exponential backoff with jitter for sync retries."""
    exp_delay = SYNC_TASK_BASE_RETRY_DELAY_SECONDS * (2 ** max(retries_so_far, 0))
    capped_delay = min(exp_delay, SYNC_TASK_MAX_RETRY_DELAY_SECONDS)
    jitter = random.randint(0, 10)
    return capped_delay + jitter


async def _clear_last_errors_for_integration(
    organization_id: str,
    provider: str,
    user_id: str | None,
) -> None:
    """Clear integration.last_error for matching rows (matches API trigger_sync behavior)."""
    from sqlalchemy import select

    from models.database import get_session
    from models.integration import Integration

    org_uuid: UUID = UUID(organization_id)
    async with get_session(organization_id=organization_id) as session:
        stmt = select(Integration).where(
            Integration.organization_id == org_uuid,
            Integration.connector == provider,
            Integration.is_active == True,  # noqa: E712
        )
        if user_id is not None:
            stmt = stmt.where(Integration.user_id == UUID(user_id))
        else:
            stmt = stmt.where(Integration.user_id.is_(None))
        result = await session.execute(stmt)
        rows: list[Integration] = list(result.scalars().all())
        changed: bool = False
        for integ in rows:
            if integ.last_error is not None:
                integ.last_error = None
                changed = True
        if changed:
            await session.commit()


async def _sync_integration(
    organization_id: str,
    provider: str,
    user_id: str | None = None,
    sync_since_override_iso: str | None = None,
) -> dict[str, Any]:
    """
    Internal async function to sync a single integration.

    Args:
        organization_id: UUID of the organization.
        provider: Integration provider name.
        user_id: Optional UUID of the user who owns this integration
                 (for per-user providers like Gmail, Calendar, etc.).
        sync_since_override_iso: Optional ISO8601 manual resync cutoff (serialized for Celery).

    Returns sync results including counts and any errors.
    """
    from connectors.base import SyncCancelledError
    from connectors.registry import Capability, discover_connectors
    from services.embedding_sync import generate_embeddings_for_organization
    from workers.events import emit_event

    connectors = discover_connectors()

    connector_class = connectors.get(provider)
    if not connector_class:
        return {
            "status": "failed",
            "error": f"Unknown provider: {provider}",
            "organization_id": organization_id,
            "provider": provider,
        }
    meta = getattr(connector_class, "meta", None)
    if meta is not None and hasattr(meta, "capabilities") and Capability.SYNC not in meta.capabilities:
        return {
            "status": "skipped",
            "error": "Query-only connector (no sync)",
            "organization_id": organization_id,
            "provider": provider,
        }

    sync_since_dt: datetime | None = _parse_sync_since_iso(sync_since_override_iso)
    connector: Any | None = None

    try:
        user_label: str = f" user={user_id}" if user_id else ""
        logger.info(f"Starting sync for {provider} in org {organization_id}{user_label}")
        connector = connector_class(
            organization_id,
            user_id=user_id,
            sync_since_override=sync_since_dt,
        )

        from access_control import ConnectorContext, check_connector_call

        dp_ctx = ConnectorContext(
            organization_id=organization_id,
            user_id=user_id,
            provider=provider,
            operation="sync",
        )
        dp_result = await check_connector_call(dp_ctx, None)
        if not dp_result.allowed:
            return {
                "status": "failed",
                "organization_id": organization_id,
                "provider": provider,
                "error": dp_result.deny_reason or "Connector sync not allowed",
            }

        await _clear_last_errors_for_integration(organization_id, provider, user_id)

        await connector.mark_sync_started()
        counts = await connector.sync_all()
        await connector.update_last_sync(counts)

        # Generate embeddings for newly synced activities
        try:
            embedded_count = await generate_embeddings_for_organization(
                organization_id, limit=500
            )
            logger.info(f"Generated embeddings for {embedded_count} activities")
        except Exception as embed_err:
            logger.warning(f"Embedding generation failed: {embed_err}")

        # Emit sync completed event for workflow triggers
        await emit_event(
            event_type="sync.completed",
            organization_id=organization_id,
            data={
                "provider": provider,
                "counts": counts,
                "completed_at": datetime.utcnow().isoformat(),
            },
        )

        logger.info(f"Completed sync for {provider} in org {organization_id}: {counts}")
        return {
            "status": "completed",
            "organization_id": organization_id,
            "provider": provider,
            "counts": counts,
        }

    except SyncCancelledError as e:
        cancel_msg = str(e)
        logger.info(f"Sync cancelled for {provider} in org {organization_id}: {cancel_msg}")
        try:
            if connector is not None:
                await connector.clear_sync_started()
        except Exception:
            pass
        try:
            await emit_event(
                event_type="sync.cancelled",
                organization_id=organization_id,
                data={
                    "provider": provider,
                    "message": cancel_msg,
                    "cancelled_at": datetime.utcnow().isoformat(),
                },
            )
        except Exception:
            pass
        return {
            "status": "cancelled",
            "organization_id": organization_id,
            "provider": provider,
            "error": cancel_msg,
        }

    except Exception as e:
        error_msg = str(e)
        failure_case, log_level = _classify_sync_failure(error_msg)
        logger.debug(
            "Connector sync failure diagnostics provider=%s org=%s user=%s sync_since_override=%s "
            "connector_initialized=%s error_type=%s",
            provider,
            organization_id,
            user_id,
            sync_since_dt.isoformat() if sync_since_dt else None,
            connector is not None,
            type(e).__name__,
        )
        logger.log(
            log_level,
            "Connector sync failed provider=%s org=%s user=%s case=%s error=%s",
            provider,
            organization_id,
            user_id,
            failure_case,
            error_msg,
            exc_info=True,
        )

        # Record error in database and clear in-progress flag
        try:
            err_connector = connector_class(
                organization_id,
                user_id=user_id,
                sync_since_override=sync_since_dt,
            )
            await err_connector.clear_sync_started()
            await err_connector.record_error(error_msg)
        except Exception:
            pass

        # Emit sync failed event
        await emit_event(
            event_type="sync.failed",
            organization_id=organization_id,
            data={
                "provider": provider,
                "error": error_msg,
                "failed_at": datetime.utcnow().isoformat(),
            },
        )

        return {
            "status": "failed",
            "organization_id": organization_id,
            "provider": provider,
            "error": error_msg,
        }


async def _get_all_active_integrations() -> list[dict[str, str | None]]:
    """Get all active integrations across all organizations."""
    from sqlalchemy import select
    from models.database import get_admin_session
    from models.integration import Integration

    # Use admin session to bypass RLS and query across all organizations
    async with get_admin_session() as session:
        result = await session.execute(
            select(Integration).where(Integration.is_active == True)
        )
        integrations = result.scalars().all()
        
        return [
            {
                "organization_id": str(i.organization_id),
                "connector": i.connector,
                "user_id": str(i.user_id) if i.user_id else None,
                "last_sync_at": i.last_sync_at.isoformat() if i.last_sync_at else None,
            }
            for i in integrations
        ]


def _should_sync_in_periodic_run(integration: dict[str, str | None], now: datetime) -> bool:
    """Return whether an integration is due for sync in the periodic global run."""
    provider: str = integration["connector"]  # type: ignore[assignment]
    cadence: timedelta = PROVIDER_SYNC_INTERVALS.get(provider, DEFAULT_SYNC_INTERVAL)
    raw_last_sync_at: str | None = integration.get("last_sync_at")

    if not raw_last_sync_at:
        logger.info(
            "Periodic sync due provider=%s org=%s user=%s reason=never_synced",
            provider,
            integration.get("organization_id"),
            integration.get("user_id"),
        )
        return True

    try:
        last_sync_at = datetime.fromisoformat(raw_last_sync_at)
    except ValueError:
        logger.warning(
            "Periodic sync forced provider=%s org=%s user=%s reason=invalid_last_sync_at value=%s",
            provider,
            integration.get("organization_id"),
            integration.get("user_id"),
            raw_last_sync_at,
        )
        return True

    elapsed: timedelta = now - last_sync_at
    due: bool = elapsed >= cadence
    if not due:
        logger.info(
            "Skipping periodic sync provider=%s org=%s user=%s elapsed_seconds=%s min_interval_seconds=%s",
            provider,
            integration.get("organization_id"),
            integration.get("user_id"),
            int(elapsed.total_seconds()),
            int(cadence.total_seconds()),
        )
    return due


async def _get_org_integrations(organization_id: str) -> list[dict[str, str | None]]:
    """Get all active integrations for an organization (including per-user)."""
    from sqlalchemy import select
    from models.database import get_session
    from models.integration import Integration

    async with get_session(organization_id=organization_id) as session:
        result = await session.execute(
            select(Integration).where(
                Integration.organization_id == UUID(organization_id),
                Integration.is_active == True,
            )
        )
        integrations = result.scalars().all()
        return [
            {
                "connector": i.connector,
                "user_id": str(i.user_id) if i.user_id else None,
            }
            for i in integrations
        ]


@celery_app.task(
    bind=True,
    name="workers.tasks.sync.sync_integration",
    max_retries=SYNC_TASK_MAX_RETRIES,
)
def sync_integration(
    self: Any,
    organization_id: str,
    provider: str,
    user_id: str | None = None,
    sync_since_override_iso: str | None = None,
) -> dict[str, Any]:
    """
    Celery task to sync a single integration.

    Args:
        organization_id: UUID of the organization
        provider: Integration provider name (e.g., 'hubspot', 'salesforce')
        user_id: Optional UUID of the user who owns this integration
        sync_since_override_iso: Optional ISO8601 manual resync cutoff

    Returns:
        Dict with sync status, counts, and any errors
    """
    user_label: str = f" user={user_id}" if user_id else ""
    logger.info(f"Task {self.request.id}: Syncing {provider} for org {organization_id}{user_label}")
    result: dict[str, Any] = run_async(
        _sync_integration(
            organization_id,
            provider,
            user_id=user_id,
            sync_since_override_iso=sync_since_override_iso,
        )
    )
    if result.get("status") == "failed":
        error_message: str = str(result.get("error") or "")
        failure_case, _ = _classify_sync_failure(error_message)
        retries_so_far: int = int(getattr(self.request, "retries", 0) or 0)
        if _should_retry_sync_failure(failure_case):
            delay_seconds = _compute_sync_retry_delay_seconds(retries_so_far)
            logger.warning(
                "Retrying sync task for transient failure task_id=%s provider=%s org=%s user=%s "
                "failure_case=%s retries_so_far=%s max_retries=%s delay_seconds=%s error=%s",
                self.request.id,
                provider,
                organization_id,
                user_id,
                failure_case,
                retries_so_far,
                SYNC_TASK_MAX_RETRIES,
                delay_seconds,
                error_message,
            )
            raise self.retry(countdown=delay_seconds)
    return result


@celery_app.task(bind=True, name="workers.tasks.sync.sync_organization")
def sync_organization(self: Any, organization_id: str) -> dict[str, Any]:
    """
    Celery task: enqueue one sync_integration child task per SYNC-capable integration.

    Args:
        organization_id: UUID of the organization

    Returns:
        Summary with dispatched task ids and counts
    """
    logger.info(f"Task {self.request.id}: Queueing per-integration syncs for org {organization_id}")

    async def _dispatch_org_syncs() -> dict[str, Any]:
        from connectors.registry import Capability, discover_connectors

        connectors = discover_connectors()
        integration_entries: list[dict[str, str | None]] = await _get_org_integrations(organization_id)
        task_ids: list[str] = []
        skipped: int = 0

        for entry in integration_entries:
            provider: str = entry["connector"]  # type: ignore[assignment]
            connector_cls = connectors.get(provider)
            meta = getattr(connector_cls, "meta", None) if connector_cls else None
            if (
                connector_cls is None
                or meta is None
                or not hasattr(meta, "capabilities")
                or Capability.SYNC not in meta.capabilities
            ):
                skipped += 1
                continue
            uid: str | None = entry["user_id"]
            async_result = sync_integration.delay(organization_id, provider, uid)
            task_ids.append(str(async_result.id))

        return {
            "organization_id": organization_id,
            "status": "dispatched",
            "child_tasks_dispatched": len(task_ids),
            "child_task_ids": task_ids,
            "skipped_non_sync": skipped,
            "completed_at": datetime.utcnow().isoformat(),
        }

    return run_async(_dispatch_org_syncs())


@celery_app.task(
    bind=True,
    name="workers.tasks.sync.check_huddle_recording",
    max_retries=3,
    default_retry_delay=600,
)
def check_huddle_recording(
    self: Any,
    meeting_id: str,
    organization_id: str,
) -> dict[str, Any]:
    """
    Check Google Drive for a recording after a huddle ends.

    Retries up to 3 times with 10-minute delay to allow time for
    Google to process and upload the recording to Drive.
    """
    logger.info(f"Task {self.request.id}: Checking recording for meeting {meeting_id}")
    return run_async(_check_huddle_recording(self, meeting_id, organization_id))


async def _check_huddle_recording(
    task: Any,
    meeting_id: str,
    organization_id: str,
) -> dict[str, Any]:
    """Async implementation of huddle recording check.

    New path (meet_space_name set): uses Meet REST API v2 conference records
    to fetch participants, recordings, and transcripts.

    Legacy path (meet_space_name null, google_event_id set): falls back to
    fuzzy Drive search for video files.
    """
    from models.database import get_session
    from models.meeting import Meeting
    from workers.events import emit_event

    async with get_session(organization_id=organization_id) as session:
        meeting = await session.get(Meeting, UUID(meeting_id))
        if not meeting:
            return {"status": "skipped", "reason": "meeting_not_found"}

        if meeting.recording_drive_id and meeting.summary:
            return {"status": "skipped", "reason": "recording_and_summary_already_linked"}

        meet_space_name = meeting.meet_space_name
        meeting_code = meeting.meeting_code
        title = meeting.title or "Huddle"
        start_time = meeting.scheduled_start
        organizer_email = meeting.organizer_email
        stored_participant_emails = [
            p["email"] for p in (meeting.participants or []) if p.get("email")
        ]

    # ── Get an OAuth token (Calendar integration — same token works for Meet & Drive) ──
    token = await _get_google_token(task, organization_id, organizer_email)
    if token is None:
        return {"status": "skipped", "reason": "no_google_integration"}

    import httpx

    MEET_API = "https://meet.googleapis.com/v2"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # ── Meet API path (huddles with space name) ──
    if meet_space_name:
        try:
            async with httpx.AsyncClient() as client:
                # Find the conference record by space name
                resp = await client.get(
                    f"{MEET_API}/conferenceRecords",
                    headers=headers,
                    params={"filter": f'space.name="{meet_space_name}"'},
                    timeout=30.0,
                )
                resp.raise_for_status()
                records = resp.json().get("conferenceRecords", [])

            if not records:
                remaining = task.max_retries - task.request.retries
                if remaining > 0:
                    logger.info(
                        "No conference record yet for meeting %s, %d retries remaining",
                        meeting_id, remaining,
                    )
                    raise task.retry()
                return {"status": "not_found", "meeting_id": meeting_id}

            # Use the most recent conference record
            conf_record = records[-1]
            conf_record_name = conf_record["name"]  # "conferenceRecords/abc123"

            recording_url = ""
            recording_drive_id = ""
            transcript_url = ""
            gemini_summary = ""
            participant_data: list[dict[str, Any]] = []

            async with httpx.AsyncClient() as client:
                # Fetch recordings
                rec_resp = await client.get(
                    f"{MEET_API}/{conf_record_name}/recordings",
                    headers=headers,
                    timeout=30.0,
                )
                if rec_resp.status_code == 200:
                    rec_json = rec_resp.json()
                    recordings = rec_json.get("recordings", [])
                    logger.info("Recordings response for %s: %s", meeting_id, rec_json)
                    if recordings:
                        drive_dest = recordings[0].get("driveDestination", {})
                        recording_url = drive_dest.get("exportUri", "")
                        recording_drive_id = drive_dest.get("file", "").split("/")[-1] if drive_dest.get("file") else ""
                else:
                    logger.warning("Recordings API returned %d for %s", rec_resp.status_code, meeting_id)

                # Fetch transcripts
                trans_resp = await client.get(
                    f"{MEET_API}/{conf_record_name}/transcripts",
                    headers=headers,
                    timeout=30.0,
                )
                if trans_resp.status_code == 200:
                    trans_json = trans_resp.json()
                    transcripts = trans_json.get("transcripts", [])
                    logger.info("Transcripts response for %s: %s", meeting_id, trans_json)
                    if transcripts:
                        docs_dest = transcripts[0].get("docsDestination", {})
                        transcript_url = docs_dest.get("exportUri", "")
                else:
                    logger.warning("Transcripts API returned %d for %s", trans_resp.status_code, meeting_id)

                # Fetch participants
                part_resp = await client.get(
                    f"{MEET_API}/{conf_record_name}/participants",
                    headers=headers,
                    timeout=30.0,
                )
                if part_resp.status_code == 200:
                    for p in part_resp.json().get("participants", []):
                        signin = p.get("signedinUser", {})
                        anon = p.get("anonymousUser", {})
                        participant_data.append({
                            "email": signin.get("user", ""),
                            "name": signin.get("displayName", anon.get("displayName", "")),
                        })

                # Fetch Gemini meeting summary doc from "Meet Recordings" in Drive
                # Merge live participant emails with any stored from calendar sync
                all_participant_emails = list({
                    e for e in
                    [p.get("email", "") for p in participant_data] + stored_participant_emails
                    if e
                })
                gemini_summary, summary_doc_id = await _fetch_gemini_summary(
                    client, organization_id, organizer_email, title, start_time, meeting_id,
                    participant_emails=all_participant_emails,
                )

        except Exception as e:
            if "retry" in type(e).__name__.lower():
                raise
            logger.warning("Meet API recording check failed: %s", e)
            raise task.retry(exc=e)

        # Save whatever we have so far (participants, summary) even if
        # recordings/transcripts aren't ready yet — don't lose data on retry
        async with get_session(organization_id=organization_id) as session:
            meeting = await session.get(Meeting, UUID(meeting_id))
            if meeting:
                if recording_url:
                    meeting.recording_url = recording_url
                if recording_drive_id:
                    meeting.recording_drive_id = recording_drive_id
                if transcript_url:
                    meeting.transcript_url = transcript_url
                if participant_data:
                    meeting.participants = participant_data
                    meeting.participant_count = len(participant_data)
                notes_changed = False
                if gemini_summary:
                    notes_changed = meeting.set_notes("gemini", gemini_summary, doc_id=summary_doc_id)
                await session.commit()

        if notes_changed:
            generate_meeting_summary.apply_async(
                args=[meeting_id, organization_id], countdown=_SUMMARY_DELAY,
            )

        if not recording_url and not transcript_url:
            remaining = task.max_retries - task.request.retries
            if remaining > 0:
                logger.info(
                    "No recordings/transcripts found yet for meeting %s (%d participants found), %d retries remaining",
                    meeting_id, len(participant_data), remaining,
                )
                raise task.retry()
            else:
                logger.info(
                    "Retries exhausted for meeting %s — saved %d participants + summary without recordings/transcripts",
                    meeting_id, len(participant_data),
                )

        if recording_url:
            await emit_event(
                event_type="huddle.recording_ready",
                organization_id=organization_id,
                data={
                    "meeting_id": meeting_id,
                    "recording_url": recording_url,
                    "drive_file_id": recording_drive_id,
                    "transcript_url": transcript_url,
                },
            )

        logger.info("Linked Meet API data to meeting %s (summary=%s)", meeting_id, bool(gemini_summary))
        return {
            "status": "found",
            "meeting_id": meeting_id,
            "recording_url": recording_url,
            "transcript_url": transcript_url,
            "participant_count": len(participant_data),
            "has_summary": bool(gemini_summary),
        }

    # ── Calendared meetings (or any meeting with a title): fetch Gemini summary from Drive ──
    if meeting_code or title:
        gemini_summary = ""
        summary_doc_id = ""
        try:
            async with httpx.AsyncClient() as client:
                gemini_summary, summary_doc_id = await _fetch_gemini_summary(
                    client, organization_id, organizer_email, title, start_time, meeting_id,
                    participant_emails=stored_participant_emails,
                )
        except Exception as e:
            logger.warning("Drive summary fetch failed for calendared meeting %s: %s", meeting_id, e)

        if gemini_summary:
            notes_changed = False
            async with get_session(organization_id=organization_id) as session:
                meeting = await session.get(Meeting, UUID(meeting_id))
                if meeting:
                    notes_changed = meeting.set_notes("gemini", gemini_summary, doc_id=summary_doc_id)
                    await session.commit()
            if notes_changed:
                generate_meeting_summary.apply_async(
                    args=[meeting_id, organization_id], countdown=_SUMMARY_DELAY,
                )
            logger.info("Saved Gemini summary (%d chars) for calendared meeting %s", len(gemini_summary), meeting_id)
            return {
                "status": "found",
                "meeting_id": meeting_id,
                "has_summary": True,
            }

        # No summary found yet — retry if retries remain
        if task is not None:
            remaining = task.max_retries - task.request.retries
            if remaining > 0:
                logger.info("No Gemini summary yet for calendared meeting %s, %d retries remaining", meeting_id, remaining)
                raise task.retry()

        return {"status": "not_found", "meeting_id": meeting_id}

    # ── Legacy fallback: Drive search for meetings without meet_space_name ──
    search_after = start_time.strftime("%Y-%m-%dT%H:%M:%S")
    search_before = (start_time + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S")
    query = (
        f"(mimeType='video/mp4' or mimeType='video/webm') "
        f"and modifiedTime > '{search_after}' "
        f"and modifiedTime < '{search_before}' "
        f"and trashed=false"
    )

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://www.googleapis.com/drive/v3/files",
                headers={"Authorization": f"Bearer {token}"},
                params={
                    "q": query,
                    "fields": "files(id,name,webViewLink,modifiedTime)",
                    "orderBy": "modifiedTime desc",
                    "pageSize": 20,
                },
                timeout=30.0,
            )
            resp.raise_for_status()
            files = resp.json().get("files", [])
    except Exception as e:
        logger.warning("Drive search failed for recording: %s", e)
        raise task.retry(exc=e)

    title_lower = title.lower()
    matched = None
    fallback = None
    for f in files:
        name_lower = f.get("name", "").lower()
        if title_lower != "huddle" and title_lower in name_lower:
            matched = f
            break
        if fallback is None and "meet" in name_lower:
            fallback = f
    if not matched:
        matched = fallback

    if not matched:
        remaining = task.max_retries - task.request.retries
        if remaining > 0:
            logger.info(
                "No recording found for meeting %s, %d retries remaining",
                meeting_id, remaining,
            )
            raise task.retry()
        return {"status": "not_found", "meeting_id": meeting_id}

    async with get_session(organization_id=organization_id) as session:
        meeting = await session.get(Meeting, UUID(meeting_id))
        if meeting:
            meeting.recording_url = matched.get("webViewLink", "")
            meeting.recording_drive_id = matched.get("id", "")
            await session.commit()

    await emit_event(
        event_type="huddle.recording_ready",
        organization_id=organization_id,
        data={
            "meeting_id": meeting_id,
            "recording_url": matched.get("webViewLink", ""),
            "drive_file_id": matched.get("id", ""),
            "file_name": matched.get("name", ""),
        },
    )

    logger.info("Linked recording %s to meeting %s", matched.get("id"), meeting_id)
    return {
        "status": "found",
        "meeting_id": meeting_id,
        "drive_file_id": matched.get("id", ""),
        "recording_url": matched.get("webViewLink", ""),
    }


async def _share_gemini_doc(
    client: "httpx.AsyncClient",
    drive_headers: dict[str, str],
    doc_id: str,
    organizer_email: str,
    participant_emails: list[str],
    meeting_id: str,
) -> None:
    """Share a Gemini summary doc with the organizer's domain and all participants.

    - Shares with the whole Google Workspace domain (reader) so anyone in the
      org can access the notes.
    - Shares individually with external participants (email domain differs from
      the organizer's) so they also get access.

    Failures are logged but never raised — sharing is best-effort and must not
    block the summary from being saved.
    """
    DRIVE_API = "https://www.googleapis.com/drive/v3"
    domain = organizer_email.rsplit("@", 1)[-1] if "@" in organizer_email else ""

    if not domain:
        logger.warning("Cannot share Gemini doc %s — no domain in organizer email", doc_id)
        return

    # 1. Share with the organizer's domain
    try:
        resp = await client.post(
            f"{DRIVE_API}/files/{doc_id}/permissions",
            headers=drive_headers,
            params={"sendNotificationEmail": "false"},
            json={"type": "domain", "role": "reader", "domain": domain},
            timeout=15.0,
        )
        if resp.status_code < 300:
            logger.info("Shared Gemini doc %s with domain %s for meeting %s", doc_id, domain, meeting_id)
        else:
            logger.warning(
                "Domain share failed for doc %s (meeting %s): %d %s",
                doc_id, meeting_id, resp.status_code, resp.text[:200],
            )
    except Exception as e:
        logger.warning("Domain share request failed for doc %s (meeting %s): %s", doc_id, meeting_id, e)

    # 2. Share individually with external participants (different domain)
    external_emails = [
        e for e in participant_emails
        if "@" in e and e.rsplit("@", 1)[-1].lower() != domain.lower() and e.lower() != organizer_email.lower()
    ]
    for email in external_emails:
        try:
            resp = await client.post(
                f"{DRIVE_API}/files/{doc_id}/permissions",
                headers=drive_headers,
                params={"sendNotificationEmail": "false"},
                json={"type": "user", "role": "reader", "emailAddress": email},
                timeout=15.0,
            )
            if resp.status_code < 300:
                logger.info("Shared Gemini doc %s with %s for meeting %s", doc_id, email, meeting_id)
            else:
                logger.warning(
                    "User share failed for doc %s → %s (meeting %s): %d %s",
                    doc_id, email, meeting_id, resp.status_code, resp.text[:200],
                )
        except Exception as e:
            logger.warning("User share request failed for doc %s → %s (meeting %s): %s", doc_id, email, meeting_id, e)


async def _fetch_gemini_summary(
    client: "httpx.AsyncClient",
    organization_id: str,
    organizer_email: str | None,
    title: str,
    start_time: datetime,
    meeting_id: str,
    participant_emails: list[str] | None = None,
) -> tuple[str, str]:
    """Search Drive's 'Meet Recordings' folder for a Gemini-generated summary doc.

    For named meetings, Gemini uses the meeting title as the doc name.
    For huddles (title='Huddle'), the doc is named like 'Meeting started <timestamp>'.
    Returns (plain-text content, doc_id) or ("", "") if not found.

    When a doc is found, it is shared with the organizer's domain and all
    meeting participants so everyone has access to the notes.
    """
    # Get a Drive-scoped token (Calendar token won't have Drive access)
    drive_token = await _get_google_token(
        None, organization_id, organizer_email, preferred_connector="google_drive"
    )
    if not drive_token:
        logger.info("No Drive token available for Gemini summary fetch, meeting %s", meeting_id)
        return "", ""

    drive_headers = {"Authorization": f"Bearer {drive_token}"}
    DRIVE_API = "https://www.googleapis.com/drive/v3"

    # Collect Drive doc IDs already assigned to other meetings in this org
    # to avoid assigning the same doc to multiple meetings
    exclude_doc_ids: set[str] = set()
    try:
        from models.database import get_admin_session
        from models.meeting import Meeting
        from sqlalchemy import select

        async with get_admin_session() as session:
            result = await session.execute(
                select(Meeting.summary_doc_id).where(
                    Meeting.organization_id == UUID(organization_id),
                    Meeting.summary_doc_id.isnot(None),
                )
            )
            exclude_doc_ids = {row[0] for row in result}
    except Exception as e:
        logger.warning("Failed to load existing summary_doc_ids: %s", e)

    # Build time window: Gemini creates the doc shortly after the meeting.
    # Use createdTime (not modifiedTime) — the doc is created once, but
    # modifiedTime can shift if someone opens/edits it later.
    # Search from 2h before to 4h after scheduled_start — for ad-hoc huddles
    # the stored start time may differ from when the meeting actually happened.
    search_after = (start_time - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S")
    search_before = (start_time + timedelta(hours=4)).strftime("%Y-%m-%dT%H:%M:%S")

    # Search for the doc by name.
    # - Huddles: Gemini uses "Meeting started <timestamp>" — search by that pattern
    # - Named meetings: try the title first, then fall back to "Meeting started"
    #   (Gemini doesn't always know the calendar/huddle name)
    title_lower = (title or "").lower()
    is_huddle = not title or title_lower.startswith("huddle") or title_lower == "untitled event"
    if is_huddle:
        name_filters = ["name contains 'Meeting started'"]
    else:
        safe_title = title.replace("\\", "\\\\").replace("'", "\\'")
        name_filters = [f"name contains '{safe_title}'", "name contains 'Meeting started'"]

    files = []
    try:
        for name_filter in name_filters:
            query = (
                f"mimeType='application/vnd.google-apps.document' "
                f"and {name_filter} "
                f"and createdTime > '{search_after}' "
                f"and createdTime < '{search_before}' "
                f"and trashed=false"
            )
            resp = await client.get(
                f"{DRIVE_API}/files",
                headers=drive_headers,
                params={
                    "q": query,
                    "fields": "files(id,name,createdTime)",
                    "orderBy": "createdTime desc",
                    "pageSize": 10,
                },
                timeout=30.0,
            )
            resp.raise_for_status()
            candidates = resp.json().get("files", [])
            # Filter out docs already assigned to other meetings
            files = [f for f in candidates if f["id"] not in exclude_doc_ids]
            if files:
                break

        if not files:
            logger.info("No Gemini summary doc found for meeting %s", meeting_id)
            return "", ""

        doc_id = files[0]["id"]
        doc_name = files[0].get("name", "")
        logger.info("Found Gemini summary doc '%s' (%s) for meeting %s", doc_name, doc_id, meeting_id)

        # Export as plain text
        export_resp = await client.get(
            f"{DRIVE_API}/files/{doc_id}/export",
            headers=drive_headers,
            params={"mimeType": "text/plain"},
            timeout=30.0,
        )
        export_resp.raise_for_status()
        summary_text = export_resp.text.strip()

        logger.info("Fetched Gemini summary (%d chars) for meeting %s", len(summary_text), meeting_id)

        # Share the doc with the org domain and all participants
        if organizer_email:
            await _share_gemini_doc(
                client, drive_headers, doc_id, organizer_email,
                participant_emails or [], meeting_id,
            )

        return summary_text, doc_id

    except Exception as e:
        logger.warning("Failed to fetch Gemini summary for meeting %s: %s", meeting_id, e)
        return "", ""


async def _get_google_token(
    task: Any,
    organization_id: str,
    organizer_email: str | None,
    preferred_connector: str | None = None,
) -> str | None:
    """Get a Google OAuth token for Meet/Drive API calls.

    Prefers the Calendar integration (same token works for Meet API).
    Falls back to Drive integration if Calendar is not available.
    Use preferred_connector='google_drive' to try Drive first (e.g. for Drive API calls).
    """
    from connectors.registry import discover_connectors
    from models.database import get_admin_session
    from models.integration import Integration
    from models.user import User
    from sqlalchemy import select

    connectors = discover_connectors()

    # Default order: Calendar first (covers Meet API), Drive fallback
    order = ["google_calendar", "google_drive"]
    if preferred_connector and preferred_connector in order:
        order.remove(preferred_connector)
        order.insert(0, preferred_connector)

    for connector_name in order:
        cls = connectors.get(connector_name)
        if not cls:
            continue

        integration = None

        if organizer_email:
            async with get_admin_session() as admin_session:
                result = await admin_session.execute(
                    select(Integration)
                    .join(User, Integration.user_id == User.id)
                    .where(
                        Integration.organization_id == UUID(organization_id),
                        Integration.connector == connector_name,
                        Integration.is_active == True,  # noqa: E712
                        User.email == organizer_email,
                    )
                )
                integration = result.scalars().first()

        if not integration:
            async with get_admin_session() as admin_session:
                result = await admin_session.execute(
                    select(Integration).where(
                        Integration.organization_id == UUID(organization_id),
                        Integration.connector == connector_name,
                        Integration.is_active == True,  # noqa: E712
                    )
                )
                integration = result.scalars().first()

        if integration:
            try:
                connector = cls(organization_id, user_id=str(integration.user_id))
                token, _ = await connector.get_oauth_token()
                return token
            except Exception as e:
                logger.warning("Failed to get %s token: %s", connector_name, e)
                continue

    return None


@celery_app.task(bind=True, name="workers.tasks.sync.sync_all_organizations")
def sync_all_organizations(self: Any) -> dict[str, Any]:
    """
    Celery task: enqueue one sync_integration child task per due integration (hourly beat).

    Returns:
        Dict with dispatch counts (each child runs independently on workers).
    """
    logger.info(f"Task {self.request.id}: Starting hourly sync dispatch for all organizations")

    async def _dispatch_all() -> dict[str, Any]:
        import time as _time

        from connectors.registry import Capability, discover_connectors

        run_start: float = _time.monotonic()
        now = datetime.utcnow()
        all_integrations: list[dict[str, str | None]] = await _get_all_active_integrations()
        connectors = discover_connectors()

        child_task_ids: list[str] = []
        total_skipped_cadence: int = 0
        total_skipped_non_sync: int = 0

        for entry in all_integrations:
            org_id: str = entry["organization_id"]  # type: ignore[assignment]
            provider: str = entry["connector"]  # type: ignore[assignment]
            connector_cls = connectors.get(provider)
            meta = getattr(connector_cls, "meta", None) if connector_cls else None
            if (
                connector_cls is None
                or meta is None
                or not hasattr(meta, "capabilities")
                or Capability.SYNC not in meta.capabilities
            ):
                total_skipped_non_sync += 1
                continue
            if not _should_sync_in_periodic_run(entry, now):
                total_skipped_cadence += 1
                continue
            uid: str | None = entry["user_id"]
            async_result = sync_integration.delay(org_id, provider, uid)
            child_task_ids.append(str(async_result.id))

        total_elapsed: float = _time.monotonic() - run_start
        dispatched: int = len(child_task_ids)

        summary: dict[str, Any] = {
            "status": "dispatched",
            "child_tasks_dispatched": dispatched,
            "skipped_due_to_cadence": total_skipped_cadence,
            "skipped_non_sync_connector": total_skipped_non_sync,
            "started_at": datetime.utcnow().isoformat(),
            "elapsed_seconds": round(total_elapsed, 3),
        }

        logger.info(
            "Hourly sync dispatch complete: %d child tasks queued, %d skipped cadence, "
            "%d skipped non-sync in %.1fs",
            dispatched,
            total_skipped_cadence,
            total_skipped_non_sync,
            total_elapsed,
        )
        return summary

    return run_async(_dispatch_all())


@celery_app.task(bind=True, name="workers.tasks.sync.sweep_active_huddles")
def sweep_active_huddles(self: Any) -> dict[str, Any]:
    """
    Periodic task that finds huddles still marked 'active' and checks
    whether their conference has actually ended. If so, marks them
    completed and schedules the recording/transcript check.
    """
    logger.info(f"Task {self.request.id}: Sweeping active huddles")
    return run_async(_sweep_active_huddles())


async def _sweep_active_huddles() -> dict[str, Any]:
    """Async implementation of the active huddle sweep."""
    import httpx
    from models.database import get_admin_session, get_session
    from models.meeting import Meeting
    from sqlalchemy import select

    MEET_API = "https://meet.googleapis.com/v2"

    # Find all meetings with huddle_status = 'active'
    async with get_admin_session() as session:
        result = await session.execute(
            select(Meeting).where(Meeting.huddle_status == "active")
        )
        active_huddles = result.scalars().all()

    if not active_huddles:
        return {"status": "ok", "checked": 0, "ended": 0}

    ended = 0
    checked = 0

    for meeting in active_huddles:
        checked += 1
        org_id = str(meeting.organization_id)
        meeting_id = str(meeting.id)

        # Skip huddles less than 5 minutes old (still likely in progress)
        if meeting.scheduled_start:
            age_minutes = (datetime.utcnow() - meeting.scheduled_start).total_seconds() / 60
            if age_minutes < 5:
                continue

        if meeting.meet_space_name:
            # New path: check Meet API for active conference
            token = await _get_google_token(None, org_id, meeting.organizer_email)
            if not token:
                logger.warning("No token for huddle sweep, meeting %s", meeting_id)
                continue

            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(
                        f"{MEET_API}/conferenceRecords",
                        headers={
                            "Authorization": f"Bearer {token}",
                            "Content-Type": "application/json",
                        },
                        params={"filter": f'space.name="{meeting.meet_space_name}"'},
                        timeout=30.0,
                    )
                    resp.raise_for_status()
                    records = resp.json().get("conferenceRecords", [])

                if records:
                    # Conference record exists — check if it has ended
                    latest = records[-1]
                    end_time = latest.get("endTime")
                    if end_time:
                        # Conference ended — mark meeting completed
                        now = datetime.utcnow()
                        async with get_session(organization_id=org_id) as session:
                            m = await session.get(Meeting, meeting.id)
                            if m and m.huddle_status == "active":
                                m.status = "completed"
                                m.huddle_status = "ended"
                                m.scheduled_end = now
                                if m.scheduled_start:
                                    m.duration_minutes = max(1, int((now - m.scheduled_start).total_seconds() / 60))
                                await session.commit()
                                ended += 1

                        # Schedule recording check
                        check_huddle_recording.apply_async(
                            args=[meeting_id, org_id],
                            countdown=300,
                        )
                        logger.info("Sweep: ended huddle %s, scheduled recording check", meeting_id)
                else:
                    # No conference record — nobody ever joined. If old enough (>30 min), clean up.
                    if meeting.scheduled_start:
                        age = (datetime.utcnow() - meeting.scheduled_start).total_seconds() / 60
                        if age > 30:
                            async with get_session(organization_id=org_id) as session:
                                m = await session.get(Meeting, meeting.id)
                                if m and m.huddle_status == "active":
                                    m.status = "cancelled"
                                    m.huddle_status = "ended"
                                    await session.commit()
                                    ended += 1
                            logger.info("Sweep: cancelled stale huddle %s (no one joined)", meeting_id)

            except Exception as e:
                logger.warning("Sweep: error checking huddle %s: %s", meeting_id, e)
                continue

        elif meeting.google_event_id:
            # Legacy path: check if calendar event end time has passed
            if meeting.scheduled_end and meeting.scheduled_end < datetime.utcnow():
                async with get_session(organization_id=org_id) as session:
                    m = await session.get(Meeting, meeting.id)
                    if m and m.huddle_status == "active":
                        m.status = "completed"
                        m.huddle_status = "ended"
                        if m.scheduled_start:
                            m.duration_minutes = max(1, int((m.scheduled_end - m.scheduled_start).total_seconds() / 60))
                        await session.commit()
                        ended += 1

                check_huddle_recording.apply_async(
                    args=[meeting_id, org_id],
                    countdown=300,
                )
                logger.info("Sweep: ended legacy huddle %s", meeting_id)

    logger.info("Sweep complete: checked=%d, ended=%d", checked, ended)
    return {"status": "ok", "checked": checked, "ended": ended}


@celery_app.task(bind=True, name="workers.tasks.sync.sweep_completed_meetings")
def sweep_completed_meetings(self: Any) -> dict[str, Any]:
    """
    Periodic task that finds recently-ended calendared Google Meet meetings
    missing a Gemini summary and schedules a fetch.
    """
    logger.info(f"Task {self.request.id}: Sweeping completed meetings for summaries")
    return run_async(_sweep_completed_meetings())


async def _sweep_completed_meetings() -> dict[str, Any]:
    """Async implementation: check Meet API for ended calendared meetings, fetch summaries."""
    import httpx
    from models.database import get_admin_session, get_session
    from models.meeting import Meeting
    from sqlalchemy import select

    MEET_API = "https://meet.googleapis.com/v2"

    now = datetime.utcnow()
    # Look at meetings starting within the last 4 hours that haven't been summarized
    cutoff = now - timedelta(hours=4)

    async with get_admin_session() as session:
        result = await session.execute(
            select(Meeting).where(
                Meeting.meeting_code.isnot(None),
                Meeting.summary.is_(None),
                Meeting.huddle_status.is_(None),  # Skip huddles — handled by sweep_active_huddles
                Meeting.scheduled_start.isnot(None),
                Meeting.scheduled_start > cutoff,
                Meeting.scheduled_start < now,  # Meeting has started
            )
        )
        meetings = result.scalars().all()

    if not meetings:
        return {"status": "ok", "checked": 0, "ended": 0, "scheduled": 0}

    checked = 0
    ended = 0
    scheduled = 0

    for meeting in meetings:
        checked += 1
        org_id = str(meeting.organization_id)
        meeting_id = str(meeting.id)
        meeting_code = meeting.meeting_code

        # Skip meetings less than 5 minutes past start (still likely in progress)
        if meeting.scheduled_start:
            age_minutes = (now - meeting.scheduled_start).total_seconds() / 60
            if age_minutes < 5:
                continue

        # Check Meet API for conference records using meeting_code
        token = await _get_google_token(None, org_id, meeting.organizer_email)
        if not token:
            logger.warning("Sweep: no token for calendared meeting %s", meeting_id)
            continue

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{MEET_API}/conferenceRecords",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    params={"filter": f'space.meeting_code="{meeting_code}"'},
                    timeout=30.0,
                )

                if resp.status_code == 403:
                    # Scope doesn't cover calendar-created spaces — fall back to
                    # scheduled_end check
                    logger.info(
                        "Sweep: Meet API 403 for calendared meeting %s, falling back to scheduled_end",
                        meeting_id,
                    )
                    if meeting.scheduled_end and meeting.scheduled_end < now:
                        check_huddle_recording.apply_async(
                            args=[meeting_id, org_id], countdown=10,
                        )
                        scheduled += 1
                    continue

                resp.raise_for_status()
                records = resp.json().get("conferenceRecords", [])

            if records:
                latest = records[-1]
                end_time = latest.get("endTime")
                if end_time:
                    # Conference ended — mark meeting completed and schedule summary fetch
                    if meeting.status != "completed":
                        async with get_session(organization_id=org_id) as session:
                            m = await session.get(Meeting, meeting.id)
                            if m:
                                m.status = "completed"
                                m.scheduled_end = now
                                if m.scheduled_start:
                                    m.duration_minutes = max(
                                        1, int((now - m.scheduled_start).total_seconds() / 60)
                                    )
                                await session.commit()
                        ended += 1

                    check_huddle_recording.apply_async(
                        args=[meeting_id, org_id], countdown=10,
                    )
                    scheduled += 1
                    logger.info("Sweep: calendared meeting %s ended, scheduled summary fetch", meeting_id)
            # else: no conference record yet — meeting may not have started or nobody joined

        except Exception as e:
            logger.warning("Sweep: error checking calendared meeting %s: %s", meeting_id, e)
            continue

    logger.info(
        "Completed meeting sweep: checked=%d, ended=%d, scheduled=%d",
        checked, ended, scheduled,
    )
    return {"status": "ok", "checked": checked, "ended": ended, "scheduled": scheduled}


# ── Meeting summary generation ──────────────────────────────────────────


@celery_app.task(
    bind=True,
    name="workers.tasks.sync.generate_meeting_summary",
    max_retries=2,
    default_retry_delay=30,
)
def generate_meeting_summary(
    self: Any,
    meeting_id: str,
    organization_id: str,
) -> dict[str, Any]:
    """Generate an LLM summary from all external_notes for a meeting."""
    logger.info("Generating meeting summary for %s", meeting_id)
    return run_async(_generate_meeting_summary(self, meeting_id, organization_id))


# Delay before generating summary — gives time for multiple sources to arrive
_SUMMARY_DELAY = 60


_SUMMARY_SYSTEM_PROMPT = (
    "You synthesize meeting notes from multiple sources into a single concise summary. "
    "Each source may capture different aspects of the meeting (auto-generated notes, "
    "personal notes, transcript summaries). Combine them into a coherent summary that "
    "preserves all key information.\n\n"
    "Guidelines:\n"
    "- Lead with the main topics and decisions\n"
    "- Include action items if mentioned in any source\n"
    "- Keep it concise (2-4 paragraphs max)\n"
    "- Do not mention the source names or that multiple sources exist\n"
    "- Write in plain text, no markdown"
)


async def _generate_meeting_summary(
    task: Any,
    meeting_id: str,
    organization_id: str,
) -> dict[str, Any]:
    from config import settings
    from models.database import get_admin_session
    from models.meeting import Meeting
    from services.llm_provider import resolve_llm_config, get_adapter

    async with get_admin_session() as session:
        meeting = await session.get(Meeting, UUID(meeting_id))
        if not meeting or not meeting.external_notes:
            return {"status": "skipped", "reason": "no notes"}

        # Build prompt from all sources
        parts = []
        for source, entries in meeting.external_notes.items():
            for entry in entries:
                content = entry.get("content", "").strip()
                if content:
                    parts.append(f"[{source}]\n{content}")

        if not parts:
            return {"status": "skipped", "reason": "empty notes"}

        title = meeting.title or "Untitled Meeting"
        user_message = (
            f"Meeting: {title}\n\n"
            + "\n\n---\n\n".join(parts)
        )

    llm_config = await resolve_llm_config(organization_id)
    adapter = get_adapter(llm_config)
    try:
        completed = await adapter.complete(
            model=llm_config.cheap_model,
            system=_SUMMARY_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
            max_tokens=1024,
        )
        await report_anthropic_call_success(source="workers.tasks.sync._generate_meeting_summary")
    except Exception as exc:
        await report_anthropic_call_failure(
            exc=exc,
            source="workers.tasks.sync._generate_meeting_summary",
        )
        raise
    summary_text = (completed.content_blocks[0].text or "").strip() if completed.content_blocks else ""

    # Save
    async with get_admin_session() as session:
        meeting = await session.get(Meeting, UUID(meeting_id))
        if meeting:
            meeting.summary = summary_text
            await session.commit()

    logger.info(
        "Generated meeting summary (%d chars) for %s from %d source(s)",
        len(summary_text), meeting_id, len(parts),
    )
    return {"status": "ok", "meeting_id": meeting_id, "summary_length": len(summary_text)}
