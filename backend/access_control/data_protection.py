"""
Data Protection Layer stub.

Interposes on connector sync and tool calls so future modules can inject
credentials or block connector operations per org/user/context.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ConnectorContext:
    """Context for connector access checks."""

    organization_id: str
    user_id: str | None
    provider: str
    operation: str  # "sync" | "query" | "write" | "action"


@dataclass(frozen=True)
class DataProtectionResult:
    """Result of a data protection check: allow/deny and optional transformed payload."""

    allowed: bool
    deny_reason: str | None = None
    transformed_payload: dict[str, Any] | None = None
    injected_credentials: dict[str, Any] | None = None


async def check_connector_call(
    context: ConnectorContext,
    payload: dict[str, Any] | None = None,
) -> DataProtectionResult:
    """
    Check whether a connector call (sync, query, write, action) is allowed.

    Stub: always allows and passes through unchanged (caller uses original payload).
    """
    return DataProtectionResult(allowed=True)
