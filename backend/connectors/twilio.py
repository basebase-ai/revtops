"""
Twilio connector – SMS messaging as a togglable connector.

Wraps Twilio SMS sending so organizations can enable/disable SMS for the agent.
"""

import logging
import re
from typing import Any

from connectors.base import BaseConnector
from connectors.registry import (
    AuthType,
    Capability,
    ConnectorAction,
    ConnectorMeta,
    ConnectorScope,
)

logger = logging.getLogger(__name__)


class TwilioConnector(BaseConnector):
    """SMS messaging via Twilio, togglable per organization."""

    source_system: str = "twilio"
    meta = ConnectorMeta(
        name="Twilio",
        slug="twilio",
        auth_type=AuthType.CUSTOM,
        scope=ConnectorScope.ORGANIZATION,
        capabilities=[Capability.ACTION],
        actions=[
            ConnectorAction(
                name="send_sms",
                description="Send an SMS text message to a phone number.",
                parameters=[
                    {"name": "to", "type": "string", "required": True, "description": "Phone number in E.164 format (e.g. +14155551234)"},
                    {"name": "body", "type": "string", "required": True, "description": "Message text (max 1600 characters)"},
                ],
            ),
        ],
        description="SMS messaging via Twilio",
    )

    # Stub abstract methods – no CRM entities
    async def sync_deals(self) -> int:
        return 0

    async def sync_accounts(self) -> int:
        return 0

    async def sync_contacts(self) -> int:
        return 0

    async def sync_activities(self) -> int:
        return 0

    async def fetch_deal(self, deal_id: str) -> dict[str, Any]:
        return {}

    # -----------------------------------------------------------------
    # ACTION – send_sms
    # -----------------------------------------------------------------

    async def execute_action(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        if action != "send_sms":
            raise ValueError(f"Unknown action: {action}")
        return await self._send_sms(params)

    async def _send_sms(self, params: dict[str, Any]) -> dict[str, Any]:
        from services.sms import send_sms

        to: str = (params.get("to") or "").strip()
        body: str = (params.get("body") or "").strip()

        if not to:
            return {"error": "to is required (E.164 phone number, e.g. +14155551234)."}
        if not body:
            return {"error": "body is required."}

        digits_only: str = re.sub(r"[^\d]", "", to)
        if not to.startswith("+"):
            if len(digits_only) == 10:
                digits_only = f"1{digits_only}"
            to = f"+{digits_only}"

        if len(digits_only) < 7 or len(digits_only) > 15:
            return {"error": f"Invalid phone number '{to}'. Expected E.164 format, e.g. +14155551234."}

        result: dict[str, str | bool] = await send_sms(to=to, body=body)

        if result.get("success"):
            return {"status": "sent", "to": to, "message_sid": result.get("message_sid")}
        return {"error": result.get("error", "Failed to send SMS.")}
