"""
Connector metadata endpoint.

Serves the dynamically-discovered connector registry to the frontend so it
can render auth forms, capability badges, and the data-sources UI without
hardcoded provider lists.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter

from connectors.registry import discover_connectors

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
