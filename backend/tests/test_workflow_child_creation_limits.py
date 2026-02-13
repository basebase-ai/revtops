from agents.tools import (
    MAX_CREATED_CHILD_WORKFLOWS,
    _workflow_child_creation_limit_error,
)


def test_workflow_child_creation_limit_allows_under_limit() -> None:
    context = {
        "is_workflow": True,
        "workflow_id": "wf-123",
        "created_workflow_count": MAX_CREATED_CHILD_WORKFLOWS - 1,
    }

    assert _workflow_child_creation_limit_error(context) is None


def test_workflow_child_creation_limit_blocks_at_limit() -> None:
    context = {
        "is_workflow": True,
        "workflow_id": "wf-123",
        "created_workflow_count": MAX_CREATED_CHILD_WORKFLOWS,
    }

    error = _workflow_child_creation_limit_error(context)

    assert error is not None
    assert error["status"] == "rejected"
    assert f"at most {MAX_CREATED_CHILD_WORKFLOWS} child workflows" in error["error"]
