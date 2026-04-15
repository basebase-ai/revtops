import asyncio

from workers.tasks import workflows


def test_record_workflow_query_outcome_records_completed_status(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def _fake_record_query_outcome(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(
        "services.query_outcome_metrics.record_query_outcome",
        _fake_record_query_outcome,
    )

    asyncio.run(
        workflows._record_workflow_query_outcome(
            result={"status": "completed", "conversation_id": "conv-123"},
            workflow_id="wf-123",
        )
    )

    assert captured["platform"] == "workflow"
    assert captured["was_success"] is True
    assert captured["failure_reason"] is None
    assert captured["conversation_id"] == "conv-123"


def test_record_workflow_query_outcome_records_failed_status(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def _fake_record_query_outcome(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(
        "services.query_outcome_metrics.record_query_outcome",
        _fake_record_query_outcome,
    )

    asyncio.run(
        workflows._record_workflow_query_outcome(
            result={"status": "failed", "error": "Timeout while calling provider: trace"},
            workflow_id="wf-456",
        )
    )

    assert captured["platform"] == "workflow"
    assert captured["was_success"] is False
    assert captured["failure_reason"] == "timeout while calling provider"
    assert captured["conversation_id"] == "workflow:wf-456"


def test_record_workflow_query_outcome_ignores_skipped_status(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    async def _fake_record_query_outcome(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(
        "services.query_outcome_metrics.record_query_outcome",
        _fake_record_query_outcome,
    )

    asyncio.run(
        workflows._record_workflow_query_outcome(
            result={"status": "skipped", "reason": "Workflow is disabled"},
            workflow_id="wf-789",
        )
    )

    assert calls == []


def test_record_workflow_query_outcome_records_skipped_privilege_fast_fail(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def _fake_record_query_outcome(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(
        "services.query_outcome_metrics.record_query_outcome",
        _fake_record_query_outcome,
    )

    asyncio.run(
        workflows._record_workflow_query_outcome(
            result={"status": "skipped", "reason": "RLS permission denied on workflow_runs"},
            workflow_id="wf-fast-fail",
        )
    )

    assert captured["platform"] == "workflow"
    assert captured["was_success"] is False
    assert captured["failure_reason"] == "rls permission denied on workflow_runs"
    assert captured["conversation_id"] == "workflow:wf-fast-fail"


def test_record_workflow_query_outcome_records_error_status_as_failure(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def _fake_record_query_outcome(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(
        "services.query_outcome_metrics.record_query_outcome",
        _fake_record_query_outcome,
    )

    asyncio.run(
        workflows._record_workflow_query_outcome(
            result={"status": "error", "error": "Model is not available"},
            workflow_id="wf-999",
        )
    )

    assert captured["platform"] == "workflow"
    assert captured["was_success"] is False
    assert captured["failure_reason"] == "model is not available"
    assert captured["conversation_id"] == "workflow:wf-999"
