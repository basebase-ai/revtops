import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from agents.orchestrator import ChatOrchestrator


class _FakeScalarResult:
    def __init__(self, values):
        self._values = values

    def all(self):
        return list(self._values)


class _FakeResult:
    def __init__(self, *, scalars=None, scalar_one=None, rows=None):
        self._scalars = list(scalars or [])
        self._scalar_one = scalar_one
        self._rows = list(rows or [])

    def scalars(self):
        return _FakeScalarResult(self._scalars)

    def scalar_one_or_none(self):
        return self._scalar_one

    def all(self):
        return list(self._rows)


class _FakeSession:
    def __init__(self, results):
        self._results = list(results)

    async def execute(self, _query):
        if not self._results:
            raise AssertionError("Unexpected query executed")
        return self._results.pop(0)


@asynccontextmanager
async def _fake_get_session(organization_id=None):
    yield _fake_get_session.session


def test_load_context_profile_includes_all_participants_and_recent_user_commands(monkeypatch):
    org_id = uuid4()
    current_user_id = uuid4()
    recent_user_id = uuid4()
    current_membership_id = uuid4()
    recent_membership_id = uuid4()

    memories = [
        SimpleNamespace(
            id=uuid4(),
            entity_type="user",
            entity_id=current_user_id,
            category="personal",
            content="Prefers concise summaries",
            created_at=datetime.now(UTC),
        ),
        SimpleNamespace(
            id=uuid4(),
            entity_type="user",
            entity_id=recent_user_id,
            category="personal",
            content="Owns enterprise accounts",
            created_at=datetime.now(UTC),
        ),
        SimpleNamespace(
            id=uuid4(),
            entity_type="organization_member",
            entity_id=recent_membership_id,
            category="professional",
            content="Leads deal reviews",
            created_at=datetime.now(UTC),
        ),
    ]

    conversation_user_ids = [recent_user_id, current_user_id]
    memberships = [
        SimpleNamespace(
            id=current_membership_id,
            user_id=current_user_id,
            organization_id=org_id,
            title="AE",
            reports_to_membership_id=None,
        ),
        SimpleNamespace(
            id=recent_membership_id,
            user_id=recent_user_id,
            organization_id=org_id,
            title="Sales Director",
            reports_to_membership_id=None,
        ),
    ]

    user_rows = [
        (current_user_id, "Current User"),
        (recent_user_id, "Recent User"),
    ]

    _fake_get_session.session = _FakeSession(
        [
            _FakeResult(scalars=memories),
            _FakeResult(scalars=conversation_user_ids),
            _FakeResult(scalar_one="Use bullet points for updates"),
            _FakeResult(scalars=memberships),
            _FakeResult(rows=user_rows),
        ]
    )

    monkeypatch.setattr("agents.orchestrator.get_session", _fake_get_session)

    orchestrator = ChatOrchestrator(
        user_id=str(current_user_id),
        organization_id=str(org_id),
        conversation_id=str(uuid4()),
    )

    profile = asyncio.run(orchestrator._load_context_profile())

    assert orchestrator.agent_global_commands == "Use bullet points for updates"
    assert len(profile["participant_profiles"]) == 2

    recent_profile = next(
        p for p in profile["participant_profiles"] if p["user_id"] == str(recent_user_id)
    )
    assert recent_profile["name"] == "Recent User"
    assert recent_profile["membership_title"] == "Sales Director"
    assert any(m["content"] == "Owns enterprise accounts" for m in recent_profile["user_memories"])
    assert any(m["content"] == "Leads deal reviews" for m in recent_profile["job_memories"])
