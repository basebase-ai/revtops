import asyncio
from types import SimpleNamespace

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


def test_get_oauth_token_uses_inferred_team_bot_install(monkeypatch) -> None:
    connector = SlackConnector(organization_id="00000000-0000-0000-0000-000000000001")

    async def _fake_load_integration() -> None:
        connector._integration = SimpleNamespace(extra_data={"team_id": "T999"})

    async def _fake_get_slack_bot_token(organization_id: str, team_id: str) -> str | None:
        assert organization_id == "00000000-0000-0000-0000-000000000001"
        assert team_id == "T999"
        return "xoxb-bot-token"

    async def _fake_base_get_oauth_token() -> tuple[str, str]:
        raise AssertionError("base token fallback should not be used when bot token exists")

    monkeypatch.setattr(connector, "_load_integration", _fake_load_integration)
    monkeypatch.setattr("services.slack_bot_install.get_slack_bot_token", _fake_get_slack_bot_token)
    monkeypatch.setattr("connectors.base.BaseConnector.get_oauth_token", _fake_base_get_oauth_token)

    token, _ = asyncio.run(connector.get_oauth_token())

    assert token == "xoxb-bot-token"
    assert connector.team_id == "T999"


def test_send_direct_message_falls_back_to_user_channel_on_missing_scope(monkeypatch) -> None:
    connector = SlackConnector(organization_id="00000000-0000-0000-0000-000000000001")

    async def _fake_make_request(method: str, endpoint: str, **_: object):
        assert method == "POST"
        assert endpoint == "conversations.open"
        raise ValueError("Slack API error: missing_scope")

    captured: dict[str, str] = {}

    async def _fake_post_message(channel: str, text: str, thread_ts: str | None = None):
        captured["channel"] = channel
        captured["text"] = text
        captured["thread_ts"] = thread_ts or ""
        return {"ok": True, "channel": channel}

    monkeypatch.setattr(connector, "_make_request", _fake_make_request)
    monkeypatch.setattr(connector, "post_message", _fake_post_message)

    result = asyncio.run(connector.send_direct_message("U123", "Fallback DM"))

    assert result == {"ok": True, "channel": "U123"}
    assert captured == {"channel": "U123", "text": "Fallback DM", "thread_ts": ""}


def test_post_message_resolves_hash_channel_name(monkeypatch) -> None:
    connector = SlackConnector(organization_id="00000000-0000-0000-0000-000000000001")

    async def _fake_get_channels() -> list[dict[str, str]]:
        return [{"id": "C999", "name": "random", "name_normalized": "random"}]

    captured: dict[str, object] = {}

    async def _fake_make_request(method: str, endpoint: str, **kwargs: object):
        captured["method"] = method
        captured["endpoint"] = endpoint
        captured["json_data"] = kwargs.get("json_data")
        return {"ok": True, "channel": "C999", "ts": "1.2", "message": {"text": "hello"}}

    monkeypatch.setattr(connector, "get_channels", _fake_get_channels)
    monkeypatch.setattr(connector, "_make_request", _fake_make_request)

    result = asyncio.run(connector.post_message("#random", "hello"))

    assert result["ok"] is True
    assert captured["method"] == "POST"
    assert captured["endpoint"] == "chat.postMessage"
    assert captured["json_data"] == {"channel": "C999", "text": "hello"}


def test_post_message_retries_with_org_credentials_on_channel_not_found(monkeypatch) -> None:
    connector = SlackConnector(
        organization_id="00000000-0000-0000-0000-000000000001",
        user_id="11111111-1111-1111-1111-111111111111",
    )

    calls: list[str | None] = []

    async def _fake_make_request(method: str, endpoint: str, **kwargs: object):
        assert method == "POST"
        assert endpoint == "chat.postMessage"
        calls.append(connector.user_id)
        if len(calls) == 1:
            raise ValueError("Slack API error: channel_not_found")
        return {
            "ok": True,
            "channel": str(kwargs.get("json_data", {}).get("channel")),
            "ts": "2.3",
            "message": {"text": "hello"},
        }

    monkeypatch.setattr(connector, "_make_request", _fake_make_request)

    result = asyncio.run(connector.post_message("C0AEA4J556F", "hello"))

    assert result["ok"] is True
    assert calls == ["11111111-1111-1111-1111-111111111111", None]
    assert connector.user_id == "11111111-1111-1111-1111-111111111111"
