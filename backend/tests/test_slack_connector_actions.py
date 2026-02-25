import asyncio

from connectors.slack import SlackConnector


def test_execute_action_send_message_can_initiate_dm_with_user_id(monkeypatch) -> None:
    connector = SlackConnector(organization_id="00000000-0000-0000-0000-000000000001")
    captured: dict[str, str] = {}

    async def _fake_send_direct_message(slack_user_id: str, text: str):
        captured["slack_user_id"] = slack_user_id
        captured["text"] = text
        return {"ok": True}

    monkeypatch.setattr(connector, "send_direct_message", _fake_send_direct_message)

    result = asyncio.run(
        connector.execute_action(
            "send_message",
            {"user_id": "U123", "text": "Hi from Penny"},
        )
    )

    assert result == {"ok": True}
    assert captured == {"slack_user_id": "U123", "text": "Hi from Penny"}


def test_execute_action_send_message_accepts_legacy_message_param(monkeypatch) -> None:
    connector = SlackConnector(organization_id="00000000-0000-0000-0000-000000000001")
    captured: dict[str, str] = {}

    async def _fake_post_message(channel: str, text: str, thread_ts: str | None = None):
        captured["channel"] = channel
        captured["text"] = text
        captured["thread_ts"] = thread_ts or ""
        return {"ok": True}

    monkeypatch.setattr(connector, "post_message", _fake_post_message)

    result = asyncio.run(
        connector.execute_action(
            "send_message",
            {"channel": "C123", "message": "Legacy text", "thread_ts": "111.222"},
        )
    )

    assert result == {"ok": True}
    assert captured == {"channel": "C123", "text": "Legacy text", "thread_ts": "111.222"}
