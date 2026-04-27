from agents.orchestrator import _workflow_target_is_slack_dm


def test_workflow_target_is_slack_dm_true_for_dm_channel() -> None:
    assert _workflow_target_is_slack_dm({"is_workflow": True, "slack_channel_id": "D12345"}) is True


def test_workflow_target_is_slack_dm_false_for_non_dm_channel() -> None:
    assert _workflow_target_is_slack_dm({"is_workflow": True, "slack_channel_id": "C12345"}) is False


def test_workflow_target_is_slack_dm_false_without_workflow_flag() -> None:
    assert _workflow_target_is_slack_dm({"slack_channel_id": "D12345"}) is False
