from uuid import UUID

from agents import orchestrator


def test_merge_participating_user_ids_preserves_order_and_appends_fallback_as_most_recent():
    user_a = UUID("11111111-1111-1111-1111-111111111111")
    user_b = UUID("22222222-2222-2222-2222-222222222222")

    merged = orchestrator._merge_participating_user_ids(
        conversation_user_id=None,
        participating_user_ids=[user_a, user_b],
        fallback_user_id=str(user_a),
    )

    assert merged == [user_b, user_a]


def test_merge_participating_user_ids_includes_conversation_user_when_not_in_participants():
    conversation_user = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

    merged = orchestrator._merge_participating_user_ids(
        conversation_user_id=conversation_user,
        participating_user_ids=[],
        fallback_user_id=None,
    )

    assert merged == [conversation_user]
