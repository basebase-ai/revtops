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
            message_text="hello",
            event_ts="100.1",
            thread_ts="100.0",
        )
    )

    assert result["status"] == "success"
    assert captured["thread_ts"] == "100.0"
    assert captured["slack_channel_id"] == "D1:100.0"
    assert captured["team_id"] == "T1"
