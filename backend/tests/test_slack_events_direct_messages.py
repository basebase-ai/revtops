import asyncio

from api.routes import slack_events
from messengers.base import InboundMessage, MessageType
from messengers.slack import SlackMessenger


def test_process_event_callback_routes_mpim_messages_to_direct_message_handler(monkeypatch) -> None:
    captured: list[InboundMessage] = []

    async def _fake_is_duplicate_event(_event_id: str) -> bool:
        return False

    async def _fake_process_inbound(self, message: InboundMessage):
        captured.append(message)
        return {"status": "success"}

    monkeypatch.setattr(slack_events, "is_duplicate_event", _fake_is_duplicate_event)
    monkeypatch.setattr(SlackMessenger, "process_inbound", _fake_process_inbound)

    payload = {
        "type": "event_callback",
        "event_id": "EvMPIM1",
        "team_id": "T123",
        "event": {
            "type": "message",
            "channel_type": "mpim",
            "channel": "G123",
            "user": "U123",
            "text": "hey basebase",
            "ts": "1700000000.001",
        },
    }

    asyncio.run(slack_events._process_event_callback_impl(payload))

    assert len(captured) == 1
    msg: InboundMessage = captured[0]
    assert msg.message_type == MessageType.DIRECT
    assert msg.external_user_id == "U123"
    assert msg.text == "hey basebase"
    assert msg.messenger_context["workspace_id"] == "T123"
    assert msg.messenger_context["channel_id"] == "G123"
    assert msg.messenger_context["thread_id"] is None


def test_process_event_callback_passes_thread_ts_for_direct_message_thread(monkeypatch) -> None:
    captured: list[InboundMessage] = []

    async def _fake_is_duplicate_event(_event_id: str) -> bool:
        return False

    async def _fake_process_inbound(self, message: InboundMessage):
        captured.append(message)
        return {"status": "success"}

    monkeypatch.setattr(slack_events, "is_duplicate_event", _fake_is_duplicate_event)
    monkeypatch.setattr(SlackMessenger, "process_inbound", _fake_process_inbound)

    payload = {
        "type": "event_callback",
        "event_id": "EvIMThread1",
        "team_id": "T123",
        "event": {
            "type": "message",
            "channel_type": "im",
            "channel": "D123",
            "user": "U123",
            "text": "follow up",
            "thread_ts": "1700000000.001",
            "ts": "1700000000.002",
        },
    }

    asyncio.run(slack_events._process_event_callback_impl(payload))

    assert len(captured) == 1
    msg: InboundMessage = captured[0]
    assert msg.messenger_context["thread_ts"] == "1700000000.001"
    assert msg.message_id == "1700000000.002"
