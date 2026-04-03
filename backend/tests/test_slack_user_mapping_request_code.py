import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import UUID

from fastapi import HTTPException

from api.auth_middleware import AuthContext
from api.routes import slack_user_mappings


class _FakeScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _FakeExecuteResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _FakeScalarResult(self._rows)


class _FakeSession:
    def __init__(self, mappings):
        self._mappings = mappings
        self._execute_calls = 0

    async def execute(self, _query, _params=None):
        self._execute_calls += 1
        if self._execute_calls == 1:
            return _FakeExecuteResult([])
        return _FakeExecuteResult(self._mappings)


class _FakeRedis:
    def __init__(self):
        self.cooldown_set = False
        self.values = {}
        self.deleted_keys = []

    async def set(self, key, value, nx=False, ex=None):
        if nx:
            if self.cooldown_set:
                return False
            self.cooldown_set = True
            self.values[key] = value
            return True
        self.values[key] = value
        return True

    async def delete(self, key):
        self.deleted_keys.append(key)
        self.values.pop(key, None)


def test_request_code_retries_multiple_slack_identities(monkeypatch):
    org_uuid = UUID("00000000-0000-0000-0000-000000000001")
    user_uuid = UUID("00000000-0000-0000-0000-000000000002")
    mappings = [
        SimpleNamespace(external_userid="U_FIRST", external_email="user@example.com"),
        SimpleNamespace(external_userid="U_SECOND", external_email="user@example.com"),
    ]
    sent_ids = []

    @asynccontextmanager
    async def _fake_get_session(*_args, **_kwargs):
        yield _FakeSession(mappings)

    class _FakeSlackConnector:
        def __init__(self, **_kwargs):
            pass

        async def send_direct_message(self, slack_user_id, text):
            sent_ids.append(slack_user_id)
            if slack_user_id == "U_FIRST":
                raise RuntimeError("first identity failed")
            return {"ok": True, "channel": "D123", "text": text}

    fake_redis = _FakeRedis()
    monkeypatch.setattr(slack_user_mappings, "_resolve_org_and_user", lambda *_: asyncio.sleep(0, result=(org_uuid, user_uuid)))
    monkeypatch.setattr(slack_user_mappings, "_require_slack_integration", lambda *_: asyncio.sleep(0, result=object()))
    monkeypatch.setattr(slack_user_mappings, "get_session", _fake_get_session)
    monkeypatch.setattr(slack_user_mappings, "SlackConnector", _FakeSlackConnector)
    monkeypatch.setattr(slack_user_mappings, "_get_redis", lambda: asyncio.sleep(0, result=fake_redis))
    monkeypatch.setattr(slack_user_mappings.secrets, "randbelow", lambda _n: 123456)

    auth_ctx = AuthContext(
        user_id=user_uuid,
        organization_id=org_uuid,
        email="user@example.com",
        role="user",
        is_global_admin=False,
    )
    response = asyncio.run(
        slack_user_mappings.request_slack_user_mapping_code(
            auth_ctx,
            slack_user_mappings.SlackMappingRequest(
                user_id=str(user_uuid),
                organization_id=str(org_uuid),
                email="user@example.com",
            ),
        )
    )

    assert response == {"status": "sent"}
    assert sent_ids == ["U_FIRST", "U_SECOND"]


def test_request_code_returns_502_when_all_slack_identities_fail(monkeypatch):
    org_uuid = UUID("00000000-0000-0000-0000-000000000001")
    user_uuid = UUID("00000000-0000-0000-0000-000000000002")
    mappings = [SimpleNamespace(external_userid="U_ONLY", external_email="user@example.com")]

    @asynccontextmanager
    async def _fake_get_session(*_args, **_kwargs):
        yield _FakeSession(mappings)

    class _FakeSlackConnector:
        def __init__(self, **_kwargs):
            pass

        async def send_direct_message(self, slack_user_id, text):
            raise RuntimeError(f"failed for {slack_user_id}:{len(text)}")

    fake_redis = _FakeRedis()
    monkeypatch.setattr(slack_user_mappings, "_resolve_org_and_user", lambda *_: asyncio.sleep(0, result=(org_uuid, user_uuid)))
    monkeypatch.setattr(slack_user_mappings, "_require_slack_integration", lambda *_: asyncio.sleep(0, result=object()))
    monkeypatch.setattr(slack_user_mappings, "get_session", _fake_get_session)
    monkeypatch.setattr(slack_user_mappings, "SlackConnector", _FakeSlackConnector)
    monkeypatch.setattr(slack_user_mappings, "_get_redis", lambda: asyncio.sleep(0, result=fake_redis))

    auth_ctx = AuthContext(
        user_id=user_uuid,
        organization_id=org_uuid,
        email="user@example.com",
        role="user",
        is_global_admin=False,
    )
    try:
        asyncio.run(
            slack_user_mappings.request_slack_user_mapping_code(
                auth_ctx,
                slack_user_mappings.SlackMappingRequest(
                    user_id=str(user_uuid),
                    organization_id=str(org_uuid),
                    email="user@example.com",
                ),
            )
        )
        raise AssertionError("Expected HTTPException")
    except HTTPException as exc:
        assert exc.status_code == 502
        assert "Unable to deliver verification code" in exc.detail

    assert fake_redis.deleted_keys
