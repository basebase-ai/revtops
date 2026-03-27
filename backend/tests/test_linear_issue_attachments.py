"""Tests for Linear issue creation with chat attachment uploads."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from connectors.linear import (
    LinearConnector,
    _normalize_uuid_string_list,
    _safe_markdown_alt_text,
)


def test_safe_markdown_alt_text_strips_brackets() -> None:
    assert _safe_markdown_alt_text("a[b]c.png") == "abc.png"


def test_normalize_uuid_string_list() -> None:
    assert _normalize_uuid_string_list(None) == []
    assert _normalize_uuid_string_list("  x  ") == ["x"]
    assert _normalize_uuid_string_list([" a ", "b"]) == ["a", "b"]
    assert _normalize_uuid_string_list({}) == []


@pytest.mark.asyncio
async def test_upload_bytes_to_linear(monkeypatch: pytest.MonkeyPatch) -> None:
    connector = LinearConnector(organization_id="00000000-0000-0000-0000-000000000001")

    async def fake_gql(_query: str, _variables: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "fileUpload": {
                "success": True,
                "uploadFile": {
                    "uploadUrl": "https://upload.example/put",
                    "assetUrl": "https://linear.example/asset",
                    "headers": [{"key": "X-Custom", "value": "1"}],
                },
            },
        }

    monkeypatch.setattr(connector, "_gql", fake_gql)

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

    fake_client = MagicMock()
    fake_client.put = AsyncMock(return_value=FakeResponse())
    fake_cm = MagicMock()
    fake_cm.__aenter__ = AsyncMock(return_value=fake_client)
    fake_cm.__aexit__ = AsyncMock(return_value=None)

    monkeypatch.setattr(
        "connectors.linear.httpx.AsyncClient",
        lambda *args, **kwargs: fake_cm,
    )

    asset: str = await connector._upload_bytes_to_linear(
        data=b"hello",
        filename="x.png",
        content_type="image/png",
    )
    assert asset == "https://linear.example/asset"
    fake_client.put.assert_awaited_once()
    call_kw: dict[str, Any] = fake_client.put.await_args.kwargs
    assert call_kw["content"] == b"hello"
    assert call_kw["headers"]["Content-Type"] == "image/png"
    assert call_kw["headers"]["X-Custom"] == "1"


@pytest.mark.asyncio
async def test_write_create_issue_filters_unknown_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    connector = LinearConnector(organization_id="00000000-0000-0000-0000-000000000001")
    captured: dict[str, Any] = {}

    async def fake_create_issue(**kwargs: Any) -> dict[str, Any]:
        captured.clear()
        captured.update(kwargs)
        return {"identifier": "ENG-1", "linear_issue_id": "i1", "title": "t", "url": "u"}

    monkeypatch.setattr(connector, "create_issue", fake_create_issue)
    await connector.write(
        "create_issue",
        {
            "team_key": "ENG",
            "title": "Hello",
            "unknown_field": "drop_me",
            "conversation_id": "00000000-0000-0000-0000-000000000002",
            "attachment_ids": ["00000000-0000-0000-0000-000000000003"],
        },
    )
    assert "unknown_field" not in captured
    assert captured["team_key"] == "ENG"
    assert captured["conversation_id"] == "00000000-0000-0000-0000-000000000002"
    assert captured["attachment_ids"] == ["00000000-0000-0000-0000-000000000003"]


@pytest.mark.asyncio
async def test_create_issue_appends_uploaded_markdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connector = LinearConnector(organization_id="00000000-0000-0000-0000-000000000001")
    aid: str = "11111111-1111-1111-1111-111111111111"
    conv: str = "22222222-2222-2222-2222-222222222222"

    async def fake_load(
        self: LinearConnector,
        *,
        conversation_id: str,
        attachment_ids: list[str],
    ) -> list[tuple[str, str, bytes]]:
        assert conversation_id == conv
        assert attachment_ids == [aid]
        return [("shot.png", "image/png", b"\x89PNG")]

    async def fake_upload(
        self: LinearConnector,
        *,
        data: bytes,
        filename: str,
        content_type: str,
    ) -> str:
        assert filename == "shot.png"
        return "https://files.linear.app/x"

    gql_calls: list[dict[str, Any]] = []

    async def fake_gql(query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        gql_calls.append({"query": query, "variables": variables or {}})
        if "issueCreate" in query:
            return {
                "issueCreate": {
                    "success": True,
                    "issue": {
                        "id": "iss1",
                        "identifier": "ENG-9",
                        "title": variables.get("title") if variables else "",
                        "url": "https://linear/issue",
                        "state": {"name": "Todo"},
                        "priority": 3,
                        "priorityLabel": "Medium",
                    },
                },
            }
        return {}

    monkeypatch.setattr(LinearConnector, "_load_chat_attachments_for_issue", fake_load)
    monkeypatch.setattr(LinearConnector, "_upload_bytes_to_linear", fake_upload)
    monkeypatch.setattr(connector, "_gql", fake_gql)
    monkeypatch.setattr(
        connector,
        "resolve_team_by_key",
        AsyncMock(return_value={"id": "team-1"}),
    )

    await connector.create_issue(
        team_key="ENG",
        title="Bug",
        description="See screenshot",
        conversation_id=conv,
        attachment_ids=[aid],
    )

    issue_mutations: list[dict[str, Any]] = [c for c in gql_calls if "issueCreate" in c["query"]]
    assert len(issue_mutations) == 1
    desc: str | None = issue_mutations[0]["variables"].get("description")
    assert desc is not None
    assert "See screenshot" in desc
    assert "![shot.png](https://files.linear.app/x)" in desc


@pytest.mark.asyncio
async def test_write_update_issue_filters_keys_and_passes_conversation_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connector = LinearConnector(organization_id="00000000-0000-0000-0000-000000000001")
    captured: dict[str, Any] = {}

    async def fake_update(**kwargs: Any) -> dict[str, Any]:
        captured.clear()
        captured.update(kwargs)
        return {
            "linear_issue_id": "x",
            "identifier": "BAS-497",
            "title": "t",
            "url": "u",
            "state": None,
            "priority": None,
            "priority_label": None,
        }

    monkeypatch.setattr(connector, "update_issue", fake_update)
    await connector.write(
        "update_issue",
        {
            "issue_identifier": "BAS-497",
            "state_name": "Canceled",
            "conversation_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
            "bogus": "nope",
        },
    )
    assert "bogus" not in captured
    assert captured.get("conversation_id") == "cccccccc-cccc-cccc-cccc-cccccccccccc"


@pytest.mark.asyncio
async def test_update_issue_attachment_only_appends_to_existing_description(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connector = LinearConnector(organization_id="00000000-0000-0000-0000-000000000001")
    monkeypatch.setattr(
        connector,
        "resolve_issue_by_identifier",
        AsyncMock(
            return_value={
                "id": "i1",
                "identifier": "BAS-497",
                "description": "Original body",
                "team": {"id": "t1", "key": "BAS"},
            },
        ),
    )
    monkeypatch.setattr(
        connector,
        "_markdown_block_from_chat_attachments",
        AsyncMock(return_value="![](https://asset)"),
    )
    gql_calls: list[tuple[str, dict[str, Any]]] = []

    async def fake_gql(query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        gql_calls.append((query, variables or {}))
        if "issueUpdate" in query:
            return {
                "issueUpdate": {
                    "success": True,
                    "issue": {
                        "id": "i1",
                        "identifier": "BAS-497",
                        "title": "T",
                        "url": "u",
                        "state": {"name": "Todo"},
                        "priority": 1,
                        "priorityLabel": "Urgent",
                    },
                },
            }
        return {}

    monkeypatch.setattr(connector, "_gql", fake_gql)
    await connector.update_issue(
        issue_identifier="BAS-497",
        conversation_id="22222222-2222-2222-2222-222222222222",
        attachment_ids=["11111111-1111-1111-1111-111111111111"],
    )
    update_calls: list[dict[str, Any]] = [v for q, v in gql_calls if "issueUpdate" in q]
    assert len(update_calls) == 1
    assert update_calls[0].get("input_description") == "Original body\n\n![](https://asset)"
