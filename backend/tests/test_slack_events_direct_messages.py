import asyncio

from api.routes import slack_events


def test_process_event_callback_routes_mpim_messages_to_direct_message_handler(monkeypatch) -> None:
    captured: dict[str, str | None] = {}

    async def _fake_is_duplicate_event(_event_id: str) -> bool:
        return False

    async def _fake_process_slack_dm(**kwargs):
        captured["team_id"] = kwargs["team_id"]
        captured["channel_id"] = kwargs["channel_id"]
        captured["user_id"] = kwargs["user_id"]
        captured["message_text"] = kwargs["message_text"]
        captured["event_ts"] = kwargs["event_ts"]
        captured["thread_ts"] = kwargs["thread_ts"]

    monkeypatch.setattr(slack_events, "is_duplicate_event", _fake_is_duplicate_event)
    monkeypatch.setattr(slack_events, "process_slack_dm", _fake_process_slack_dm)

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

    assert captured == {
        "team_id": "T123",
        "channel_id": "G123",
        "user_id": "U123",
        "message_text": "hey basebase",
        "event_ts": "1700000000.001",
        "thread_ts": None,
    }


def test_process_event_callback_passes_thread_ts_for_direct_message_thread(monkeypatch) -> None:
    captured: dict[str, str | None] = {}

    async def _fake_is_duplicate_event(_event_id: str) -> bool:
        return False

    async def _fake_process_slack_dm(**kwargs):
        captured["thread_ts"] = kwargs["thread_ts"]
        captured["event_ts"] = kwargs["event_ts"]

    monkeypatch.setattr(slack_events, "is_duplicate_event", _fake_is_duplicate_event)
    monkeypatch.setattr(slack_events, "process_slack_dm", _fake_process_slack_dm)

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

    assert captured == {
        "thread_ts": "1700000000.001",
        "event_ts": "1700000000.002",
    }
