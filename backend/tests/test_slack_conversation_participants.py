from types import SimpleNamespace

from sqlalchemy.dialects import postgresql

from api.routes.chat import _build_conversation_access_filter
from services.slack_conversations import _merge_participating_user_ids


def test_merge_participating_user_ids_adds_latest_without_duplicates():
    merged = _merge_participating_user_ids(["U1", "U2", "U1"], "U3")
    assert merged == ["U1", "U2", "U3"]


def test_merge_participating_user_ids_keeps_existing_order():
    merged = _merge_participating_user_ids(["U9", "U7"], "U7")
    assert merged == ["U9", "U7"]


def test_conversation_access_filter_includes_participant_overlap():
    auth = SimpleNamespace(
        user_id="11111111-1111-1111-1111-111111111111",
        organization_id="22222222-2222-2222-2222-222222222222",
    )

    filt = _build_conversation_access_filter(auth, {"U1", "U2"})
    sql = str(
        filt.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "participating_user_ids" in sql
    assert "&&" in sql
