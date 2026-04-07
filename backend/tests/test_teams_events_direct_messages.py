from api.routes.teams_events import _build_inbound_message
from messengers.base import MessageType


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
