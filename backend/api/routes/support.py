"""
Support request endpoint for in-app help.

Sends user messages to Slack (immediate) or email (fallback) so a human
can respond quickly during business hours.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from api.auth_middleware import AuthContext, require_organization
from config import settings
from services.email import send_email

router = APIRouter()
logger = logging.getLogger(__name__)


class SupportRequest(BaseModel):
    """Payload for a support request."""

    message: str = Field(..., min_length=1, max_length=4000)


@router.post("/request", response_model=dict[str, str])
async def submit_support_request(
    body: SupportRequest,
    auth: AuthContext = Depends(require_organization),
) -> dict[str, str]:
    """
    Submit a support request. Notifies the team immediately via Slack
    (if configured) or email to support@basebase.com.
    """
    user_email: str = auth.email or ""
    org_id_str: str = auth.organization_id_str or ""
    formatted_message: str = (
        f"*Support request from:* {user_email}\n"
        f"*Org ID:* {org_id_str}\n"
        f"*Message:*\n{body.message}"
    )

    if settings.SUPPORT_SLACK_WEBHOOK_URL:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                payload: dict[str, Any] = {"text": formatted_message}
                resp = await client.post(
                    settings.SUPPORT_SLACK_WEBHOOK_URL,
                    json=payload,
                )
                if resp.status_code != 200:
                    logger.warning(
                        "[support] Slack webhook returned %s: %s",
                        resp.status_code,
                        resp.text[:200],
                    )
                    # Fall through to email
                else:
                    logger.info("[support] Posted to Slack for %s", user_email)
                    return {"status": "ok", "detail": "Your message has been sent. A team member will respond within a few minutes during business hours."}
        except Exception as e:
            logger.exception("[support] Slack webhook failed: %s", e)
            # Fall through to email

    # Fallback: email
    if settings.RESEND_API_KEY:
        sent = await send_email(
            to="support@basebase.com",
            subject=f"Support request: {user_email} ({org_id_str})",
            body=formatted_message.replace("*", ""),
            reply_to=user_email if user_email else None,
        )
        if sent:
            logger.info("[support] Sent email for %s", user_email)
            return {"status": "ok", "detail": "Your message has been sent. A team member will respond within a few minutes during business hours."}

    logger.error("[support] No Slack webhook or Resend configured; support request dropped")
    return {"status": "ok", "detail": "Your message has been recorded. A team member will respond within a few minutes during business hours."}
