import asyncio
from types import SimpleNamespace
from uuid import UUID

from services import slack_conversations


class _FakeScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeExecuteResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _FakeScalarResult(self._rows)

    def scalar_one_or_none(self):
        if not self._rows:
            return None
        return self._rows[0]


class _FakeSession:
    def __init__(self, query_results):
        self._query_results = list(query_results)

    async def execute(self, _query):
        return _FakeExecuteResult(self._query_results.pop(0))


class _FakeAdminSessionContext:
    def __init__(self, query_results):
        self._session = _FakeSession(query_results)

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeSlackConnector:
    def __init__(self, organization_id: str):
        self.organization_id = organization_id

    async def get_user_info(self, slack_user_id: str):
        assert slack_user_id == "U123"
        return {
            "name": "jane-doe",
            "real_name": "Jane Doe",
            "profile": {
                "email": "",
                "display_name": "Jane Doe",
                "real_name": "Jane Doe",
                "display_name_normalized": "Jane Doe",
                "real_name_normalized": "Jane Doe",
            },
        }


def test_resolve_revtops_user_falls_back_to_connected_slack_name_match(monkeypatch):
    org_id = "11111111-1111-1111-1111-111111111111"
    jane_id = UUID("22222222-2222-2222-2222-222222222222")
    john_id = UUID("33333333-3333-3333-3333-333333333333")

    users = [
        SimpleNamespace(id=john_id, email="john@acme.com", name="John Smith"),
        SimpleNamespace(id=jane_id, email="other@acme.com", name="  Jane   Doe  "),
    ]
    integrations = [
        SimpleNamespace(user_id=jane_id, connected_by_user_id=None, extra_data={}, id="int-1"),
    ]

    monkeypatch.setattr(
        slack_conversations,
        "get_admin_session",
        lambda: _FakeAdminSessionContext([users, integrations, []]),
    )
    monkeypatch.setattr(slack_conversations, "SlackConnector", _FakeSlackConnector)

    resolved = asyncio.run(
        slack_conversations.resolve_revtops_user_for_slack_actor(
            organization_id=org_id,
            slack_user_id="U123",
        )
    )

    assert resolved is not None
    assert resolved.id == jane_id


def test_resolve_revtops_user_matches_slack_metadata(monkeypatch):
    org_id = "11111111-1111-1111-1111-111111111111"
    jane_id = UUID("22222222-2222-2222-2222-222222222222")

    users = [
        SimpleNamespace(id=jane_id, email="jane@acme.com", name="Jane Doe"),
    ]
    integrations = [
        SimpleNamespace(
            user_id=None,
            connected_by_user_id=jane_id,
            extra_data={"authed_user": {"id": "U456"}},
            id="int-2",
        ),
    ]

    monkeypatch.setattr(
        slack_conversations,
        "get_admin_session",
        lambda: _FakeAdminSessionContext([users, integrations, []]),
    )

    class _NoSlackConnector:
        def __init__(self, organization_id: str):
            raise AssertionError("SlackConnector should not be called when metadata matches")

    monkeypatch.setattr(slack_conversations, "SlackConnector", _NoSlackConnector)

    resolved = asyncio.run(
        slack_conversations.resolve_revtops_user_for_slack_actor(
            organization_id=org_id,
            slack_user_id="U456",
        )
    )

    assert resolved is not None
    assert resolved.id == jane_id


def test_resolve_revtops_user_uses_existing_mapping(monkeypatch):
    org_id = "11111111-1111-1111-1111-111111111111"
    jane_id = UUID("22222222-2222-2222-2222-222222222222")

    users = [
        SimpleNamespace(id=jane_id, email="jane@acme.com", name="Jane Doe"),
    ]
    integrations = []
    mappings = [
        SimpleNamespace(user_id=jane_id, external_userid="U999"),
    ]

    monkeypatch.setattr(
        slack_conversations,
        "get_admin_session",
        lambda: _FakeAdminSessionContext([users, integrations, mappings]),
    )

    class _NoSlackConnector:
        def __init__(self, organization_id: str):
            raise AssertionError("SlackConnector should not be called when mapping exists")

    monkeypatch.setattr(slack_conversations, "SlackConnector", _NoSlackConnector)

    resolved = asyncio.run(
        slack_conversations.resolve_revtops_user_for_slack_actor(
            organization_id=org_id,
            slack_user_id="U999",
        )
    )

    assert resolved is not None
    assert resolved.id == jane_id


def test_merge_participating_user_ids_adds_unique_uuid():
    existing = [UUID("11111111-1111-1111-1111-111111111111")]

    merged = slack_conversations._merge_participating_user_ids(
        existing,
        "22222222-2222-2222-2222-222222222222",
    )

    assert merged == [
        UUID("11111111-1111-1111-1111-111111111111"),
        UUID("22222222-2222-2222-2222-222222222222"),
    ]


def test_merge_participating_user_ids_moves_duplicate_to_end_for_recency():
    existing = [
        UUID("11111111-1111-1111-1111-111111111111"),
        UUID("22222222-2222-2222-2222-222222222222"),
    ]

    merged = slack_conversations._merge_participating_user_ids(
        existing,
        "11111111-1111-1111-1111-111111111111",
    )

    assert merged == [
        UUID("22222222-2222-2222-2222-222222222222"),
        UUID("11111111-1111-1111-1111-111111111111"),
    ]


def test_resolve_current_revtops_user_id_prefers_linked_user_then_latest_participant():
    linked_user = SimpleNamespace(id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"))
    conversation = SimpleNamespace(
        user_id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        participating_user_ids=[UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")],
    )

    resolved_with_link = slack_conversations._resolve_current_revtops_user_id(
        linked_user=linked_user,
        conversation=conversation,
    )
    assert resolved_with_link == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"

    resolved_without_link = slack_conversations._resolve_current_revtops_user_id(
        linked_user=None,
        conversation=conversation,
    )
    assert resolved_without_link == "cccccccc-cccc-cccc-cccc-cccccccccccc"


def test_resolve_current_revtops_user_id_uses_last_participant_when_primary_missing():
    conversation = SimpleNamespace(
        user_id=None,
        participating_user_ids=[
            UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"),
            UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"),
        ],
    )

    resolved_fallback = slack_conversations._resolve_current_revtops_user_id(
        linked_user=None,
        conversation=conversation,
    )
    assert resolved_fallback == "dddddddd-dddd-dddd-dddd-dddddddddddd"


def test_resolve_current_revtops_user_id_falls_back_to_primary_when_no_participants():
    conversation = SimpleNamespace(
        user_id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        participating_user_ids=[],
    )

    resolved_fallback = slack_conversations._resolve_current_revtops_user_id(
        linked_user=None,
        conversation=conversation,
    )
    assert resolved_fallback == "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def test_resolve_thread_active_user_id_uses_new_linked_speaker_on_handoff():
    linked_user = SimpleNamespace(id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"))
    conversation = SimpleNamespace(
        user_id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        participating_user_ids=[UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")],
    )

    resolved = slack_conversations._resolve_thread_active_user_id(
        linked_user=linked_user,
        conversation=conversation,
        speaker_changed=True,
    )

    assert resolved == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def test_resolve_thread_active_user_id_clears_active_user_on_unresolved_handoff():
    conversation = SimpleNamespace(
        user_id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        participating_user_ids=[UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")],
    )

    resolved = slack_conversations._resolve_thread_active_user_id(
        linked_user=None,
        conversation=conversation,
        speaker_changed=True,
    )

    assert resolved is None


def test_process_slack_thread_reply_applies_speaker_and_global_handoff_before_other_processing(monkeypatch):
    events: list[str] = []
    conversation = SimpleNamespace(
        id=UUID("99999999-9999-9999-9999-999999999999"),
        source_user_id="U_OLD",
        user_id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        participating_user_ids=[UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")],
    )

    class _FakeSlackConnectorForThread:
        def __init__(self, organization_id: str):
            self.organization_id = organization_id

        async def add_reaction(self, channel: str, timestamp: str):
            events.append("add_reaction")

        async def remove_reaction(self, channel: str, timestamp: str):
            events.append("remove_reaction")

    async def _fake_find_org(_team_id: str):
        return "11111111-1111-1111-1111-111111111111"

    async def _fake_find_thread_conversation(**_kwargs):
        return conversation

    async def _fake_find_or_create_conversation(**kwargs):
        events.append(f"find_or_create:{kwargs['slack_user_id']}:{kwargs['revtops_user_id']}")
        if kwargs["slack_user_id"] == "U_NEW" and kwargs["revtops_user_id"] is None:
            conversation.source_user_id = "U_NEW"
            conversation.user_id = None
        if kwargs["revtops_user_id"]:
            conversation.user_id = UUID(kwargs["revtops_user_id"])
        return conversation

    async def _fake_fetch_slack_user_info(**_kwargs):
        events.append("fetch_slack_user")
        return {}

    async def _fake_resolve_user(**_kwargs):
        events.append("resolve_user")
        return SimpleNamespace(
            id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
            email="new.user@acme.com",
        )

    async def _fake_stream_and_post_responses(**_kwargs):
        events.append("stream")
        return 3

    monkeypatch.setattr(slack_conversations, "find_organization_by_slack_team", _fake_find_org)
    monkeypatch.setattr(slack_conversations, "find_thread_conversation", _fake_find_thread_conversation)
    monkeypatch.setattr(slack_conversations, "find_or_create_conversation", _fake_find_or_create_conversation)
    monkeypatch.setattr(slack_conversations, "_fetch_slack_user_info", _fake_fetch_slack_user_info)
    monkeypatch.setattr(slack_conversations, "resolve_revtops_user_for_slack_actor", _fake_resolve_user)
    monkeypatch.setattr(slack_conversations, "_stream_and_post_responses", _fake_stream_and_post_responses)
    monkeypatch.setattr(slack_conversations, "SlackConnector", _FakeSlackConnectorForThread)

    result = asyncio.run(
        slack_conversations.process_slack_thread_reply(
            team_id="T123",
            channel_id="C123",
            user_id="U_NEW",
            message_text="hello",
            thread_ts="111.222",
            event_ts="111.333",
            files=None,
        )
    )

    assert result["status"] == "success"
    assert events.index("add_reaction") < events.index("fetch_slack_user")
    assert events.index("find_or_create:U_NEW:None") < events.index("fetch_slack_user")
    assert events.index("add_reaction") < events.index("find_or_create:U_NEW:aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def test_process_slack_mention_clears_active_user_on_unresolved_speaker_handoff(monkeypatch):
    events: list[str] = []
    existing_conversation = SimpleNamespace(
        id=UUID("99999999-9999-9999-9999-999999999999"),
        source_user_id="U_OLD",
        user_id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        participating_user_ids=[UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")],
    )
    persisted_conversation = SimpleNamespace(
        id=existing_conversation.id,
        source_user_id="U_NEW",
        user_id=None,
        participating_user_ids=[UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")],
    )

    class _FakeSlackConnectorForMention:
        def __init__(self, organization_id: str):
            self.organization_id = organization_id

        async def add_reaction(self, channel: str, timestamp: str):
            events.append("add_reaction")

        async def remove_reaction(self, channel: str, timestamp: str):
            events.append("remove_reaction")

    async def _fake_find_org(_team_id: str):
        return "11111111-1111-1111-1111-111111111111"

    async def _fake_fetch_slack_user_info(**_kwargs):
        return {}

    async def _fake_resolve_user(**_kwargs):
        return None

    async def _fake_find_thread_conversation(**_kwargs):
        return existing_conversation

    async def _fake_find_or_create_conversation(**kwargs):
        events.append(
            f"find_or_create:{kwargs['slack_user_id']}:{kwargs['revtops_user_id']}:{kwargs['clear_current_user_on_unresolved']}"
        )
        return persisted_conversation

    async def _fake_stream_and_post_responses(**kwargs):
        events.append(f"stream_user_id:{kwargs['orchestrator'].user_id}")
        return 2

    monkeypatch.setattr(slack_conversations, "find_organization_by_slack_team", _fake_find_org)
    monkeypatch.setattr(slack_conversations, "_fetch_slack_user_info", _fake_fetch_slack_user_info)
    monkeypatch.setattr(slack_conversations, "resolve_revtops_user_for_slack_actor", _fake_resolve_user)
    monkeypatch.setattr(slack_conversations, "find_thread_conversation", _fake_find_thread_conversation)
    monkeypatch.setattr(slack_conversations, "find_or_create_conversation", _fake_find_or_create_conversation)
    monkeypatch.setattr(slack_conversations, "_stream_and_post_responses", _fake_stream_and_post_responses)
    monkeypatch.setattr(slack_conversations, "SlackConnector", _FakeSlackConnectorForMention)

    result = asyncio.run(
        slack_conversations.process_slack_mention(
            team_id="T123",
            channel_id="C123",
            user_id="U_NEW",
            message_text="hello",
            thread_ts="111.222",
            files=None,
        )
    )

    assert result["status"] == "success"
    assert "find_or_create:U_NEW:None:True" in events
    assert "stream_user_id:None" in events
