from messengers._workspace import _resolve_conversation_scope
from messengers.base import InboundMessage, MessageType


def _build_message(message_type: MessageType, *, channel_type: str | None, external_user_id: str = "") -> InboundMessage:
    return InboundMessage(
        external_user_id=external_user_id,
        text="hello",
        message_type=message_type,
        messenger_context={"channel_type": channel_type},
        message_id="mid-1",
    )


def test_resolve_conversation_scope_private_for_known_im_direct_message() -> None:
    message = _build_message(MessageType.DIRECT, channel_type="im", external_user_id="U123")
    assert _resolve_conversation_scope(message, revtops_user_id=None) == "private"


def test_resolve_conversation_scope_shared_for_mpim_direct_message() -> None:
    message = _build_message(MessageType.DIRECT, channel_type="mpim", external_user_id="U123")
    assert _resolve_conversation_scope(message, revtops_user_id="11111111-1111-1111-1111-111111111111") == "shared"


def test_resolve_conversation_scope_shared_for_mentions() -> None:
    message = _build_message(MessageType.MENTION, channel_type="channel", external_user_id="U123")
    assert _resolve_conversation_scope(message, revtops_user_id="11111111-1111-1111-1111-111111111111") == "shared"


def test_resolve_conversation_scope_shared_for_teams_groupchat_direct_message() -> None:
    message = _build_message(MessageType.DIRECT, channel_type="groupChat", external_user_id="U123")
    assert _resolve_conversation_scope(message, revtops_user_id="11111111-1111-1111-1111-111111111111") == "shared"
