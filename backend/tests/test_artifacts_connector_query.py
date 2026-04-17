import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

from agents import tools
from connectors.artifacts import ArtifactConnector


class _FakeExecuteResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _DetachingArtifact:
    def __init__(self):
        self.detached = False

    def _value(self, value):
        if self.detached:
            raise RuntimeError("detached")
        return value

    @property
    def id(self):
        return self._value("22c70309-d097-4e8e-a50a-e4ad7efc789a")

    @property
    def title(self):
        return self._value("Test Artifact")

    @property
    def filename(self):
        return self._value("artifact.md")

    @property
    def content_type(self):
        return self._value("markdown")

    @property
    def content(self):
        return self._value("hello")


class _FakeSession:
    def __init__(self, artifact):
        self.artifact = artifact

    async def execute(self, _query):
        return _FakeExecuteResult(self.artifact)


def test_query_materializes_payload_before_session_cleanup(monkeypatch):
    artifact = _DetachingArtifact()
    fake_session = _FakeSession(artifact)

    @asynccontextmanager
    async def _fake_get_session(*_args, **_kwargs):
        yield fake_session
        artifact.detached = True

    monkeypatch.setattr("connectors.artifacts.get_session", _fake_get_session)
    connector = ArtifactConnector(
        organization_id="00000000-0000-0000-0000-000000000010",
        user_id="00000000-0000-0000-0000-000000000011",
    )

    result = asyncio.run(connector.query("read 22c70309-d097-4e8e-a50a-e4ad7efc789a"))

    assert result["id"] == "22c70309-d097-4e8e-a50a-e4ad7efc789a"
    assert result["content"] == "hello"


def test_query_on_connector_fetches_artifact_payload(monkeypatch):
    requested_queries: list[str] = []

    class _FakeArtifactsConnector:
        async def query(self, request: str):
            requested_queries.append(request)
            return {
                "id": "22c70309-d097-4e8e-a50a-e4ad7efc789a",
                "title": "Fetched artifact",
                "filename": "artifact.md",
                "content_type": "markdown",
                "content": "hello from connector",
            }

    async def _allow_connector_call(*_args, **_kwargs):
        return SimpleNamespace(allowed=True, deny_reason=None)

    async def _fake_get_connector_instance(*_args, **_kwargs):
        return _FakeArtifactsConnector(), None

    monkeypatch.setattr(tools, "check_connector_call", _allow_connector_call)
    monkeypatch.setattr(tools, "_get_connector_instance", _fake_get_connector_instance)

    result = asyncio.run(
        tools._query_on_connector(
            {
                "connector": "artifacts",
                "query": "read 22c70309-d097-4e8e-a50a-e4ad7efc789a",
            },
            organization_id="00000000-0000-0000-0000-000000000010",
            user_id="00000000-0000-0000-0000-000000000011",
        )
    )

    assert requested_queries == ["read 22c70309-d097-4e8e-a50a-e4ad7efc789a"]
    assert result["id"] == "22c70309-d097-4e8e-a50a-e4ad7efc789a"
    assert result["filename"] == "artifact.md"
    assert result["content"] == "hello from connector"
