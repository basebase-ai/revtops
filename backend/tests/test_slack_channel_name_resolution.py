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
        raw_attachments=[
            {
                "id": "F123",
                "name": "proposal.docx",
                "url_private_download": "https://files.slack.com/files-pri/T1-F123/download/proposal.docx",
                "mimetype": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            }
        ],
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
        assert passed_values["custom_fields"]["files"][0]["id"] == "F123"
        assert passed_values["custom_fields"]["files"][0]["name"] == "proposal.docx"
        
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
        message_type=MessageType.MENTION,
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


@pytest.mark.asyncio
async def test_inject_recent_channel_context_only_appends_new_cached_messages():
    messenger = SlackMessenger()
    org_id = str(uuid4())
    message = InboundMessage(
        text="hello",
        message_type=MessageType.MENTION,
        external_user_id="U123",
        message_id="123.456",
        messenger_context={
            "workspace_id": "T123",
            "channel_id": "C456",
            "organization_id": org_id,
            "channel_type": "channel",
            "workflow_context": {
                "slack_recent_channel_context": "Slack snapshot fetched_at=2026-04-17T00:00:00+00:00\nprior snapshot",
                "slack_recent_channel_latest_ts": "1710711600.100",
            },
        },
    )

    cached_payload = (
        [
            {"ts": "1710711600.200", "thread_ts": "1710711600.200", "user": "U_A", "text": "new cached message"},
            {"ts": "1710711600.050", "thread_ts": "1710711600.050", "user": "U_B", "text": "old cached message"},
        ],
        {},
    )
    with patch.object(
        messenger,
        "_get_cached_channel_context_payload_from_activity",
        AsyncMock(return_value=cached_payload),
    ):
        await messenger._inject_recent_channel_context(
            message=message,
            workspace_id="T123",
            channel_id="C456",
        )

    updated = message.messenger_context["workflow_context"]["slack_recent_channel_context"]
    assert "prior snapshot" in updated
    assert "new cached message" in updated
    assert "old cached message" not in updated
    assert message.messenger_context["workflow_context"]["slack_recent_channel_latest_ts"] == "1710711600.200"


@pytest.mark.asyncio
async def test_inject_recent_channel_context_skips_append_when_no_new_cached_messages():
    messenger = SlackMessenger()
    org_id = str(uuid4())
    prior_context = "Slack snapshot fetched_at=2026-04-17T00:00:00+00:00\nprior snapshot"
    message = InboundMessage(
        text="hello",
        message_type=MessageType.MENTION,
        external_user_id="U123",
        message_id="123.456",
        messenger_context={
            "workspace_id": "T123",
            "channel_id": "C456",
            "organization_id": org_id,
            "channel_type": "channel",
            "workflow_context": {
                "slack_recent_channel_context": prior_context,
                "slack_recent_channel_latest_ts": "1710711600.300",
            },
        },
    )

    cached_payload = (
        [
            {"ts": "1710711600.100", "thread_ts": "1710711600.100", "user": "U_A", "text": "older message"},
            {"ts": "1710711600.200", "thread_ts": "1710711600.200", "user": "U_B", "text": "still old"},
        ],
        {},
    )
    with patch.object(
        messenger,
        "_get_cached_channel_context_payload_from_activity",
        AsyncMock(return_value=cached_payload),
    ):
        await messenger._inject_recent_channel_context(
            message=message,
            workspace_id="T123",
            channel_id="C456",
        )

    assert message.messenger_context["workflow_context"]["slack_recent_channel_context"] == prior_context
    assert message.messenger_context["workflow_context"]["slack_recent_channel_latest_ts"] == "1710711600.300"


@pytest.mark.asyncio
async def test_inject_recent_channel_context_skips_direct_messages():
    messenger = SlackMessenger()
    message = InboundMessage(
        text="hello",
        message_type=MessageType.DIRECT,
        external_user_id="U123",
        message_id="123.456",
        messenger_context={
            "workspace_id": "T123",
            "channel_id": "D456",
            "organization_id": str(uuid4()),
        },
    )

    with (
        patch.object(messenger, "_get_connector", AsyncMock(side_effect=AssertionError("should not fetch connector"))),
        patch.object(
            messenger,
            "_get_cached_channel_context_payload_from_activity",
            AsyncMock(side_effect=AssertionError("should not read channel cache for DMs")),
        ),
    ):
        await messenger._inject_recent_channel_context(
            message=message,
            workspace_id="T123",
            channel_id="D456",
        )

    assert "workflow_context" not in message.messenger_context


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

    assert "quick summary of newest 100 channel messages" in result
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


@pytest.mark.asyncio
async def test_inject_recent_channel_context_prefers_activity_cache():
    messenger = SlackMessenger()
    workspace_id = "T123"
    channel_id = "C456"

    inbound_message = InboundMessage(
        text="new message",
        message_type=MessageType.MENTION,
        external_user_id="U999",
        message_id="1710711700.000",
        messenger_context={
            "workspace_id": workspace_id,
            "channel_id": channel_id,
            "channel_type": "channel",
            "organization_id": str(uuid4()),
        },
    )
    mock_result = MagicMock()
    mock_result.all.return_value = [
        (
            f"{channel_id}:1710711600.000",
            "cached msg 1",
            {"channel_id": channel_id, "user_id": "U1", "thread_ts": "1710711600.000"},
            None,
            None,
        ),
        (
            f"{channel_id}:1710711600.100",
            "cached msg 2",
            {"channel_id": channel_id, "user_id": "U2", "thread_ts": "1710711600.000"},
            None,
            None,
        ),
    ]
    mock_session = AsyncMock()
    mock_session.execute.return_value = mock_result
    mock_session_ctx = MagicMock(
        __aenter__=AsyncMock(return_value=mock_session),
        __aexit__=AsyncMock(return_value=None),
    )
    mock_connector = AsyncMock()
    mock_connector.get_channel_messages.side_effect = AssertionError("should use activity cache first")

    with (
        patch.object(messenger, "_get_connector", return_value=mock_connector),
        patch("messengers.slack.get_admin_session", return_value=mock_session_ctx),
    ):
        await messenger._inject_recent_channel_context(
            message=inbound_message,
            workspace_id=workspace_id,
            channel_id=channel_id,
        )

    context_payload = inbound_message.messenger_context["workflow_context"]["slack_recent_channel_context"]
    assert "cached msg" in context_payload


@pytest.mark.asyncio
async def test_inject_recent_channel_context_falls_back_when_cached_payload_is_empty():
    messenger = SlackMessenger()
    message = InboundMessage(
        text="hello",
        message_type=MessageType.MENTION,
        external_user_id="U123",
        message_id="123.456",
        messenger_context={
            "workspace_id": "T123",
            "channel_id": "C456",
            "channel_type": "channel",
            "organization_id": str(uuid4()),
        },
    )

    mock_connector = AsyncMock()
    mock_connector.get_channel_messages.return_value = [
        {"ts": "1710711600.000", "user": "U_A", "text": "live message", "reply_count": 0}
    ]

    with (
        patch.object(messenger, "_get_connector", return_value=mock_connector),
        patch.object(
            messenger,
            "_get_cached_channel_context_payload_from_activity",
            AsyncMock(return_value=([], {})),
        ),
    ):
        await messenger._inject_recent_channel_context(
            message=message,
            workspace_id="T123",
            channel_id="C456",
        )

    mock_connector.get_channel_messages.assert_awaited_once()
    context_payload = message.messenger_context["workflow_context"]["slack_recent_channel_context"]
    assert "live message" in context_payload


def test_build_channel_context_payload_groups_thread_without_parent_message():
    messenger = SlackMessenger()
    payload = messenger._build_channel_context_payload_from_cached_messages(
        [
            {
                "ts": "1710711600.300",
                "thread_ts": "1710711600.100",
                "user": "U2",
                "text": "reply one",
            },
            {
                "ts": "1710711600.100",
                "thread_ts": "1710711600.100",
                "user": "U1",
                "text": "thread first message",
            },
            {
                "ts": "1710711600.500",
                "thread_ts": "1710711600.100",
                "user": "U3",
                "text": "reply two",
            },
        ]
    )

    top_level_messages, thread_expansions = payload
    assert len(top_level_messages) == 1
    assert top_level_messages[0]["ts"] == "1710711600.100"
    assert top_level_messages[0]["thread_ts"] == "1710711600.100"
    assert top_level_messages[0]["reply_count"] == 2
    assert "1710711600.100" in thread_expansions
    assert [item["ts"] for item in thread_expansions["1710711600.100"]] == [
        "1710711600.100",
        "1710711600.300",
        "1710711600.500",
    ]


def test_format_channel_history_context_inserts_thread_messages_at_thread_start():
    messenger = SlackMessenger()
    channel_messages = [
        {
            "ts": "1710711602.000",
            "thread_ts": "1710711602.000",
            "user": "U3",
            "text": "latest non-thread message",
        },
        {
            "ts": "1710711600.000",
            "thread_ts": "1710711600.000",
            "user": "U1",
            "text": "thread starter",
        },
        {
            "ts": "1710711601.000",
            "thread_ts": "1710711601.000",
            "user": "U2",
            "text": "middle non-thread message",
        },
    ]
    thread_expansions = {
        "1710711600.000": [
            {
                "ts": "1710711600.000",
                "thread_ts": "1710711600.000",
                "user": "U1",
                "text": "thread starter",
            },
            {
                "ts": "1710711600.200",
                "thread_ts": "1710711600.000",
                "user": "U4",
                "text": "thread reply one",
            },
            {
                "ts": "1710711600.400",
                "thread_ts": "1710711600.000",
                "user": "U5",
                "text": "thread reply two",
            },
        ]
    }

    rendered = messenger._format_channel_history_context(
        channel_messages=channel_messages,
        thread_expansions=thread_expansions,
    )

    starter_idx = rendered.index("thread starter")
    reply_one_idx = rendered.index("thread reply one")
    reply_two_idx = rendered.index("thread reply two")
    middle_non_thread_idx = rendered.index("middle non-thread message")
    latest_non_thread_idx = rendered.index("latest non-thread message")

    assert starter_idx < reply_one_idx < reply_two_idx < middle_non_thread_idx < latest_non_thread_idx


def test_format_single_slack_context_line_includes_file_references():
    messenger = SlackMessenger()
    line = messenger._format_single_slack_context_line(
        {
            "ts": "1710711602.000",
            "user": "U3",
            "text": "Please review this",
            "files": [
                {
                    "id": "F123",
                    "name": "q1-report.pdf",
                    "url_private_download": "https://files.slack.com/files-pri/T1-F123/download/q1-report.pdf",
                    "mimetype": "application/pdf",
                }
            ],
        }
    )

    assert line is not None
    assert "q1-report.pdf" in line
    assert "<slack_file_ref id=F123" in line
    assert "url=https://files.slack.com/files-pri/T1-F123/download/q1-report.pdf" in line


def test_format_single_slack_context_line_includes_all_file_links_for_message():
    messenger = SlackMessenger()
    line = messenger._format_single_slack_context_line(
        {
            "ts": "1710711602.000",
            "user": "U3",
            "text": "Please review all attachments",
            "files": [
                {
                    "id": "F111",
                    "name": "first.txt",
                    "url_private_download": "https://files.slack.com/files-pri/T1-F111/download/first.txt",
                    "mimetype": "text/plain",
                },
                {
                    "id": "F222",
                    "name": "second.txt",
                    "url_private": "https://files.slack.com/files-pri/T1-F222/download/second.txt",
                    "mimetype": "text/plain",
                },
                {
                    "id": "F333",
                    "name": "third.txt",
                    "url_private_download": "https://files.slack.com/files-pri/T1-F333/download/third.txt",
                    "mimetype": "text/plain",
                },
                {
                    "id": "F444",
                    "name": "fourth.txt",
                    "url_private_download": "https://files.slack.com/files-pri/T1-F444/download/fourth.txt",
                    "mimetype": "text/plain",
                },
            ],
        }
    )

    assert line is not None
    assert "url=https://files.slack.com/files-pri/T1-F111/download/first.txt" in line
    assert "url=https://files.slack.com/files-pri/T1-F222/download/second.txt" in line
    assert "url=https://files.slack.com/files-pri/T1-F333/download/third.txt" in line
    assert "url=https://files.slack.com/files-pri/T1-F444/download/fourth.txt" in line
    assert "+1 more" not in line


@pytest.mark.asyncio
async def test_get_cached_channel_context_payload_from_activity_preserves_file_metadata():
    messenger = SlackMessenger()
    org_id = str(uuid4())

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.all.return_value = [
        (
            "C456:1710711600.000",
            "Hey Guys, here is Farooq's proposal",
            {
                "channel_id": "C456",
                "user_id": "U_JACK",
                "thread_ts": "1710711600.000",
                "files": [
                    {
                        "id": "F123",
                        "name": "proposal.docx",
                        "url_private_download": "https://files.slack.com/files-pri/T1-F123/download/proposal.docx",
                        "mimetype": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    }
                ],
            },
            None,
            None,
        )
    ]
    mock_session.execute.return_value = mock_result
    mock_session_ctx = MagicMock(
        __aenter__=AsyncMock(return_value=mock_session),
        __aexit__=AsyncMock(return_value=None),
    )

    with patch("messengers.slack.get_admin_session", return_value=mock_session_ctx):
        payload = await messenger._get_cached_channel_context_payload_from_activity(
            organization_id=org_id,
            channel_id="C456",
        )

    assert payload is not None
    channel_messages, _thread_expansions = payload
    assert len(channel_messages) == 1
    assert channel_messages[0]["files"][0]["id"] == "F123"
    rendered = messenger._format_single_slack_context_line(channel_messages[0])
    assert rendered is not None
    assert "<slack_file_ref id=F123" in rendered
