import asyncio

from api.routes import teams_events
from api.routes.teams_events import _build_inbound_message
from messengers.base import MessageType
from messengers.teams import TeamsMessenger


def test_build_inbound_message_sets_channel_type_for_personal_chat() -> None:
    activity = {
        "id": "activity-1",
        "text": "hello bot",
        "from": {"id": "29:user", "aadObjectId": "aad-1"},
        "recipient": {"id": "28:bot"},
        "serviceUrl": "https://smba.trafficmanager.net/amer/",
        "conversation": {
            "id": "19:conversation",
            "conversationType": "personal",
            "isGroup": False,
        },
        "channelData": {"tenant": {"id": "tenant-1"}},
    }

    message = _build_inbound_message(activity, MessageType.DIRECT)

    assert message.message_type == MessageType.DIRECT
    assert message.messenger_context["channel_type"] == "personal"
    assert message.messenger_context["workspace_id"] == "tenant-1"


def test_process_message_activity_records_failure_when_processing_raises(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def _fake_process_inbound(self, _message):
        raise RuntimeError("teams forced failure")

    async def _fake_record_query_outcome(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(TeamsMessenger, "process_inbound", _fake_process_inbound)
    monkeypatch.setattr(
        "services.query_outcome_metrics.record_query_outcome",
        _fake_record_query_outcome,
    )

    activity = {
        "id": "activity-2",
        "type": "message",
        "text": "hello bot",
        "from": {"id": "29:user", "aadObjectId": "aad-1"},
        "recipient": {"id": "28:bot"},
        "conversation": {
            "id": "19:conversation",
            "conversationType": "personal",
            "isGroup": False,
        },
        "channelData": {"tenant": {"id": "tenant-1"}},
    }

    asyncio.run(teams_events._process_message_activity(activity))

    assert captured["platform"] == "teams"
    assert captured["was_success"] is False
    assert captured["conversation_id"] == "19:conversation:activity-2"
    assert captured["failure_reason"] == "teams forced failure"


def test_process_message_activity_persists_only_public_channel_messages(monkeypatch) -> None:
    persisted: list[str] = []

    async def _fake_persist_activity(message, _tenant_id: str) -> None:
        persisted.append(message.messenger_context.get("channel_type") or "")

    async def _fake_process_inbound(self, _message):
        return {"status": "success"}

    async def _run(activity: dict[str, object]) -> None:
        await teams_events._process_message_activity(activity)
        await asyncio.sleep(0)

    monkeypatch.setattr(teams_events, "_persist_activity", _fake_persist_activity)
    monkeypatch.setattr(TeamsMessenger, "process_inbound", _fake_process_inbound)

    standard_channel_activity = {
        "id": "activity-public-1",
        "type": "message",
        "text": "hello public channel",
        "from": {"id": "29:user", "aadObjectId": "aad-1"},
        "recipient": {"id": "28:bot"},
        "conversation": {
            "id": "19:conversation",
            "conversationType": "channel",
            "isGroup": True,
        },
        "channelData": {
            "tenant": {"id": "tenant-1"},
            "channel": {"membershipType": "standard"},
        },
    }
    private_channel_activity = {
        "id": "activity-private-1",
        "type": "message",
        "text": "hello private channel",
        "from": {"id": "29:user", "aadObjectId": "aad-1"},
        "recipient": {"id": "28:bot"},
        "conversation": {
            "id": "19:conversation-private",
            "conversationType": "channel",
            "isGroup": True,
        },
        "channelData": {
            "tenant": {"id": "tenant-1"},
            "channel": {"membershipType": "private"},
        },
    }
    personal_chat_activity = {
        "id": "activity-personal-1",
        "type": "message",
        "text": "hello direct chat",
        "from": {"id": "29:user", "aadObjectId": "aad-1"},
        "recipient": {"id": "28:bot"},
        "conversation": {
            "id": "19:conversation-personal",
            "conversationType": "personal",
            "isGroup": False,
        },
        "channelData": {"tenant": {"id": "tenant-1"}},
    }

    asyncio.run(_run(standard_channel_activity))
    asyncio.run(_run(private_channel_activity))
    asyncio.run(_run(personal_chat_activity))

    assert persisted == ["channel"]
