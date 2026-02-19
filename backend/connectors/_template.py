"""
Template connector demonstrating the new-style return-type pattern.

Copy this file as a starting point for a new connector.  Fill in the
``meta`` attribute and implement the ``sync_*`` / ``query`` / ``write`` /
``execute_action`` methods that apply.  Delete the ones you don't need.

New-style connectors return typed Pydantic record objects from their
``sync_*`` methods.  The sync engine persists them automatically – no
direct DB access required.
"""

from __future__ import annotations

import logging
from typing import Any

from connectors.base import BaseConnector
from connectors.models import AccountRecord, ActivityRecord, ContactRecord, DealRecord
from connectors.registry import (
    AuthType,
    Capability,
    ConnectorAction,
    ConnectorMeta,
    ConnectorScope,
    WriteOperation,
)

logger = logging.getLogger(__name__)


class TemplateConnector(BaseConnector):
    """Example connector – replace with your implementation."""

    source_system = "template"

    meta = ConnectorMeta(
        name="Template",
        slug="template",
        description="A starter template for building new connectors",
        auth_type=AuthType.API_KEY,
        scope=ConnectorScope.ORGANIZATION,
        entity_types=["deals", "accounts", "contacts", "activities"],
        capabilities=[Capability.SYNC, Capability.QUERY, Capability.WRITE, Capability.ACTION],
        write_operations=[
            WriteOperation(
                name="create_deal",
                entity_type="deal",
                description="Create a deal in the source system",
                parameters=[
                    {"name": "name", "type": "string", "required": True},
                    {"name": "amount", "type": "number", "required": False},
                ],
            ),
        ],
        actions=[
            ConnectorAction(
                name="send_notification",
                description="Send a notification via the source system",
                parameters=[
                    {"name": "message", "type": "string", "required": True},
                ],
            ),
        ],
        auth_fields=[],
    )

    # ------------------------------------------------------------------
    # SYNC capability – return typed Pydantic objects
    # ------------------------------------------------------------------

    async def sync_deals(self) -> list[DealRecord]:
        """Fetch deals from the source API and return as typed records."""
        # Replace with real API calls
        raw_deals: list[dict[str, Any]] = []  # await self._fetch_deals()
        return [
            DealRecord(
                source_id=d["id"],
                name=d["name"],
                amount=d.get("amount"),
                stage=d.get("stage"),
                source_system=self.source_system,
            )
            for d in raw_deals
        ]

    async def sync_accounts(self) -> list[AccountRecord]:
        raw_accounts: list[dict[str, Any]] = []
        return [
            AccountRecord(
                source_id=a["id"],
                name=a["name"],
                domain=a.get("domain"),
                source_system=self.source_system,
            )
            for a in raw_accounts
        ]

    async def sync_contacts(self) -> list[ContactRecord]:
        raw_contacts: list[dict[str, Any]] = []
        return [
            ContactRecord(
                source_id=c["id"],
                name=c.get("name"),
                email=c.get("email"),
                source_system=self.source_system,
            )
            for c in raw_contacts
        ]

    async def sync_activities(self) -> list[ActivityRecord]:
        raw_activities: list[dict[str, Any]] = []
        return [
            ActivityRecord(
                source_id=a["id"],
                type=a.get("type", "note"),
                subject=a.get("subject"),
                description=a.get("body"),
                activity_date=a.get("date"),
                source_system=self.source_system,
            )
            for a in raw_activities
        ]

    async def fetch_deal(self, deal_id: str) -> dict[str, Any]:
        """Fetch a single deal on-demand (legacy abstract method)."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # QUERY capability
    # ------------------------------------------------------------------

    async def get_schema(self) -> list[dict[str, Any]]:
        """Return queryable schema metadata."""
        return [
            {"entity": "deals", "fields": ["id", "name", "amount", "stage"]},
            {"entity": "contacts", "fields": ["id", "name", "email"]},
        ]

    async def query(self, request: str) -> dict[str, Any]:
        """Execute an on-demand query."""
        return {"results": [], "query": request}

    # ------------------------------------------------------------------
    # WRITE capability
    # ------------------------------------------------------------------

    async def write(self, operation: str, data: dict[str, Any]) -> dict[str, Any]:
        """Dispatch a record-level write operation."""
        if operation == "create_deal":
            return await self._create_deal(data)
        raise ValueError(f"Unknown write operation: {operation}")

    async def _create_deal(self, data: dict[str, Any]) -> dict[str, Any]:
        # Replace with real API call
        return {"id": "new-deal-id", "status": "created"}

    # ------------------------------------------------------------------
    # ACTION capability
    # ------------------------------------------------------------------

    async def execute_action(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        """Execute a side-effect action."""
        if action == "send_notification":
            return await self._send_notification(params)
        raise ValueError(f"Unknown action: {action}")

    async def _send_notification(self, params: dict[str, Any]) -> dict[str, Any]:
        # Replace with real API call
        message: str = params.get("message", "")
        return {"status": "sent", "message": message}
