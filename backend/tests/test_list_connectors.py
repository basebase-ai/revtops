"""Tests for the connector list endpoint and connection_flow classifier."""
from __future__ import annotations

import asyncio

from api.routes import connectors as connectors_route
from connectors.registry import discover_connectors


def _list() -> list[dict]:
    return asyncio.run(connectors_route.list_connectors())


def test_list_hides_code_sandbox() -> None:
    slugs = {c["slug"] for c in _list()}
    assert "code_sandbox" not in slugs, "code_sandbox must not appear in the public connector list"
    assert "apps" in slugs
    assert "web_search" in slugs
    assert "artifacts" in slugs


def test_builtin_no_auth_fields_classified_as_builtin() -> None:
    by_slug = {c["slug"]: c for c in _list()}
    for slug in ("apps", "web_search", "artifacts", "twilio"):
        assert by_slug[slug]["connection_flow"] == "builtin", (
            f"{slug} should be classified as builtin (no credentials needed)"
        )


def test_builtin_with_auth_fields_classified_as_custom_credentials() -> None:
    by_slug = {c["slug"]: c for c in _list()}
    for slug in ("mcp", "ispot_tv"):
        assert by_slug[slug]["connection_flow"] == "custom_credentials", (
            f"{slug} should be classified as custom_credentials (user provides credentials)"
        )


def test_oauth_connectors_classified_as_oauth() -> None:
    by_slug = {c["slug"]: c for c in _list()}
    for slug in ("slack", "salesforce", "hubspot", "github"):
        if slug in by_slug:
            assert by_slug[slug]["connection_flow"] == "oauth", (
                f"{slug} should be classified as oauth"
            )


def test_connection_flow_helper_direct() -> None:
    """Sanity check against the registry meta directly."""
    registry = discover_connectors()
    for slug, cls in registry.items():
        meta = cls.meta
        flow = connectors_route._connection_flow(meta)
        assert flow in {"oauth", "builtin", "custom_credentials"}
