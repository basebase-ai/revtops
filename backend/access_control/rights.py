"""
Rights Management stub.

Interposes on SQL and external API calls so future modules can inject
secrets/keys per user/context or block queries/API calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RightsContext:
    """Context for rights checks (org, user, conversation, workflow)."""

    organization_id: str
    user_id: str | None
    conversation_id: str | None = None
    is_workflow: bool = False


@dataclass(frozen=True)
class RightsResult:
    """Result of a rights check: allow/deny and optional transformed payload."""

    allowed: bool
    deny_reason: str | None = None
    transformed_query: str | None = None
    transformed_params: dict[str, Any] | None = None
    injected_headers: dict[str, str] | None = None
    injected_secrets: dict[str, Any] | None = None


async def check_sql(
    context: RightsContext,
    query: str,
    params: dict[str, Any] | None = None,
) -> RightsResult:
    """
    Check whether a SQL operation is allowed; optionally return transformed query/params.

    Stub: always allows and passes through unchanged (caller uses original query/params).
    """
    return RightsResult(allowed=True)


async def check_external_api(
    context: RightsContext,
    service: str,
    payload: dict[str, Any] | None = None,
) -> RightsResult:
    """
    Check whether an external API call (LLM, embeddings, web search, etc.) is allowed.

    service: e.g. "anthropic", "openai", "openai_embeddings", "perplexity", "exa".
    Stub: always allows and passes through unchanged.
    """
    return RightsResult(allowed=True)
