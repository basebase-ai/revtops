import asyncio

from api.routes import slack_events
from connectors.slack import SlackConnector
from messengers.slack import SlackMessenger


def test_stop_message_in_dm_calls_stop_listening(monkeypatch) -> None:
    called: dict[str, int] = {"stop": 0, "process": 0}

    async def _fake_is_duplicate_event(_event_id: str) -> bool:
        return False

    async def _fake_stop_listening(self, _message) -> bool:
        called["stop"] += 1
        return True

    async def _fake_process_inbound(self, _message):
        called["process"] += 1
        return {"status": "success"}

    monkeypatch.setattr(slack_events, "is_duplicate_event", _fake_is_duplicate_event)
    monkeypatch.setattr(SlackMessenger, "stop_listening", _fake_stop_listening)
    monkeypatch.setattr(SlackMessenger, "process_inbound", _fake_process_inbound)

    payload = {
        "type": "event_callback",
        "event_id": "EvStopDm1",
        "team_id": "T123",
        "event": {
            "type": "message",
            "channel_type": "im",
            "channel": "D123",
            "user": "U123",
            "text": "stop",
            "ts": "1700000000.100",
        },
    }

    asyncio.run(slack_events._process_event_callback_impl(payload))

    assert called["stop"] == 1
    assert called["process"] == 0


def test_x_reaction_calls_stop_listening(monkeypatch) -> None:
    captured: dict[str, str | None] = {"thread_id": None}

    async def _fake_is_duplicate_event(_event_id: str) -> bool:
        return False

    async def _fake_resolve_org_from_workspace(self, _workspace_id: str) -> str | None:
        return "00000000-0000-0000-0000-000000000000"

    async def _fake_get_message_by_ts(self, _channel_id: str, _ts: str):
        return {"ts": "1700000000.200", "thread_ts": "1700000000.001"}

    async def _fake_stop_listening(self, message) -> bool:
        ctx = message.messenger_context
        captured["thread_id"] = ctx.get("thread_id") or ctx.get("thread_ts")
        return True

    monkeypatch.setattr(slack_events, "is_duplicate_event", _fake_is_duplicate_event)
    monkeypatch.setattr(SlackMessenger, "_resolve_org_from_workspace", _fake_resolve_org_from_workspace)
    monkeypatch.setattr(SlackConnector, "get_message_by_ts", _fake_get_message_by_ts)
    monkeypatch.setattr(SlackMessenger, "stop_listening", _fake_stop_listening)

    payload = {
        "type": "event_callback",
        "event_id": "EvReactStop1",
        "team_id": "T123",
        "event": {
            "type": "reaction_added",
            "user": "U999",
            "reaction": "x",
            "item": {"type": "message", "channel": "C123", "ts": "1700000000.200"},
        },
    }

    asyncio.run(slack_events._process_event_callback_impl(payload))

    assert captured["thread_id"] == "1700000000.001"

