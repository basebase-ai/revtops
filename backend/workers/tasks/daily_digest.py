"""
Celery tasks: nightly per-member daily digests (PT calendar day).
"""
from __future__ import annotations

import logging
import sys
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID

_backend_dir = Path(__file__).resolve().parent.parent.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

from workers.celery_app import celery_app
from workers.run_async import run_async

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, name="workers.tasks.daily_digest.generate_daily_digests_all_orgs")
def generate_daily_digests_all_orgs(
    self: Any,
    digest_date_iso: str | None = None,
) -> dict[str, Any]:
    """Beat entry: generate yesterday's digests for every org (or *digest_date_iso* if provided)."""
    logger.info("Task %s: daily digest all orgs date=%s", self.request.id, digest_date_iso)

    async def _run() -> dict[str, Any]:
        from services.daily_digest import run_daily_digests_all_organizations

        parsed: date | None = None
        if digest_date_iso:
            parsed = date.fromisoformat(digest_date_iso.strip())
        return await run_daily_digests_all_organizations(digest_date=parsed)

    return run_async(_run())


@celery_app.task(bind=True, name="workers.tasks.daily_digest.generate_daily_digests_org")
def generate_daily_digests_org(
    self: Any,
    organization_id: str,
    digest_date_iso: str | None = None,
) -> dict[str, Any]:
    """Generate digests for one org (used by manual API trigger)."""
    logger.info(
        "Task %s: daily digest org=%s date=%s",
        self.request.id,
        organization_id,
        digest_date_iso,
    )

    async def _run() -> dict[str, Any]:
        from models.database import get_session
        from services.daily_digest import (
            digest_date_yesterday_pt,
            generate_org_digests_for_session,
        )

        oid: UUID = UUID(organization_id)
        d: date
        if digest_date_iso:
            d = date.fromisoformat(digest_date_iso.strip())
        else:
            d = digest_date_yesterday_pt()
        async with get_session(organization_id=organization_id) as session:
            return await generate_org_digests_for_session(session, oid, d)

    return run_async(_run())
