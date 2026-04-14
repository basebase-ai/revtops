import asyncio
from types import SimpleNamespace
from uuid import UUID

from api.routes import workflows


class _FakeExecuteResult:
    def __init__(self, workflow: object) -> None:
        self._workflow = workflow

    def scalar_one_or_none(self) -> object:
        return self._workflow


class _FakeSession:
    def __init__(self, workflow: object) -> None:
        self.workflow = workflow
        self.added: list[object] = []
        self.committed = 0

    async def execute(self, _query: object) -> _FakeExecuteResult:
        return _FakeExecuteResult(self.workflow)

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.committed += 1

    async def refresh(self, _obj: object) -> None:
        return None


class _FakeSessionFactory:
    def __init__(self, session: _FakeSession) -> None:
        self.session = session

    async def __aenter__(self) -> _FakeSession:
        return self.session

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class _FakeConversation:
    def __init__(self, **kwargs) -> None:
        self.id = UUID("00000000-0000-0000-0000-0000000000c0")
        for key, value in kwargs.items():
            setattr(self, key, value)


class _FakeTask:
    id = "task-123"


class _FakeExecuteWorkflowTask:
    @staticmethod
    def delay(**_kwargs) -> _FakeTask:
        return _FakeTask()


def test_trigger_workflow_creates_private_conversation_for_owner_by_default(monkeypatch) -> None:
    org_id = UUID("00000000-0000-0000-0000-0000000000a1")
    creator_id = UUID("00000000-0000-0000-0000-0000000000b2")

    fake_workflow = SimpleNamespace(
        id=UUID("00000000-0000-0000-0000-0000000000d3"),
        organization_id=org_id,
        created_by_user_id=creator_id,
        archived_at=None,
        is_enabled=True,
        prompt="Do work",
        name="Parent workflow",
    )
    fake_session = _FakeSession(fake_workflow)

    monkeypatch.setattr(workflows, "get_session", lambda **_kwargs: _FakeSessionFactory(fake_session))
    monkeypatch.setattr("models.conversation.Conversation", _FakeConversation)
    monkeypatch.setattr("workers.tasks.workflows.execute_workflow", _FakeExecuteWorkflowTask)

    auth = SimpleNamespace(
        user_id=UUID("00000000-0000-0000-0000-0000000000e4"),
        organization_id=org_id,
        is_global_admin=False,
    )

    response = asyncio.run(
        workflows.trigger_workflow(
            organization_id=str(org_id),
            workflow_id=str(fake_workflow.id),
            body=workflows.TriggerWorkflowRequest(),
            user_id=None,
            auth=auth,
        )
    )

    assert response.status == "queued"
    assert response.conversation_id == "00000000-0000-0000-0000-0000000000c0"
    assert len(fake_session.added) == 1

    conversation = fake_session.added[0]
    assert conversation.scope == "private"
    assert conversation.type == "workflow"
    assert conversation.user_id == creator_id
