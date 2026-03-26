from messengers._workspace import _build_workflow_context_for_message


def test_build_workflow_context_for_slack_includes_channel_fields() -> None:
    ctx = {
        "channel_id": "C123",
        "thread_ts": "1700000000.001",
        "channel_name": "sales-team",
    }

    workflow_context = _build_workflow_context_for_message("slack", ctx)

    assert workflow_context is not None
    assert workflow_context["slack_channel_id"] == "C123"
    assert workflow_context["slack_thread_ts"] == "1700000000.001"
    assert workflow_context["slack_channel_name"] == "sales-team"


def test_build_workflow_context_preserves_existing_values() -> None:
    ctx = {
        "channel_id": "C999",
        "thread_ts": "1700000000.999",
        "channel_name": "engineering",
        "workflow_context": {
            "workflow_id": "wf_1",
            "slack_channel_id": "C123",
            "slack_thread_ts": "1700000000.001",
            "slack_channel_name": "sales",
        },
    }

    workflow_context = _build_workflow_context_for_message("slack", ctx)

    assert workflow_context == {
        "workflow_id": "wf_1",
        "slack_channel_id": "C123",
        "slack_thread_ts": "1700000000.001",
        "slack_channel_name": "sales",
    }
