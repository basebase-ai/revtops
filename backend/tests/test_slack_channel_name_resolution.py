
import asyncio
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from uuid import uuid4, UUID
from datetime import datetime

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
