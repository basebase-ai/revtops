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

from config import get_redis_connection_kwargs, settings
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
    external_userid: str | None
    external_email: str | None
    source: str
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
        _redis_client = redis.from_url(
            settings.REDIS_URL, **get_redis_connection_kwargs(decode_responses=True)
        )
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
                Integration.connector == "slack",
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
            "[user_mappings_for_identity] Normalized email from '%s' to '%s'",
            original,
            normalized,
        )
    if not slack_conversations.EMAIL_PATTERN.fullmatch(normalized):
        raise HTTPException(status_code=400, detail="Invalid email address")
    return normalized


@router.get("/user-mappings", response_model=SlackMappingListResponse)
async def list_user_mappings_for_identity(
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
            .where(SlackUserMapping.source == "slack")
            .where(SlackUserMapping.user_id == user_uuid)
            .order_by(SlackUserMapping.created_at.desc())
        )
        mappings = result.scalars().all()

    response = [
        SlackMappingResponse(
            id=str(mapping.id),
            external_userid=mapping.external_userid,
            external_email=mapping.external_email,
            source=mapping.source,
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
            .where(SlackUserMapping.source == "slack")
            .where(SlackUserMapping.external_email == email)
            .order_by(SlackUserMapping.updated_at.desc())
        )
        matched_mappings = result.scalars().all()

    slack_user_candidates: list[str] = []
    seen_candidates: set[str] = set()
    for mapping in matched_mappings:
        candidate = (mapping.external_userid or "").strip()
        if not candidate or candidate in seen_candidates:
            continue
        seen_candidates.add(candidate)
        slack_user_candidates.append(candidate)

    logger.info(
        "[user_mappings_for_identity] Lookup Slack mapping for org=%s user=%s email=%s matched=%s",
        org_uuid,
        user_uuid,
        email,
        bool(slack_user_candidates),
    )

    if not slack_user_candidates:
        logger.warning(
            "[user_mappings_for_identity] No Slack mapping found for org=%s user=%s email=%s",
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
            "[user_mappings_for_identity] Slack verification code cooldown active org=%s user=%s",
            org_uuid,
            user_uuid,
        )
        raise HTTPException(
            status_code=429,
            detail="Please wait at least one minute before requesting another code.",
        )
    code = f"{secrets.randbelow(1000000):06d}"
    logger.info(
        "[user_mappings_for_identity] Sending Slack verification code org=%s user=%s candidate_count=%d",
        org_uuid,
        user_uuid,
        len(slack_user_candidates),
    )
    message_text = (
        "Your Basebase verification code is: "
        f"{code}\n\nIf you didn't request this, you can ignore it."
    )
    connector = SlackConnector(organization_id=str(org_uuid))

    last_send_error: Exception | None = None
    sent_slack_user_id: str | None = None
    action_response: dict[str, object] | None = None
    for idx, slack_user_id in enumerate(slack_user_candidates, start=1):
        try:
            logger.info(
                "[user_mappings_for_identity] Attempting Slack verification DM org=%s user=%s slack_user=%s attempt=%d/%d",
                org_uuid,
                user_uuid,
                slack_user_id,
                idx,
                len(slack_user_candidates),
            )
            action_response = await connector.send_direct_message(
                slack_user_id=slack_user_id,
                text=message_text,
            )
            sent_slack_user_id = slack_user_id
            logger.info(
                "[user_mappings_for_identity] Slack verification DM succeeded org=%s user=%s slack_user=%s attempt=%d/%d",
                org_uuid,
                user_uuid,
                slack_user_id,
                idx,
                len(slack_user_candidates),
            )
            break
        except Exception as exc:
            last_send_error = exc
            logger.warning(
                "[user_mappings_for_identity] Slack verification DM failed org=%s user=%s slack_user=%s attempt=%d/%d error=%s",
                org_uuid,
                user_uuid,
                slack_user_id,
                idx,
                len(slack_user_candidates),
                exc,
                exc_info=True,
            )

    if not sent_slack_user_id:
        await redis_client.delete(cooldown_key)
        raise HTTPException(
            status_code=502,
            detail="Unable to deliver verification code via Slack DM. Please try again.",
        ) from last_send_error

    code_key = f"revtops:slack-email-verify:{org_uuid}:{user_uuid}:{email}"
    payload = json.dumps(
        {
            "slack_user_id": sent_slack_user_id,
            "email": email,
            "code": code,
        }
    )
    await redis_client.set(code_key, payload, ex=int(_CODE_TTL.total_seconds()))

    logger.info(
        "[user_mappings_for_identity] Slack DM send response org=%s user=%s slack_user=%s keys=%s",
        org_uuid,
        user_uuid,
        sent_slack_user_id,
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
        "[user_mappings_for_identity] Verified Slack user mapping org=%s user=%s slack_user=%s",
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
            .where(SlackUserMapping.source == "slack")
            .where(SlackUserMapping.user_id == user_uuid)
        )
        mapping = result.scalar_one_or_none()
        if not mapping:
            raise HTTPException(status_code=404, detail="Mapping not found")

        await session.delete(mapping)
        await session.commit()

    logger.info(
        "[user_mappings_for_identity] Deleted mapping id=%s org=%s user=%s",
        mapping_id,
        org_uuid,
        user_uuid,
    )
    return {"status": "deleted"}
