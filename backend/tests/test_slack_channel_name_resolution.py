import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from messengers.slack import SlackMessenger
from messengers.base import InboundMessage, MessageType

@pytest.mark.asyncio
async def test_resolve_channel_name_caching():
    messenger = SlackMessenger()
    workspace_id = "T123"
    channel_id = "C456"
    channel_name = "general"

    # Mock SlackConnector
    mock_connector = AsyncMock()
    mock_connector.get_channel_info.return_value = {"name": channel_name}

    with patch.object(messenger, "_get_connector", return_value=mock_connector):
        # First call - should fetch from API
        name1 = await messenger.resolve_channel_name(workspace_id, channel_id)
        assert name1 == channel_name
        assert mock_connector.get_channel_info.call_count == 1

        # Second call - should hit cache
        name2 = await messenger.resolve_channel_name(workspace_id, channel_id)
        assert name2 == channel_name
        assert mock_connector.get_channel_info.call_count == 1

@pytest.mark.asyncio
async def test_enrich_message_context_attaches_channel_name():
    messenger = SlackMessenger()
    workspace_id = "T123"
    channel_id = "C456"
    channel_name = "general"
    org_id = str(uuid4())

    message = InboundMessage(
        text="hello",
        message_type=MessageType.DIRECT,
        external_user_id="U123",
        message_id="123.456",
        messenger_context={
            "workspace_id": workspace_id,
            "channel_id": channel_id,
        }
    )

    # Mock resolve_channel_name
    with patch.object(messenger, "resolve_channel_name", return_value=channel_name):
        await messenger.enrich_message_context(message, org_id)
        assert message.messenger_context["channel_name"] == channel_name

@pytest.mark.asyncio
async def test_persist_channel_activity_uses_channel_name():
    messenger = SlackMessenger()
    workspace_id = "T123"
    channel_id = "C456"
    channel_name = "general"
    org_id = str(uuid4())

    message = InboundMessage(
        text="hello activity",
        message_type=MessageType.DIRECT,
        external_user_id="U123",
        message_id="1710711600.000", # Fixed TS for deterministic testing
        messenger_context={
            "workspace_id": workspace_id,
            "channel_id": channel_id,
            "channel_name": channel_name,
        }
    )

    # Mock get_session and pg_insert
    mock_session = AsyncMock()
    mock_insert_obj = MagicMock()
    mock_insert_obj.values.return_value = mock_insert_obj
    mock_insert_obj.on_conflict_do_nothing.return_value = mock_insert_obj

    with patch("messengers._workspace.get_session", return_value=MagicMock(__aenter__=AsyncMock(return_value=mock_session))), \
         patch("messengers._workspace.pg_insert", return_value=mock_insert_obj):
        
        await messenger.persist_channel_activity(message, org_id)
        
        # Verify pg_insert was called with Activity model
        from models.activity import Activity
        # args, kwargs = mock_insert_obj.call_args
        # Note: pg_insert is called as pg_insert(Activity)
        
        # Verify values() was called with correct subject and custom_fields
        assert mock_insert_obj.values.called
        values_args, values_kwargs = mock_insert_obj.values.call_args
        passed_values = values_args[0] if values_args else values_kwargs
        
        assert passed_values["subject"] == f"#{channel_name}"
        assert passed_values["custom_fields"]["channel_name"] == channel_name
        assert passed_values["custom_fields"]["channel_id"] == channel_id
        
@pytest.mark.asyncio
async def test_enrich_message_context_does_not_overwrite_existing():
    """Channel name already in context (e.g. from batch sync) should not be replaced."""
    messenger = SlackMessenger()
    org_id = str(uuid4())

    message = InboundMessage(
        text="hello",
        message_type=MessageType.DIRECT,
        external_user_id="U123",
        message_id="123.456",
        messenger_context={
            "workspace_id": "T123",
            "channel_id": "C456",
            "channel_name": "already-set",
        },
    )

    with patch.object(messenger, "resolve_channel_name", return_value="general"):
        await messenger.enrich_message_context(message, org_id)
        assert message.messenger_context["channel_name"] == "already-set"


@pytest.mark.asyncio
async def test_resolve_channel_name_failure_caching():
    messenger = SlackMessenger()
    workspace_id = "T123"
    channel_id = "C_INVALID"

    # Mock SlackConnector failure
    mock_connector = AsyncMock()
    mock_connector.get_channel_info.return_value = None

    with patch.object(messenger, "_get_connector", return_value=mock_connector):
        # First call - failure
        name1 = await messenger.resolve_channel_name(workspace_id, channel_id)
        assert name1 is None
        assert mock_connector.get_channel_info.call_count == 1

        # Second call - should still be None and hit cache (TTL for failure is 120s)
        name2 = await messenger.resolve_channel_name(workspace_id, channel_id)
        assert name2 is None
        assert mock_connector.get_channel_info.call_count == 1


@pytest.mark.asyncio
async def test_post_message_skips_duplicate_thread_message_when_whitespace_only_differs():
    messenger = SlackMessenger()
    mock_connector = AsyncMock()
    mock_connector.get_thread_messages.return_value = [
        {"ts": "1710711600.001", "text": "_Writing to Linear…_"}
    ]

    with patch.object(messenger, "_get_connector", return_value=mock_connector):
        result = await messenger.post_message(
            channel_id="C456",
            text="_Writing to  Linear…_",
            thread_id="1710711600.000",
            workspace_id="T123",
            organization_id=str(uuid4()),
        )

    assert result is None
    mock_connector.get_thread_messages.assert_awaited_once_with(
        channel_id="C456",
        thread_ts="1710711600.000",
        limit=1000,
    )
    mock_connector.post_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_post_message_sends_when_last_thread_message_differs():
    messenger = SlackMessenger()
    mock_connector = AsyncMock()
    mock_connector.get_thread_messages.return_value = [
        {"ts": "1710711600.001", "text": "_Looking up data in Github…_"}
    ]
    mock_connector.post_message.return_value = {"ts": "1710711600.002"}

    with patch.object(messenger, "_get_connector", return_value=mock_connector):
        result = await messenger.post_message(
            channel_id="C456",
            text="_Writing to Linear…_",
            thread_id="1710711600.000",
            workspace_id="T123",
            organization_id=str(uuid4()),
        )

    assert result == "1710711600.002"
    mock_connector.post_message.assert_awaited_once_with(
        channel="C456",
        text="_Writing to Linear…_",
        thread_ts="1710711600.000",
        blocks=None,
    )


@pytest.mark.asyncio
async def test_enrich_message_context_resolves_slack_mentions_via_mapping_table():
    messenger = SlackMessenger()
    org_id = str(uuid4())
    message = InboundMessage(
        text="who is <@UJON123>?",
        message_type=MessageType.MENTION,
        external_user_id="U_SENDER",
        message_id="123.456",
        messenger_context={"workspace_id": "T123", "channel_id": "C456"},
        mentions=[{"type": "user", "external_user_id": "UJON123"}],
    )

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.all.return_value = [("UJON123", "Jon Alferness", "", "T123")]
    mock_session.execute.return_value = mock_result
    mock_ctx = MagicMock(__aenter__=AsyncMock(return_value=mock_session), __aexit__=AsyncMock(return_value=None))

    with patch.object(messenger, "resolve_channel_name", return_value="general"), patch(
        "messengers.slack.get_admin_session", return_value=mock_ctx
    ):
        await messenger.enrich_message_context(message, org_id)

    assert message.text == "who is @Jon Alferness?"


@pytest.mark.asyncio
async def test_enrich_message_context_keeps_unresolved_mentions_unchanged():
    messenger = SlackMessenger()
    org_id = str(uuid4())
    original = "who is <@UUNKNOWN>?"
    message = InboundMessage(
        text=original,
        message_type=MessageType.MENTION,
        external_user_id="U_SENDER",
        message_id="123.456",
        messenger_context={"workspace_id": "T123", "channel_id": "C456"},
        mentions=[{"type": "user", "external_user_id": "UUNKNOWN"}],
    )

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.all.return_value = []
    mock_session.execute.return_value = mock_result
    mock_ctx = MagicMock(__aenter__=AsyncMock(return_value=mock_session), __aexit__=AsyncMock(return_value=None))

    with patch.object(messenger, "resolve_channel_name", return_value="general"), patch(
        "messengers.slack.get_admin_session", return_value=mock_ctx
    ):
        await messenger.enrich_message_context(message, org_id)

    assert message.text == original


@pytest.mark.asyncio
async def test_inject_recent_channel_context_appends_refreshed_snapshot():
    messenger = SlackMessenger()
    message = InboundMessage(
        text="hello",
        message_type=MessageType.DIRECT,
        external_user_id="U123",
        message_id="123.456",
        messenger_context={
            "workspace_id": "T123",
            "channel_id": "C456",
            "channel_type": "channel",
            "workflow_context": {
                "slack_recent_channel_context": "Slack snapshot fetched_at=2026-04-17T00:00:00+00:00\nprior snapshot",
            },
        },
    )

    mock_connector = AsyncMock()
    mock_connector.get_channel_messages.return_value = [
        {"ts": "1710711600.000", "user": "U_A", "text": "fresh message", "reply_count": 0}
    ]

    with patch.object(messenger, "_get_connector", return_value=mock_connector):
        await messenger._inject_recent_channel_context(
            message=message,
            workspace_id="T123",
            channel_id="C456",
        )

    updated = message.messenger_context["workflow_context"]["slack_recent_channel_context"]
    assert "prior snapshot" in updated
    assert "fresh message" in updated
    assert "\n\n---\n\n" in updated
    assert updated.count("Slack snapshot fetched_at=") == 2


def test_summarize_channel_history_if_needed_returns_original_when_within_limit():
    messenger = SlackMessenger()
    history = "short history"
    result = messenger._summarize_channel_history_if_needed(
        history_context=history,
        channel_messages=[],
        thread_expansions={},
    )
    assert result == history


def test_summarize_channel_history_if_needed_compresses_oversized_payload():
    messenger = SlackMessenger()
    channel_messages = [
        {
            "ts": "1710711600.100",
            "user": "U1",
            "text": "Kickoff update " + ("A" * 600),
            "thread_ts": "1710711600.100",
            "reply_count": 2,
        },
        {
            "ts": "1710711601.200",
            "user": "U2",
            "text": "Follow up " + ("B" * 600),
            "thread_ts": "1710711601.200",
            "reply_count": 1,
        },
    ]
    thread_expansions = {
        "1710711600.100": [
            {
                "ts": "1710711600.100",
                "user": "U1",
                "text": "Kickoff update " + ("A" * 600),
            },
            {
                "ts": "1710711600.300",
                "user": "U3",
                "text": "Reply in first thread " + ("C" * 300),
            },
            {
                "ts": "1710711600.400",
                "user": "U4",
                "text": "Another reply " + ("D" * 300),
            },
        ],
        "1710711601.200": [
            {
                "ts": "1710711601.200",
                "user": "U2",
                "text": "Follow up " + ("B" * 600),
            },
            {
                "ts": "1710711601.250",
                "user": "U5",
                "text": "Reply in second thread " + ("E" * 300),
            },
        ],
    }
    oversized_history = "X" * 26000

    result = messenger._summarize_channel_history_if_needed(
        history_context=oversized_history,
        channel_messages=channel_messages,
        thread_expansions=thread_expansions,
    )

    assert "quick summary of newest 300 channel messages" in result
    assert "Most active threads by reply count" in result
    assert len(result) <= 12000


def test_append_channel_snapshot_context_trims_to_max_chars():
    messenger = SlackMessenger()
    prior = "A" * 20000
    latest = "B" * 10000

    result = messenger._append_channel_snapshot_context(
        prior_snapshot_context=prior,
        latest_snapshot_context=latest,
    )

    assert len(result) == 24000
    assert result.endswith("B" * 10000)
