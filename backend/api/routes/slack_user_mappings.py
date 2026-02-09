"""Slack user mapping endpoints for per-user email verification."""
from __future__ import annotations

import json
import logging
import secrets
from datetime import timedelta
from uuid import UUID

import redis.asyncio as redis
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, text

from config import settings
from connectors.slack import SlackConnector
from models.database import get_admin_session, get_session
from models.integration import Integration
from models.slack_user_mapping import SlackUserMapping
from models.user import User
from services import slack_conversations

logger = logging.getLogger(__name__)
router = APIRouter()

_redis_client: redis.Redis | None = None
_SEND_COOLDOWN = timedelta(minutes=1)
_CODE_TTL = timedelta(minutes=10)


class SlackMappingResponse(BaseModel):
    id: str
    slack_user_id: str | None
    slack_email: str | None
    match_source: str
    created_at: str


class SlackMappingListResponse(BaseModel):
    mappings: list[SlackMappingResponse]


class SlackMappingRequest(BaseModel):
    user_id: str
    organization_id: str | None = None
    email: str


class SlackMappingVerifyRequest(BaseModel):
    user_id: str
    organization_id: str | None = None
    email: str
    code: str


async def _get_redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis_client


async def _resolve_org_and_user(
    user_id: str,
    organization_id: str | None,
) -> tuple[UUID, UUID]:
    try:
        user_uuid = UUID(user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid user ID") from exc

    org_uuid: UUID | None = None
    if organization_id:
        try:
            org_uuid = UUID(organization_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid organization ID") from exc

    async with get_admin_session() as session:
        user = await session.get(User, user_uuid)
        if not user or not user.organization_id:
            raise HTTPException(status_code=404, detail="User not found")

        if org_uuid and user.organization_id != org_uuid:
            raise HTTPException(status_code=403, detail="User not authorized")

        return user.organization_id, user.id


async def _require_slack_integration(organization_id: UUID) -> Integration:
    async with get_session(organization_id=str(organization_id)) as session:
        result = await session.execute(
            select(Integration).where(
                Integration.organization_id == organization_id,
                Integration.provider == "slack",
                Integration.is_active == True,
            )
        )
        integration = result.scalar_one_or_none()
        if not integration:
            raise HTTPException(status_code=404, detail="Slack integration not connected")
        return integration


def _normalize_email(email: str) -> str:
    normalized = "".join(email.split()).lower()
    original = email
    if "@" in normalized:
        local_part, domain = normalized.split("@", 1)
        if "+" in local_part:
            local_part = local_part.split("+", 1)[0]
        if domain in {"gmail.com", "googlemail.com"}:
            local_part = local_part.replace(".", "")
        normalized = f"{local_part}@{domain}"
    if normalized != original:
        logger.debug(
            "[slack_user_mappings] Normalized email from '%s' to '%s'",
            original,
            normalized,
        )
    if not slack_conversations.EMAIL_PATTERN.fullmatch(normalized):
        raise HTTPException(status_code=400, detail="Invalid email address")
    return normalized


@router.get("/user-mappings", response_model=SlackMappingListResponse)
async def list_slack_user_mappings(
    user_id: str,
    organization_id: str | None = None,
) -> SlackMappingListResponse:
    org_uuid, user_uuid = await _resolve_org_and_user(user_id, organization_id)
    async with get_session(organization_id=str(org_uuid)) as session:
        await session.execute(
            text("SELECT set_config('app.current_org_id', :org_id, true)"),
            {"org_id": str(org_uuid)},
        )
        result = await session.execute(
            select(SlackUserMapping)
            .where(SlackUserMapping.organization_id == org_uuid)
            .where(SlackUserMapping.user_id == user_uuid)
            .order_by(SlackUserMapping.created_at.desc())
        )
        mappings = result.scalars().all()

    response = [
        SlackMappingResponse(
            id=str(mapping.id),
            slack_user_id=mapping.slack_user_id,
            slack_email=mapping.slack_email,
            match_source=mapping.match_source,
            created_at=mapping.created_at.isoformat() + "Z",
        )
        for mapping in mappings
    ]
    return SlackMappingListResponse(mappings=response)


@router.post("/user-mappings/request-code")
async def request_slack_user_mapping_code(
    request: SlackMappingRequest,
) -> dict[str, str]:
    org_uuid, user_uuid = await _resolve_org_and_user(
        request.user_id,
        request.organization_id,
    )
    email = _normalize_email(request.email)
    await _require_slack_integration(org_uuid)

    async with get_session(organization_id=str(org_uuid)) as session:
        await session.execute(
            text("SELECT set_config('app.current_org_id', :org_id, true)"),
            {"org_id": str(org_uuid)},
        )
        result = await session.execute(
            select(SlackUserMapping)
            .where(SlackUserMapping.organization_id == org_uuid)
            .where(SlackUserMapping.slack_email == email)
            .order_by(SlackUserMapping.updated_at.desc())
        )
        matched_mapping = result.scalars().first()

    logger.info(
        "[slack_user_mappings] Lookup Slack mapping for org=%s user=%s email=%s matched=%s",
        org_uuid,
        user_uuid,
        email,
        bool(matched_mapping),
    )

    if not matched_mapping or not matched_mapping.slack_user_id:
        logger.warning(
            "[slack_user_mappings] No Slack mapping found for org=%s user=%s email=%s",
            org_uuid,
            user_uuid,
            email,
        )
        raise HTTPException(
            status_code=404,
            detail=(
                "No matching Slack user found for that email. "
                "If you're new, try running a Slack sync and try again."
            ),
        )

    redis_client = await _get_redis()
    cooldown_key = f"revtops:slack-email-verify:{org_uuid}:{user_uuid}:cooldown"
    was_set = await redis_client.set(
        cooldown_key,
        "1",
        nx=True,
        ex=int(_SEND_COOLDOWN.total_seconds()),
    )
    if not was_set:
        logger.info(
            "[slack_user_mappings] Slack verification code cooldown active org=%s user=%s",
            org_uuid,
            user_uuid,
        )
        raise HTTPException(
            status_code=429,
            detail="Please wait at least one minute before requesting another code.",
        )
    matched_user = {
        "id": matched_mapping.slack_user_id,
        "email": matched_mapping.slack_email or email,
    }

    code = f"{secrets.randbelow(1000000):06d}"
    payload = json.dumps(
        {
            "slack_user_id": matched_user["id"],
            "email": matched_user["email"],
            "code": code,
        }
    )
    code_key = f"revtops:slack-email-verify:{org_uuid}:{user_uuid}:{email}"
    await redis_client.set(code_key, payload, ex=int(_CODE_TTL.total_seconds()))

    logger.info(
        "[slack_user_mappings] Sending Slack verification code org=%s user=%s slack_user=%s",
        org_uuid,
        user_uuid,
        matched_user["id"],
    )
    message_text = (
        "Your RevTops verification code is: "
        f"{code}\n\nIf you didn't request this, you can ignore it."
    )
    connector = SlackConnector(organization_id=str(org_uuid))
    action_response = await connector.send_direct_message(
        slack_user_id=matched_user["id"],
        text=message_text,
    )
    logger.info(
        "[slack_user_mappings] Slack DM send response org=%s user=%s slack_user=%s keys=%s",
        org_uuid,
        user_uuid,
        matched_user["id"],
        sorted(action_response.keys()) if isinstance(action_response, dict) else "n/a",
    )

    return {"status": "sent"}


@router.post("/user-mappings/verify-code")
async def verify_slack_user_mapping_code(
    request: SlackMappingVerifyRequest,
) -> dict[str, str]:
    org_uuid, user_uuid = await _resolve_org_and_user(
        request.user_id,
        request.organization_id,
    )
    email = _normalize_email(request.email)
    await _require_slack_integration(org_uuid)

    redis_client = await _get_redis()
    code_key = f"revtops:slack-email-verify:{org_uuid}:{user_uuid}:{email}"
    payload = await redis_client.get(code_key)
    if not payload:
        raise HTTPException(status_code=400, detail="Verification code expired or not found")

    data = json.loads(payload)
    expected_code = str(data.get("code", ""))
    if expected_code != request.code.strip():
        raise HTTPException(status_code=400, detail="Invalid verification code")

    slack_user_id = data.get("slack_user_id")
    if not slack_user_id:
        raise HTTPException(status_code=400, detail="Slack user information missing")

    await slack_conversations.upsert_slack_user_mapping_for_user(
        organization_id=str(org_uuid),
        user_id=user_uuid,
        slack_user_id=slack_user_id,
        slack_email=email,
        match_source="user_email_verification",
    )
    await redis_client.delete(code_key)

    logger.info(
        "[slack_user_mappings] Verified Slack user mapping org=%s user=%s slack_user=%s",
        org_uuid,
        user_uuid,
        slack_user_id,
    )
    return {"status": "verified"}


@router.delete("/user-mappings/{mapping_id}")
async def delete_slack_user_mapping(
    mapping_id: str,
    user_id: str,
    organization_id: str | None = None,
) -> dict[str, str]:
    org_uuid, user_uuid = await _resolve_org_and_user(user_id, organization_id)
    try:
        mapping_uuid = UUID(mapping_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid mapping ID") from exc

    async with get_session(organization_id=str(org_uuid)) as session:
        await session.execute(
            text("SELECT set_config('app.current_org_id', :org_id, true)"),
            {"org_id": str(org_uuid)},
        )
        result = await session.execute(
            select(SlackUserMapping)
            .where(SlackUserMapping.id == mapping_uuid)
            .where(SlackUserMapping.organization_id == org_uuid)
            .where(SlackUserMapping.user_id == user_uuid)
        )
        mapping = result.scalar_one_or_none()
        if not mapping:
            raise HTTPException(status_code=404, detail="Mapping not found")

        await session.delete(mapping)
        await session.commit()

    logger.info(
        "[slack_user_mappings] Deleted mapping id=%s org=%s user=%s",
        mapping_id,
        org_uuid,
        user_uuid,
    )
    return {"status": "deleted"}
