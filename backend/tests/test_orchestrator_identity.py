from agents.orchestrator import ChatOrchestrator


def test_resolve_current_user_uuid_returns_user_uuid_when_valid():
    orchestrator = ChatOrchestrator(
        user_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        organization_id="11111111-1111-1111-1111-111111111111",
        source="slack_thread",
        source_user_id="U123",
    )

    resolved = orchestrator._resolve_current_user_uuid()

    assert resolved is not None
    assert str(resolved) == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def test_resolve_current_user_uuid_returns_none_when_missing_or_invalid():
    missing_orchestrator = ChatOrchestrator(
        user_id=None,
        organization_id="11111111-1111-1111-1111-111111111111",
        source="slack_thread",
        source_user_id="U123",
    )
    assert missing_orchestrator._resolve_current_user_uuid() is None

    invalid_orchestrator = ChatOrchestrator(
        user_id="not-a-uuid",
        organization_id="11111111-1111-1111-1111-111111111111",
        source="slack_thread",
        source_user_id="U123",
    )
    assert invalid_orchestrator._resolve_current_user_uuid() is None
