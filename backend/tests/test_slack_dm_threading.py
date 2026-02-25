import asyncio
from types import SimpleNamespace
from uuid import UUID

from services import slack_conversations


def test_process_slack_dm_posts_reply_in_same_thread(monkeypatch) -> None:
    captured: dict[str, str | None] = {}

    class _FakeConnector:
        def __init__(self, organization_id: str, team_id: str | None = None) -> None:
            self.organization_id = organization_id
            captured["team_id"] = team_id

        async def add_reaction(self, channel: str, timestamp: str) -> None:
            captured["add_reaction_timestamp"] = timestamp

        async def remove_reaction(self, channel: str, timestamp: str) -> None:
            captured["remove_reaction_timestamp"] = timestamp

        async def post_message(self, channel: str, text: str, thread_ts: str | None = None) -> None:
            captured["slow_reply_thread_ts"] = thread_ts

    class _FakeOrchestrator:
        def __init__(self, **kwargs) -> None:
            captured["conversation_id"] = kwargs["conversation_id"]

    async def _fake_find_org(_team_id: str) -> str:
        return "org-1"

    async def _fake_resolve_user(**_kwargs):
        return SimpleNamespace(id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"), email="user@example.com")

    async def _fake_find_or_create_conversation(**kwargs):
        captured["slack_channel_id"] = kwargs["slack_channel_id"]
        return SimpleNamespace(id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"))

    async def _fake_stream_and_post_responses(**kwargs) -> int:
        captured["thread_ts"] = kwargs["thread_ts"]
        return 42

    monkeypatch.setattr(slack_conversations, "find_organization_by_slack_team", _fake_find_org)
    monkeypatch.setattr(slack_conversations, "SlackConnector", _FakeConnector)
    
    async def _fake_fetch_user_info(**_kwargs):
        return {}

    async def _fake_can_use_credits(_organization_id: str) -> bool:
        return True

    monkeypatch.setattr(slack_conversations, "_fetch_slack_user_info", _fake_fetch_user_info)
    monkeypatch.setattr(slack_conversations, "resolve_revtops_user_for_slack_actor", _fake_resolve_user)
    monkeypatch.setattr(slack_conversations, "find_or_create_conversation", _fake_find_or_create_conversation)
    monkeypatch.setattr(slack_conversations, "can_use_credits", _fake_can_use_credits)
    monkeypatch.setattr(slack_conversations, "ChatOrchestrator", _FakeOrchestrator)
    monkeypatch.setattr(slack_conversations, "_stream_and_post_responses", _fake_stream_and_post_responses)

    result = asyncio.run(
        slack_conversations.process_slack_dm(
            team_id="T1",
            channel_id="D1",
            user_id="U1",
            bot_id=None,
            message_text="hello",
            event_ts="100.1",
            thread_ts="100.0",
        )
    )

    assert result["status"] == "success"
    assert captured["thread_ts"] == "100.0"
    assert captured["slack_channel_id"] == "D1:100.0"
    assert captured["team_id"] == "T1"


def test_streaming_flushes_on_buffer_threshold(monkeypatch) -> None:
    posted: list[str] = []

    class _FakeConnector:
        async def post_message(self, channel: str, text: str, thread_ts: str | None = None) -> None:
            posted.append(text)

    class _FakeOrchestrator:
        async def process_message(self, _message_text: str, attachment_ids=None):
            yield "x" * slack_conversations.SLACK_STREAM_FLUSH_CHAR_THRESHOLD
            yield "tail"

    total = asyncio.run(
        slack_conversations._stream_and_post_responses(
            orchestrator=_FakeOrchestrator(),
            connector=_FakeConnector(),
            message_text="hello",
            channel="C123",
            thread_ts="T123",
        )
    )

    assert len(posted) == 2
    assert posted[0] == "x" * slack_conversations.SLACK_STREAM_FLUSH_CHAR_THRESHOLD
    assert posted[1] == "tail"
    assert total == len(posted[0]) + len(posted[1])


def test_process_slack_dm_allows_initial_response_when_credits_check_is_slow(monkeypatch) -> None:
    events: list[str] = []

    class _FakeConnector:
        def __init__(self, organization_id: str, team_id: str | None = None) -> None:
            self.organization_id = organization_id

        async def add_reaction(self, channel: str, timestamp: str) -> None:
            events.append("add_reaction")

        async def remove_reaction(self, channel: str, timestamp: str) -> None:
            events.append("remove_reaction")

        async def post_message(self, channel: str, text: str, thread_ts: str | None = None) -> None:
            events.append(f"post:{text}")

    class _FakeOrchestrator:
        def __init__(self, **kwargs) -> None:
            self.user_id = kwargs.get("user_id")

    async def _fake_find_org(_team_id: str) -> str:
        return "org-slow-credits"

    async def _fake_fetch_user_info(**_kwargs):
        return {}

    async def _fake_resolve_user(**_kwargs):
        return SimpleNamespace(id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"), email="user@example.com")

    async def _fake_find_or_create_conversation(**_kwargs):
        return SimpleNamespace(id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"))

    async def _fake_stream_and_post_responses(**_kwargs) -> int:
        events.append("stream")
        await asyncio.sleep(0.03)
        return 8

    async def _fake_can_use_credits(_organization_id: str) -> bool:
        await asyncio.sleep(0.02)
        return False

    slack_conversations._slack_credits_gate_cache.clear()
    monkeypatch.setattr(slack_conversations, "SLACK_CREDITS_CHECK_SOFT_TIMEOUT_SECONDS", 0.005)
    monkeypatch.setattr(slack_conversations, "find_organization_by_slack_team", _fake_find_org)
    monkeypatch.setattr(slack_conversations, "SlackConnector", _FakeConnector)
    monkeypatch.setattr(slack_conversations, "_fetch_slack_user_info", _fake_fetch_user_info)
    monkeypatch.setattr(slack_conversations, "resolve_revtops_user_for_slack_actor", _fake_resolve_user)
    monkeypatch.setattr(slack_conversations, "find_or_create_conversation", _fake_find_or_create_conversation)
    monkeypatch.setattr(slack_conversations, "can_use_credits", _fake_can_use_credits)
    monkeypatch.setattr(slack_conversations, "ChatOrchestrator", _FakeOrchestrator)
    monkeypatch.setattr(slack_conversations, "_stream_and_post_responses", _fake_stream_and_post_responses)

    result = asyncio.run(
        slack_conversations.process_slack_dm(
            team_id="T1",
            channel_id="D1",
            user_id="U1",
            bot_id=None,
            message_text="hello",
            event_ts="100.1",
            thread_ts="100.0",
        )
    )

    assert result["status"] == "success"
    assert "stream" in events
    assert not any("out of credits" in e for e in events if e.startswith("post:"))


def test_process_slack_dm_passes_bot_actor_id_to_orchestrator(monkeypatch) -> None:
    captured: dict[str, str | None] = {}

    class _FakeConnector:
        def __init__(self, organization_id: str, team_id: str | None = None) -> None:
            self.organization_id = organization_id

        async def add_reaction(self, channel: str, timestamp: str) -> None:
            return None

        async def remove_reaction(self, channel: str, timestamp: str) -> None:
            return None

        async def post_message(self, channel: str, text: str, thread_ts: str | None = None) -> None:
            return None

    class _FakeOrchestrator:
        def __init__(self, **kwargs) -> None:
            captured["source_user_id"] = kwargs.get("source_user_id")
            captured["source_user_email"] = kwargs.get("source_user_email")

    async def _fake_find_org(_team_id: str) -> str:
        return "org-1"

    async def _fake_find_or_create_conversation(**_kwargs):
        return SimpleNamespace(id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"))

    async def _fake_stream_and_post_responses(**_kwargs) -> int:
        return 11

    async def _fake_can_use_credits(_organization_id: str) -> bool:
        return True

    monkeypatch.setattr(slack_conversations, "find_organization_by_slack_team", _fake_find_org)
    monkeypatch.setattr(slack_conversations, "SlackConnector", _FakeConnector)
    monkeypatch.setattr(slack_conversations, "find_or_create_conversation", _fake_find_or_create_conversation)
    monkeypatch.setattr(slack_conversations, "can_use_credits", _fake_can_use_credits)
    monkeypatch.setattr(slack_conversations, "ChatOrchestrator", _FakeOrchestrator)
    monkeypatch.setattr(slack_conversations, "_stream_and_post_responses", _fake_stream_and_post_responses)

    result = asyncio.run(
        slack_conversations.process_slack_dm(
            team_id="T1",
            channel_id="D1",
            user_id="B1",
            bot_id="B1",
            message_text="bot hello",
            event_ts="100.1",
            thread_ts=None,
        )
    )

    assert result["status"] == "success"
    assert captured["source_user_id"] == "B1"
    assert captured["source_user_email"] is None
