from agents.orchestrator import _should_include_cross_conversation_history


def test_should_include_cross_conversation_history_when_user_explicitly_requests_it() -> None:
    assert _should_include_cross_conversation_history(
        "Can you search across my conversations and summarize the decisions?"
    )
    assert _should_include_cross_conversation_history(
        "Please check all past chats, including shared conversations."
    )


def test_should_not_include_cross_conversation_history_by_default() -> None:
    assert not _should_include_cross_conversation_history("What did we just discuss?")
    assert not _should_include_cross_conversation_history("Summarize this chat.")
