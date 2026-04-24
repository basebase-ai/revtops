from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest

from services.content_groups import _build_content_group_upsert, _normalize_content_group_key
from services.context_pack import build_incoming_message_context_pack


def test_normalize_content_group_key_slack_thread() -> None:
    out = _normalize_content_group_key(
        {
            "workspace_id": "T123",
            "channel_id": "C111",
            "thread_ts": "1710000.012",
            "channel_name": "eng",
        },
        "slack",
    )
    assert out is not None
    assert out["workspace_id"] == "T123"
    assert out["external_group_id"] == "C111"
    assert out["external_thread_id"] == "1710000.012"
    assert out["name"] == "eng"


def test_normalize_content_group_key_teams_reply_chain() -> None:
    out = _normalize_content_group_key(
        {
            "workspace_id": "tenant-1",
            "chat_id": "19:abc@thread.v2",
            "reply_to_id": "17000",
        },
        "teams",
    )
    assert out is not None
    assert out["external_group_id"] == "19:abc@thread.v2"
    assert out["external_thread_id"] == "17000"


def test_content_group_upsert_non_thread_uses_partial_unique_index() -> None:
    stmt = _build_content_group_upsert(
        organization_id=UUID("00000000-0000-0000-0000-000000000000"),
        platform="slack",
        workspace_id="T123",
        external_group_id="C123",
        external_thread_id=None,
        name="eng",
    )
    sql = str(stmt)
    assert "ON CONFLICT (organization_id, platform, workspace_id, external_group_id)" in sql
    assert "WHERE external_thread_id IS NULL" in sql


def test_content_group_upsert_thread_uses_full_unique_constraint() -> None:
    stmt = _build_content_group_upsert(
        organization_id=UUID("00000000-0000-0000-0000-000000000000"),
        platform="slack",
        workspace_id="T123",
        external_group_id="C123",
        external_thread_id="1710000.01",
        name="eng",
    )
    sql = str(stmt)
    assert "ON CONFLICT ON CONSTRAINT uq_content_groups_key" in sql


@pytest.mark.asyncio
async def test_context_pack_trims_to_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(UTC)
    summary = SimpleNamespace(
        id="s1",
        content_group_id="g1",
        summary_text="x" * 5000,
        first_message_at=now,
        last_message_at=now,
        first_message_external_id="m1",
        last_message_external_id="m2",
        summarized_through_at=now,
    )

    async def _source(**kwargs):  # type: ignore[no-untyped-def]
        return [summary]

    async def _camp(**kwargs):  # type: ignore[no-untyped-def]
        return [summary]

    monkeypatch.setattr("services.context_pack.list_recent_summaries", _source)
    monkeypatch.setattr("services.context_pack.list_campfire_context_summaries", _camp)

    pack = await build_incoming_message_context_pack(
        organization_id="00000000-0000-0000-0000-000000000000",
        content_group_id="11111111-1111-1111-1111-111111111111",
        token_budget_chars=300,
    )
    assert pack is not None
    assert len(pack["context_text"]) == 300
