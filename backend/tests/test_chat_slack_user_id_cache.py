import asyncio
from uuid import UUID

from api.auth_middleware import AuthContext
from api.routes import chat


class _FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}
        self.get_calls: list[str] = []
        self.set_calls: list[tuple[str, str, int | None]] = []

    async def get(self, key: str):
        self.get_calls.append(key)
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None):
        self.set_calls.append((key, value, ex))
        self.store[key] = value


def _auth(user_id: str, org_id: str) -> AuthContext:
    return AuthContext(
        user_id=UUID(user_id),
        organization_id=UUID(org_id),
        email="test@example.com",
        role="member",
        is_global_admin=False,
    )


def test_get_slack_user_ids_passes_caller_session_on_cache_miss(monkeypatch):
    fake_redis = _FakeRedis()
    lookup_calls: list[object] = []

    async def _fake_get_redis():
        return fake_redis

    async def _fake_lookup(org_id: str, user_id: str, session=None):
        lookup_calls.append(session)
        assert org_id == "11111111-1111-1111-1111-111111111111"
        assert user_id == "22222222-2222-2222-2222-222222222222"
        return {"U222"}

    monkeypatch.setattr(chat, "_get_redis", _fake_get_redis)
    monkeypatch.setattr(chat, "get_slack_user_ids_for_revtops_user", _fake_lookup)

    caller_session = object()
    auth = _auth(
        user_id="22222222-2222-2222-2222-222222222222",
        org_id="11111111-1111-1111-1111-111111111111",
    )
    result = asyncio.run(chat._get_slack_user_ids(auth, session=caller_session))

    assert result == {"U222"}
    assert lookup_calls == [caller_session]
    assert fake_redis.get_calls == [
        "slack_user_ids:11111111-1111-1111-1111-111111111111:22222222-2222-2222-2222-222222222222"
    ]


def test_get_slack_user_ids_cache_isolated_when_switching_users(monkeypatch):
    fake_redis = _FakeRedis()
    lookup_calls: list[tuple[str, str]] = []

    async def _fake_get_redis():
        return fake_redis

    async def _fake_lookup(org_id: str, user_id: str, session=None):
        lookup_calls.append((org_id, user_id))
        return {f"U-{user_id[:4]}"}

    monkeypatch.setattr(chat, "_get_redis", _fake_get_redis)
    monkeypatch.setattr(chat, "get_slack_user_ids_for_revtops_user", _fake_lookup)

    org_id = "11111111-1111-1111-1111-111111111111"
    user_a = _auth("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", org_id)
    user_b = _auth("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", org_id)

    first_a = asyncio.run(chat._get_slack_user_ids(user_a, session=object()))
    first_b = asyncio.run(chat._get_slack_user_ids(user_b, session=object()))
    second_a = asyncio.run(chat._get_slack_user_ids(user_a, session=object()))

    assert first_a == {"U-aaaa"}
    assert first_b == {"U-bbbb"}
    assert second_a == {"U-aaaa"}
    # One DB lookup per unique user; second call for user_a should come from Redis.
    assert lookup_calls == [
        (org_id, "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        (org_id, "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
    ]


def test_get_slack_user_ids_fast_multiuser_requests_do_not_cross_talk(monkeypatch):
    fake_redis = _FakeRedis()

    async def _fake_get_redis():
        return fake_redis

    async def _fake_lookup(org_id: str, user_id: str, session=None):
        # Give different users different timings to simulate a burst of traffic.
        if user_id.startswith("1111"):
            await asyncio.sleep(0.03)
        else:
            await asyncio.sleep(0.005)
        return {f"U-{user_id[-4:]}"}

    monkeypatch.setattr(chat, "_get_redis", _fake_get_redis)
    monkeypatch.setattr(chat, "get_slack_user_ids_for_revtops_user", _fake_lookup)

    org_id = "99999999-9999-9999-9999-999999999999"
    users = [
        _auth("11111111-1111-1111-1111-111111111111", org_id),
        _auth("22222222-2222-2222-2222-222222222222", org_id),
        _auth("33333333-3333-3333-3333-333333333333", org_id),
    ]

    async def _run_batch():
        return await asyncio.gather(*[chat._get_slack_user_ids(u, session=object()) for u in users])

    results = asyncio.run(_run_batch())

    assert results == [{"U-1111"}, {"U-2222"}, {"U-3333"}]
