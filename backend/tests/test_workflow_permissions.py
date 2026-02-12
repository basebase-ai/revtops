from workers.tasks.workflows import compute_effective_auto_approve_tools


def test_root_workflow_keeps_configured_auto_approve_tools() -> None:
    effective = compute_effective_auto_approve_tools(
        workflow_auto_approve_tools=["send_slack", "run_sql_query"],
        parent_auto_approve_tools=None,
    )
    assert effective == ["send_slack", "run_sql_query"]


def test_child_workflow_is_intersection_with_parent_permissions() -> None:
    effective = compute_effective_auto_approve_tools(
        workflow_auto_approve_tools=["send_slack", "send_email", "run_sql_query"],
        parent_auto_approve_tools=["send_slack", "run_sql_query"],
    )
    assert effective == ["send_slack", "run_sql_query"]


def test_child_workflow_cannot_escalate_if_parent_has_no_permissions() -> None:
    effective = compute_effective_auto_approve_tools(
        workflow_auto_approve_tools=["send_slack"],
        parent_auto_approve_tools=[],
    )
    assert effective == []
