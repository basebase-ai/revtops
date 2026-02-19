import asyncio
from types import SimpleNamespace

from services import slack_conversations


class _FakeSlackConnector:
    def __init__(self, organization_id: str):
        self.organization_id = organization_id

    async def add_reaction(self, channel: str, timestamp: str):
        return None

    async def remove_reaction(self, channel: str, timestamp: str):
        return None


class _CapturedOrchestrator:
    calls: list[dict] = []

    def __init__(self, **kwargs):
        _CapturedOrchestrator.calls.append(kwargs)


async def _fake_org_lookup(_team_id: str) -> str:
    return "11111111-1111-1111-1111-111111111111"


async def _fake_thread_lookup(**_kwargs):
    return SimpleNamespace(
        id="conv-1",
        source_user_id="U_OLD",
        user_id="legacy-user",
    )


async def _fake_user_info(**_kwargs):
    return {}


async def _fake_user_resolution(**_kwargs):
    return None


def test_thread_reply_switches_active_speaker(monkeypatch):
    updated_conversation = SimpleNamespace(
        id="conv-1",
        source_user_id="U_NEW",
        user_id=None,
    )
    upsert_calls: list[dict] = []

    async def _fake_find_or_create_conversation(**kwargs):
        upsert_calls.append(kwargs)
        return updated_conversation

    async def _fake_stream_and_post_responses(**kwargs):
        return 12

    monkeypatch.setattr(slack_conversations, "find_organization_by_slack_team", _fake_org_lookup)
    monkeypatch.setattr(slack_conversations, "find_thread_conversation", _fake_thread_lookup)
    monkeypatch.setattr(slack_conversations, "SlackConnector", _FakeSlackConnector)
    monkeypatch.setattr(slack_conversations, "_fetch_slack_user_info", _fake_user_info)
    monkeypatch.setattr(slack_conversations, "resolve_revtops_user_for_slack_actor", _fake_user_resolution)
    monkeypatch.setattr(slack_conversations, "find_or_create_conversation", _fake_find_or_create_conversation)
    monkeypatch.setattr(slack_conversations, "_stream_and_post_responses", _fake_stream_and_post_responses)
    monkeypatch.setattr(slack_conversations, "ChatOrchestrator", _CapturedOrchestrator)

    _CapturedOrchestrator.calls.clear()

    result = asyncio.run(
        slack_conversations.process_slack_thread_reply(
            team_id="T1",
            channel_id="C1",
            user_id="U_NEW",
            message_text="hello",
            thread_ts="123.456",
            event_ts="123.789",
        )
    )

    assert result["status"] == "success"
    assert upsert_calls[0]["slack_user_id"] == "U_NEW"
    assert upsert_calls[0]["revtops_user_id"] is None
    assert _CapturedOrchestrator.calls[0]["source_user_id"] == "U_NEW"
    assert _CapturedOrchestrator.calls[0]["user_id"] is None
