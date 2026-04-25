import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

from workers.tasks import workflows


class _FakeExecuteResult:
    def __init__(self, integration: object) -> None:
        self._integration = integration

    def scalar_one_or_none(self) -> object:
        return self._integration


class _FakeSession:
    def __init__(self, integration: object) -> None:
        self._integration = integration

    async def execute(self, _query: object) -> _FakeExecuteResult:
        return _FakeExecuteResult(self._integration)


class _FakeSlackConnector:
    init_kwargs: dict[str, object] | None = None

    def __init__(self, **kwargs: object) -> None:
        _FakeSlackConnector.init_kwargs = kwargs

    async def post_message(
        self,
        channel: str,
        text: str,
        thread_ts: str | None = None,
        blocks: list[dict[str, object]] | None = None,
    ) -> dict[str, str]:
        return {"channel": channel, "ts": "123.456", "text": text, "thread_ts": thread_ts or ""}


def test_action_send_slack_posts_with_team_id(monkeypatch) -> None:
    integration = SimpleNamespace(
        nango_connection_id="conn_123",
        extra_data={"team_id": "T123"},
    )

    @asynccontextmanager
    async def _fake_get_session(*_args: object, **_kwargs: object):
        yield _FakeSession(integration)

    monkeypatch.setattr("models.database.get_session", _fake_get_session)
    monkeypatch.setattr("connectors.slack.SlackConnector", _FakeSlackConnector)

    async def _run() -> dict[str, object]:
        return await workflows._action_send_slack(
            params={"channel": "#alerts", "message": "hello"},
            context={"organization_id": "00000000-0000-0000-0000-000000000001"},
            workflow=None,
        )

    result = asyncio.run(_run())

    assert result["status"] == "completed"
    assert _FakeSlackConnector.init_kwargs == {
        "organization_id": "00000000-0000-0000-0000-000000000001",
        "team_id": "T123",
    }


def test_action_send_slack_blocks_unlisted_channel(monkeypatch) -> None:
    integration = SimpleNamespace(
        nango_connection_id="conn_123",
        extra_data={"team_id": "T123"},
    )
    workflow = SimpleNamespace(
        id="wf_1",
        output_config={"allowed_slack_channels": ["#ops-alerts"]},
    )

    @asynccontextmanager
    async def _fake_get_session(*_args: object, **_kwargs: object):
        yield _FakeSession(integration)

    monkeypatch.setattr("models.database.get_session", _fake_get_session)
    monkeypatch.setattr("connectors.slack.SlackConnector", _FakeSlackConnector)

    async def _run() -> dict[str, object]:
        return await workflows._action_send_slack(
            params={"channel": "#random", "message": "hello"},
            context={"organization_id": "00000000-0000-0000-0000-000000000001"},
            workflow=workflow,
        )

    result = asyncio.run(_run())

    assert result["status"] == "failed"
    assert "explicitly allowed channels" in str(result["error"])


def test_workflow_slack_target_allows_dm_channel() -> None:
    assert workflows._is_allowed_workflow_slack_target("D12345", ["#ops"]) is True


def test_action_send_slack_allows_blocks_without_message(monkeypatch) -> None:
    integration = SimpleNamespace(
        nango_connection_id="conn_123",
        extra_data={"team_id": "T123"},
    )

    @asynccontextmanager
    async def _fake_get_session(*_args: object, **_kwargs: object):
        yield _FakeSession(integration)

    monkeypatch.setattr("models.database.get_session", _fake_get_session)
    monkeypatch.setattr("connectors.slack.SlackConnector", _FakeSlackConnector)

    async def _run() -> dict[str, object]:
        return await workflows._action_send_slack(
            params={
                "channel": "#alerts",
                "message": "",
                "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": "hello"}}],
            },
            context={"organization_id": "00000000-0000-0000-0000-000000000001"},
            workflow=None,
        )

    result = asyncio.run(_run())

    assert result["status"] == "completed"
