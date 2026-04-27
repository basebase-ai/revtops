import asyncio
from contextlib import asynccontextmanager
from uuid import uuid4

from api.routes import slack_events
from messengers.base import InboundMessage, MessageType
from messengers.slack import SlackMessenger


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

    async def _fake_process_inbound(self, message: InboundMessage):
        nonlocal overlap_counter, max_overlap
        overlap_counter += 1
        max_overlap = max(max_overlap, overlap_counter)
        await asyncio.sleep(0.03)
        overlap_counter -= 1
        return {"status": "success"}

    monkeypatch.setattr(slack_events, "is_duplicate_event", _fake_is_duplicate_event)
    monkeypatch.setattr(slack_events, "is_duplicate_message", _fake_is_duplicate_message)
    monkeypatch.setattr(SlackMessenger, "process_inbound", _fake_process_inbound)

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


def test_process_event_callback_routes_thread_message_bot_mention_to_mention_handler(monkeypatch) -> None:
    captured: list[InboundMessage] = []

    async def _fake_is_duplicate_event(_event_id: str) -> bool:
        return False

    async def _fake_is_duplicate_message(_channel_id: str, _message_ts: str) -> bool:
        return False

    async def _fake_process_inbound(self, message: InboundMessage):
        captured.append(message)
        return {"status": "success"}

    monkeypatch.setattr(slack_events, "is_duplicate_event", _fake_is_duplicate_event)
    monkeypatch.setattr(slack_events, "is_duplicate_message", _fake_is_duplicate_message)
    monkeypatch.setattr(SlackMessenger, "process_inbound", _fake_process_inbound)

    payload = {
        "type": "event_callback",
        "event_id": "EvThreadMention1",
        "team_id": "T123",
        "authed_users": ["UBOT"],
        "event": {
            "type": "message",
            "channel_type": "channel",
            "channel": "C123",
            "user": "U123",
            "thread_ts": "1700000000.001",
            "ts": "1700000000.002",
            "text": "<@UBOT> can you help in this thread?",
        },
    }

    asyncio.run(slack_events._process_event_callback_impl(payload))

    assert len(captured) == 1
    msg: InboundMessage = captured[0]
    assert msg.message_type == MessageType.MENTION
    assert msg.text == "can you help in this thread?"
    assert msg.messenger_context["channel_id"] == "C123"
    assert msg.messenger_context["thread_ts"] == "1700000000.001"


def test_strip_bot_mentions_removes_only_known_bot_mentions() -> None:
    text = "<@UBOT> hi <@UOTHER> and <@UBOT>"
    cleaned = slack_events._strip_bot_mentions(text, {"UBOT"})
    assert cleaned == "hi <@UOTHER> and"


def test_app_mention_keeps_non_bot_user_mentions_in_text(monkeypatch) -> None:
    captured: list[InboundMessage] = []

    async def _fake_is_duplicate_event(_event_id: str) -> bool:
        return False

    async def _fake_is_duplicate_message(_channel_id: str, _message_ts: str) -> bool:
        return False

    async def _fake_process_inbound(self, message: InboundMessage):
        captured.append(message)
        return {"status": "success"}

    monkeypatch.setattr(slack_events, "is_duplicate_event", _fake_is_duplicate_event)
    monkeypatch.setattr(slack_events, "is_duplicate_message", _fake_is_duplicate_message)
    monkeypatch.setattr(SlackMessenger, "process_inbound", _fake_process_inbound)

    payload = {
        "type": "event_callback",
        "event_id": "EvAppMention1",
        "team_id": "T123",
        "authed_users": ["UBOT"],
        "event": {
            "type": "app_mention",
            "channel_type": "channel",
            "channel": "C123",
            "user": "U123",
            "event_ts": "1700000000.001",
            "ts": "1700000000.001",
            "text": "<@UBOT> who is <@UJON123>?",
        },
    }

    asyncio.run(slack_events._process_event_callback_impl(payload))

    assert len(captured) == 1
    msg: InboundMessage = captured[0]
    assert msg.message_type == MessageType.MENTION
    assert msg.text == "who is <@UJON123>?"


def test_dm_bot_message_from_current_app_is_logged_not_processed(monkeypatch) -> None:
    calls: list[dict[str, str]] = []

    async def _fake_is_duplicate_event(_event_id: str) -> bool:
        return False

    async def _fake_log_external(*_args, **kwargs) -> bool:
        assert kwargs["sender_category"] == "self_bot"
        calls.append({"called": "yes"})
        return True

    monkeypatch.setattr(slack_events, "is_duplicate_event", _fake_is_duplicate_event)
    monkeypatch.setattr(
        slack_events,
        "_log_bot_dm_message_without_processing",
        _fake_log_external,
    )

    payload = {
        "type": "event_callback",
        "event_id": "EvBotSelf1",
        "team_id": "T123",
        "api_app_id": "A123",
        "authed_users": ["UBOT"],
        "event": {
            "type": "message",
            "channel_type": "im",
            "channel": "D123",
            "subtype": "bot_message",
            "bot_profile": {"app_id": "A123"},
            "text": "self bot message",
            "ts": "1700000000.010",
        },
    }

    asyncio.run(slack_events._process_event_callback_impl(payload))
    assert len(calls) == 1


def test_dm_bot_message_from_external_source_is_logged_not_processed(monkeypatch) -> None:
    calls: list[dict[str, str]] = []
    processed: list[InboundMessage] = []

    async def _fake_is_duplicate_event(_event_id: str) -> bool:
        return False

    async def _fake_log_external(*_args, **kwargs) -> bool:
        assert kwargs["sender_category"] == "other_bot"
        calls.append({"called": "yes"})
        return True

    async def _fake_process_inbound(self, message: InboundMessage):
        processed.append(message)
        return {"status": "success"}

    monkeypatch.setattr(slack_events, "is_duplicate_event", _fake_is_duplicate_event)
    monkeypatch.setattr(
        slack_events,
        "_log_bot_dm_message_without_processing",
        _fake_log_external,
    )
    monkeypatch.setattr(SlackMessenger, "process_inbound", _fake_process_inbound)

    payload = {
        "type": "event_callback",
        "event_id": "EvBotExternal1",
        "team_id": "T123",
        "api_app_id": "A123",
        "authed_users": ["UBOT"],
        "event": {
            "type": "message",
            "channel_type": "im",
            "channel": "D123",
            "subtype": "bot_message",
            "bot_profile": {"app_id": "A999"},
            "bot_id": "B999",
            "text": "external bot message",
            "ts": "1700000000.020",
        },
    }

    asyncio.run(slack_events._process_event_callback_impl(payload))
    assert len(calls) == 1
    assert processed == []


def test_classify_message_sender_categories() -> None:
    payload = {"api_app_id": "A123"}
    bot_user_ids = {"UBOT"}

    user_event = {"type": "message", "channel": "C123", "user": "U111", "text": "hello"}
    self_bot_event = {
        "type": "message",
        "subtype": "bot_message",
        "channel": "D123",
        "bot_profile": {"app_id": "A123"},
    }
    other_bot_event = {
        "type": "message",
        "subtype": "bot_message",
        "channel": "D123",
        "bot_profile": {"app_id": "A999"},
    }

    assert slack_events._classify_message_sender(payload, user_event, bot_user_ids) == "user"
    assert slack_events._classify_message_sender(payload, self_bot_event, bot_user_ids) == "self_bot"
    assert slack_events._classify_message_sender(payload, other_bot_event, bot_user_ids) == "other_bot"


def test_candidate_dm_source_channel_ids_prefers_thread_then_channel() -> None:
    assert slack_events._candidate_dm_source_channel_ids(
        channel_id="D123",
        thread_ts="1700000000.111",
    ) == ["D123:1700000000.111", "D123"]


def test_candidate_dm_source_channel_ids_handles_channel_without_thread() -> None:
    assert slack_events._candidate_dm_source_channel_ids(
        channel_id="D123",
        thread_ts=None,
    ) == ["D123"]


def test_log_bot_dm_message_uses_admin_session_for_private_conversations(monkeypatch) -> None:
    conversation_id = uuid4()
    used_admin_session = {"value": False}

    class _FakeResult:
        def scalar_one_or_none(self):
            return conversation_id

    class _FakeSession:
        async def execute(self, *_args, **_kwargs):
            return _FakeResult()

        def add(self, _row) -> None:
            return None

        async def commit(self) -> None:
            return None

    @asynccontextmanager
    async def _fake_get_admin_session():
        used_admin_session["value"] = True
        yield _FakeSession()

    async def _fake_resolve_org(_team_id: str) -> str:
        return str(uuid4())

    class _FakeMessenger:
        _resolve_org_from_workspace = staticmethod(_fake_resolve_org)

    monkeypatch.setattr(slack_events, "get_admin_session", _fake_get_admin_session)

    event = {
        "channel_type": "im",
        "channel": "D0AA3KFETUY",
        "text": "bot reply in private convo",
        "bot_id": "B123",
    }
    persisted = asyncio.run(
        slack_events._log_bot_dm_message_without_processing(
            _FakeMessenger(),
            event,
            "T123",
            sender_category="self_bot",
        )
    )

    assert persisted is True
    assert used_admin_session["value"] is True
