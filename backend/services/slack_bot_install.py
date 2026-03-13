"""
Slack "Add to Slack" (bot install) flow — token storage and lookup.

When another workspace adds the Basebase bot via the public link, we exchange
the OAuth code ourselves and store the bot token here (not in Nango).
Events from that workspace are then routed to the correct org via
find_organization_by_slack_team + get_slack_bot_token.

Reads from **both** ``messenger_bot_installs`` (new) and ``slack_bot_installs``
(legacy) tables so the transition is seamless.
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
from models.messenger_bot_install import MessengerBotInstall
from models.slack_bot_install import SlackBotInstall

logger = logging.getLogger(__name__)

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
    """Insert or update a Slack bot install — writes to both tables during transition."""
    normalized_team_id: str = team_id.strip()
    encrypted: str = encrypt_token(access_token)
    now: datetime = datetime.now(timezone.utc).replace(tzinfo=None)
    async with get_admin_session() as session:
        # Legacy table
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

        # New table
        new_result = await session.execute(
            select(MessengerBotInstall)
            .where(MessengerBotInstall.platform == "slack")
            .where(MessengerBotInstall.workspace_id == normalized_team_id)
        )
        existing_new: MessengerBotInstall | None = new_result.scalar_one_or_none()
        if existing_new:
            existing_new.organization_id = organization_id
            existing_new.access_token_encrypted = encrypted
            existing_new.updated_at = now
        else:
            session.add(
                MessengerBotInstall(
                    organization_id=organization_id,
                    platform="slack",
                    workspace_id=normalized_team_id,
                    access_token_encrypted=encrypted,
                )
            )

        await session.commit()
    logger.info(
        "[slack_bot_install] Upserted bot install org=%s team_id=%s",
        organization_id, normalized_team_id,
    )


async def get_slack_bot_token(organization_id: str, team_id: str) -> Optional[str]:
    """Return decrypted bot token — checks new table first, falls back to legacy."""
    normalized_team_id: str = team_id.strip()
    try:
        org_uuid: UUID = UUID(organization_id)
    except ValueError:
        return None

    async with get_admin_session() as session:
        # Check new table first
        new_result = await session.execute(
            select(MessengerBotInstall.access_token_encrypted)
            .where(MessengerBotInstall.platform == "slack")
            .where(MessengerBotInstall.workspace_id == normalized_team_id)
            .where(MessengerBotInstall.organization_id == org_uuid)
        )
        new_row = new_result.fetchone()
        if new_row and new_row[0]:
            try:
                return decrypt_token(new_row[0])
            except Exception as e:
                logger.warning(
                    "[slack_bot_install] Decrypt failed (new table) team_id=%s: %s",
                    normalized_team_id, e,
                )

        # Fall back to legacy table
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
            "[slack_bot_install] Decrypt failed team_id=%s: %s", normalized_team_id, e,
        )
        return None


async def get_organization_id_by_slack_team(team_id: str) -> Optional[str]:
    """Return organization_id for a team_id — checks new table first."""
    normalized_team_id: str = team_id.strip()
    async with get_admin_session() as session:
        # Check new table first
        new_result = await session.execute(
            select(MessengerBotInstall.organization_id)
            .where(MessengerBotInstall.platform == "slack")
            .where(MessengerBotInstall.workspace_id == normalized_team_id)
        )
        new_row = new_result.fetchone()
        if new_row:
            return str(new_row[0])

        # Fall back to legacy
        result = await session.execute(
            select(SlackBotInstall.organization_id).where(
                SlackBotInstall.team_id == normalized_team_id
            )
        )
        row = result.fetchone()
    if not row:
        return None
    return str(row[0])
