"""
Connector registry: auto-discovery, capability model, and metadata types.

ConnectorMeta is the single source of truth for what a connector is, what it
can do, and how it authenticates.  The discover_connectors() function scans
backend/connectors/ at import time and falls back to entry_points for
externally-installed packages.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from connectors.base import BaseConnector

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class Capability(Enum):
    """What a connector can do."""

    SYNC = "sync"
    QUERY = "query"
    WRITE = "write"
    ACTION = "action"
    LISTEN = "listen"


class AuthType(Enum):
    """How a connector authenticates with its source system."""

    OAUTH2 = "oauth2"
    API_KEY = "api_key"
    BEARER_TOKEN = "bearer_token"
    CUSTOM = "custom"


class ConnectorScope(Enum):
    """Whether a connector is org-wide or per-user."""

    ORGANIZATION = "organization"
    USER = "user"


# ---------------------------------------------------------------------------
# Metadata dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuthField:
    """A custom auth field rendered in the UI."""

    name: str
    label: str
    type: str = "string"
    required: bool = True
    help_text: str = ""


@dataclass(frozen=True)
class WriteOperation:
    """A CRUD operation on a record in the source system (idempotent)."""

    name: str
    entity_type: str
    description: str
    parameters: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class ConnectorAction:
    """A side-effect function the agent can invoke (not idempotent)."""

    name: str
    description: str
    parameters: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class EventType:
    """An inbound event this connector can receive."""

    name: str
    description: str


@dataclass(frozen=True)
class ConnectorMeta:
    """Self-describing metadata for a connector."""

    name: str
    slug: str
    auth_type: AuthType
    scope: ConnectorScope
    entity_types: list[str] = field(default_factory=list)
    capabilities: list[Capability] = field(default_factory=lambda: [Capability.SYNC])
    write_operations: list[WriteOperation] = field(default_factory=list)
    actions: list[ConnectorAction] = field(default_factory=list)
    event_types: list[EventType] = field(default_factory=list)
    query_description: str = ""
    oauth_scopes: list[str] = field(default_factory=list)
    auth_fields: list[AuthField] = field(default_factory=list)
    nango_integration_id: str | None = None
    description: str = ""
    icon: str = ""


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

_SKIP_MODULES = frozenset({"base", "resolution", "registry", "models", "persistence", "_template"})


def discover_connectors() -> dict[str, type[BaseConnector]]:
    """Build connector registry from in-tree modules + installed packages."""
    from connectors.base import BaseConnector  # deferred to avoid circular import

    registry: dict[str, type[BaseConnector]] = {}

    connectors_dir = Path(__file__).parent
    for module_info in pkgutil.iter_modules([str(connectors_dir)]):
        if module_info.name.startswith("_") or module_info.name in _SKIP_MODULES:
            continue
        try:
            module = importlib.import_module(f"connectors.{module_info.name}")
        except Exception:
            logger.warning("Failed to import connector module %s", module_info.name, exc_info=True)
            continue

        for attr_name in dir(module):
            obj = getattr(module, attr_name)
            if (
                isinstance(obj, type)
                and issubclass(obj, BaseConnector)
                and obj is not BaseConnector
                and hasattr(obj, "meta")
            ):
                meta: ConnectorMeta = obj.meta  # type: ignore[attr-defined]
                registry[meta.slug] = obj

    # Entry-points fallback for externally-installed connector packages
    try:
        from importlib.metadata import entry_points

        for ep in entry_points(group="revtops.connectors"):
            if ep.name not in registry:
                try:
                    connector_cls = ep.load()
                    registry[ep.name] = connector_cls
                except Exception:
                    logger.warning("Failed to load entry-point connector %s", ep.name, exc_info=True)
    except Exception:
        pass

    return registry
