from uuid import UUID

from api.routes.workflows import _workflow_conversation_participants as api_participants
from workers.tasks.workflows import _workflow_conversation_participants as task_participants


def test_api_workflow_conversation_participants_includes_creator_and_trigger_user() -> None:
    creator = UUID("00000000-0000-0000-0000-000000000001")
    trigger_user = UUID("00000000-0000-0000-0000-000000000002")

    participants = api_participants(
        workflow_creator_user_id=creator,
        trigger_user_uuid=trigger_user,
    )

    assert participants == [creator, trigger_user]


def test_api_workflow_conversation_participants_deduplicates_when_same_user() -> None:
    creator = UUID("00000000-0000-0000-0000-000000000001")

    participants = api_participants(
        workflow_creator_user_id=creator,
        trigger_user_uuid=creator,
    )

    assert participants == [creator]


def test_task_workflow_conversation_participants_merges_existing_and_trigger_user() -> None:
    creator = UUID("00000000-0000-0000-0000-000000000001")
    existing = [UUID("00000000-0000-0000-0000-000000000003")]
    trigger_user_id = "00000000-0000-0000-0000-000000000002"

    participants = task_participants(
        workflow_creator_user_id=creator,
        triggered_by_user_id=trigger_user_id,
        existing_participants=existing,
    )

    assert participants == [existing[0], creator, UUID(trigger_user_id)]


def test_task_workflow_conversation_participants_ignores_invalid_trigger_user() -> None:
    creator = UUID("00000000-0000-0000-0000-000000000001")

    participants = task_participants(
        workflow_creator_user_id=creator,
        triggered_by_user_id="not-a-uuid",
        existing_participants=None,
    )

    assert participants == [creator]
