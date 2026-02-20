"""
Connector metadata and webhook endpoint.

- Serves the dynamically-discovered connector registry to the frontend.
- Single webhook route: POST /webhook/{provider}/{organization_id}. Connectors
  that support LISTEN and set webhook_secret_extra_data_key in meta handle
  verification and payload parsing; this route dispatches and emits workflow events.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from connectors.registry import Capability, discover_connectors
from models.database import get_session
from models.integration import Integration
from workers.events import emit_event

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("")
async def list_connectors() -> list[dict[str, Any]]:
    """Return metadata for every registered connector."""
    registry = discover_connectors()
    result: list[dict[str, Any]] = []

    for slug, cls in sorted(registry.items()):
        meta = cls.meta  # type: ignore[attr-defined]
        result.append({
            "slug": meta.slug,
            "name": meta.name,
            "description": meta.description,
            "auth_type": meta.auth_type.value,
            "scope": meta.scope.value,
            "entity_types": meta.entity_types,
            "capabilities": [c.value for c in meta.capabilities],
            "write_operations": [
                {"name": op.name, "entity_type": op.entity_type, "description": op.description}
                for op in meta.write_operations
            ],
            "actions": [
                {"name": a.name, "description": a.description}
                for a in meta.actions
            ],
            "event_types": [
                {"name": e.name, "description": e.description}
                for e in meta.event_types
            ],
            "query_description": meta.query_description,
            "auth_fields": [
                {"name": f.name, "label": f.label, "type": f.type, "required": f.required, "help_text": f.help_text}
                for f in meta.auth_fields
            ],
            "icon": meta.icon,
        })

    return result


@router.post("/webhook/{provider}/{organization_id}", response_model=None)
async def handle_connector_webhook(
    request: Request, provider: str, organization_id: str
) -> dict[str, str]:
    """
    Generic webhook endpoint for LISTEN connectors. URL format:
    https://<api>/api/connectors/webhook/<provider>/<organization_id>
    e.g. .../webhook/linear/<org_uuid>. Configure the provider's webhook to point
    here and store the signing secret in integration.extra_data under the key
    defined by the connector's meta.webhook_secret_extra_data_key.
    """
    raw_body: bytes = await request.body()
    headers_dict: dict[str, str] = dict(request.headers)

    try:
        org_uuid: UUID = UUID(organization_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid organization_id")

    registry: dict[str, type[Any]] = discover_connectors()
    connector_cls: type[Any] | None = registry.get(provider)
    if not connector_cls or not hasattr(connector_cls, "meta"):
        raise HTTPException(status_code=404, detail="Connector not found")
    meta = connector_cls.meta
    if Capability.LISTEN not in meta.capabilities or not meta.webhook_secret_extra_data_key:
        raise HTTPException(
            status_code=404,
            detail="Connector does not accept webhooks",
        )

    async with get_session(organization_id=organization_id) as session:
        result = await session.execute(
            select(Integration).where(
                Integration.organization_id == org_uuid,
                Integration.provider == provider,
                Integration.is_active == True,
            )
        )
        integration: Integration | None = result.scalar_one_or_none()

    if not integration:
        logger.warning(
            "[connectors] No active %s integration for org %s", provider, organization_id
        )
        raise HTTPException(status_code=404, detail="Integration not found")

    extra: dict[str, Any] | None = integration.extra_data
    raw_secret: Any = (extra or {}).get(meta.webhook_secret_extra_data_key)
    secret: str | None = raw_secret if isinstance(raw_secret, str) and raw_secret else None
    if not secret:
        logger.warning(
            "[connectors] No webhook secret for %s org %s (key=%s)",
            provider,
            organization_id,
            meta.webhook_secret_extra_data_key,
        )
        raise HTTPException(
            status_code=503,
            detail=f"Webhook secret not configured. Set extra_data.{meta.webhook_secret_extra_data_key}.",
        )

    if not connector_cls.verify_webhook(raw_body, headers_dict, secret):
        logger.warning("[connectors] Invalid webhook signature for %s org %s", provider, organization_id)
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload: dict[str, Any] = json.loads(raw_body.decode("utf-8"))
    except json.JSONDecodeError as e:
        logger.warning("[connectors] Invalid webhook JSON (%s): %s", provider, e)
        raise HTTPException(status_code=400, detail="Invalid JSON")

    events: list[tuple[str, dict[str, Any]]] = connector_cls.process_webhook_payload(payload)
    for event_type, data in events:
        await emit_event(
            event_type=event_type,
            organization_id=organization_id,
            data=data,
        )
        logger.info(
            "[connectors] Emitted %s for %s org %s",
            event_type,
            provider,
            organization_id,
        )

    return {"ok": "true"}
