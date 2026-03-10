"""
Slack "Add to Slack" (bot install) flow — token storage and lookup.

When another workspace adds the Basebase bot via the public link, we exchange
the OAuth code ourselves and store the bot token here (not in Nango).
Events from that workspace are then routed to the correct org via
find_organization_by_slack_team + get_slack_bot_token.
"""
from __future__ import annotations

import base64
import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from cryptography.fernet import Fernet
from sqlalchemy import select, update

from config import settings
from models.database import get_admin_session
from models.slack_bot_install import SlackBotInstall

logger = logging.getLogger(__name__)

# State prefix for our "Add to Slack" link so we don't forward these to Nango
SLACK_BOT_INSTALL_STATE_PREFIX: str = "revtops_bot_"


def _fernet() -> Fernet:
    """Fernet key derived from SECRET_KEY (32 bytes, base64)."""
    key = base64.urlsafe_b64encode(
        hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()
    )
    return Fernet(key)


def encrypt_token(plain: str) -> str:
    return _fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_token(encrypted: str) -> str:
    return _fernet().decrypt(encrypted.encode("ascii")).decode("utf-8")


async def upsert_bot_install(
    organization_id: UUID,
    team_id: str,
    access_token: str,
) -> None:
    """Insert or update a Slack bot install for (org, team_id)."""
    normalized_team_id: str = team_id.strip()
    encrypted: str = encrypt_token(access_token)
    now: datetime = datetime.now(timezone.utc).replace(tzinfo=None)
    async with get_admin_session() as session:
        result = await session.execute(
            select(SlackBotInstall).where(SlackBotInstall.team_id == normalized_team_id)
        )
        existing: SlackBotInstall | None = result.scalar_one_or_none()
        if existing:
            await session.execute(
                update(SlackBotInstall)
                .where(SlackBotInstall.team_id == normalized_team_id)
                .values(
                    organization_id=organization_id,
                    access_token_encrypted=encrypted,
                    updated_at=now,
                )
            )
        else:
            session.add(
                SlackBotInstall(
                    organization_id=organization_id,
                    team_id=normalized_team_id,
                    access_token_encrypted=encrypted,
                )
            )
        await session.commit()
    logger.info(
        "[slack_bot_install] Upserted bot install org=%s team_id=%s",
        organization_id,
        normalized_team_id,
    )


async def get_slack_bot_token(organization_id: str, team_id: str) -> Optional[str]:
    """Return decrypted bot token for (org_id, team_id), or None."""
    normalized_team_id: str = team_id.strip()
    try:
        org_uuid = UUID(organization_id)
    except ValueError:
        return None
    async with get_admin_session() as session:
        result = await session.execute(
            select(SlackBotInstall.access_token_encrypted).where(
                SlackBotInstall.organization_id == org_uuid,
                SlackBotInstall.team_id == normalized_team_id,
            )
        )
        row = result.fetchone()
    if not row:
        return None
    try:
        return decrypt_token(row[0])
    except Exception as e:
        logger.warning(
            "[slack_bot_install] Decrypt failed for team_id=%s: %s",
            normalized_team_id,
            e,
        )
        return None


async def get_organization_id_by_slack_team(team_id: str) -> Optional[str]:
    """Return organization_id for a team_id that has a bot install (no Nango integration)."""
    normalized_team_id: str = team_id.strip()
    async with get_admin_session() as session:
        result = await session.execute(
            select(SlackBotInstall.organization_id).where(
                SlackBotInstall.team_id == normalized_team_id
            )
        )
        row = result.fetchone()
    if not row:
        return None
    return str(row[0])
