import asyncio

from api.routes import slack_events


def test_thread_lock_manager_serializes_same_thread_work() -> None:
    manager = slack_events.SlackThreadLockManager()
    lock_key = manager.build_lock_key("T1", "C1", "123.456")
    active = 0
    max_active = 0

    async def _worker() -> None:
        nonlocal active, max_active
        async with manager.thread_lock(lock_key):
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.02)
            active -= 1

    async def _run() -> None:
        await asyncio.gather(_worker(), _worker(), _worker())

    asyncio.run(_run())

    assert max_active == 1
    assert manager._locks == {}
    assert manager._lock_refs == {}


def test_process_event_callback_serializes_thread_reply_events(monkeypatch) -> None:
    overlap_counter = 0
    max_overlap = 0

    async def _fake_is_duplicate_event(_event_id: str) -> bool:
        return False

    async def _fake_is_duplicate_message(_channel_id: str, _message_ts: str) -> bool:
        return False

    async def _fake_process_thread_reply(**_kwargs):
        nonlocal overlap_counter, max_overlap
        overlap_counter += 1
        max_overlap = max(max_overlap, overlap_counter)
        await asyncio.sleep(0.03)
        overlap_counter -= 1

    monkeypatch.setattr(slack_events, "is_duplicate_event", _fake_is_duplicate_event)
    monkeypatch.setattr(slack_events, "is_duplicate_message", _fake_is_duplicate_message)
    monkeypatch.setattr(slack_events, "process_slack_thread_reply", _fake_process_thread_reply)

    payload_template = {
        "type": "event_callback",
        "event": {
            "type": "message",
            "channel_type": "channel",
            "thread_ts": "111.222",
            "channel": "C123",
            "user": "U123",
            "text": "hello",
            "ts": "111.333",
        },
        "team_id": "T123",
    }

    async def _run() -> None:
        first = dict(payload_template)
        first["event_id"] = "Ev1"
        second = dict(payload_template)
        second["event_id"] = "Ev2"
        await asyncio.gather(
            slack_events._process_event_callback_impl(first),
            slack_events._process_event_callback_impl(second),
        )

    asyncio.run(_run())

    assert max_overlap == 1
