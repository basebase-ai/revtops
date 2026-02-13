from api.routes.sync import (
    _get_celery_task_args,
    _get_celery_task_kwargs,
    _build_workflow_run_admin_job,
    _extract_workflow_task_org_id,
    _is_active_workflow_run_status,
    _is_trackable_admin_task,
    _parse_celery_kwargs,
)


def test_is_trackable_admin_task_supports_legacy_and_dotted_workflow_name() -> None:
    assert _is_trackable_admin_task("workers.tasks.workflows.execute_workflow") is True
    assert _is_trackable_admin_task("backend.workers.tasks.workflows.execute_workflow") is True
    assert _is_trackable_admin_task("workers.tasks.sync.sync_organization") is True
    assert _is_trackable_admin_task("backend.workers.tasks.sync.sync_organization") is True
    assert _is_trackable_admin_task("workers.tasks.other.unrelated") is False


def test_parse_celery_kwargs_handles_dict_and_string() -> None:
    assert _parse_celery_kwargs({"organization_id": "org-1"}) == {"organization_id": "org-1"}
    assert _parse_celery_kwargs("{'organization_id': 'org-2'}") == {"organization_id": "org-2"}
    assert _parse_celery_kwargs("") == {}


def test_extract_workflow_task_org_id_prefers_kwargs_then_args() -> None:
    args = ["wf-1", "manual", None, None, "org-from-args"]
    assert _extract_workflow_task_org_id(args, {"organization_id": "org-from-kwargs"}) == "org-from-kwargs"
    assert _extract_workflow_task_org_id(args, {}) == "org-from-args"


def test_get_celery_task_args_supports_argsrepr_fallback() -> None:
    task = {"args": "", "argsrepr": "('wf-1', 'manual', None, None, 'org-1')"}
    assert _get_celery_task_args(task) == ["wf-1", "manual", None, None, "org-1"]


def test_get_celery_task_kwargs_supports_kwargsrepr_fallback() -> None:
    task = {"kwargs": "", "kwargsrepr": "{'organization_id': 'org-2', 'trigger': 'manual'}"}
    assert _get_celery_task_kwargs(task) == {"organization_id": "org-2", "trigger": "manual"}


def test_is_active_workflow_run_status_only_matches_in_progress_statuses() -> None:
    assert _is_active_workflow_run_status("pending") is True
    assert _is_active_workflow_run_status("running") is True
    assert _is_active_workflow_run_status("completed") is False


def test_build_workflow_run_admin_job_sets_expected_fields() -> None:
    from datetime import datetime
    from uuid import uuid4

    from models.workflow import WorkflowRun

    run = WorkflowRun(
        id=uuid4(),
        workflow_id=uuid4(),
        organization_id=uuid4(),
        triggered_by="manual",
        status="running",
        started_at=datetime(2025, 1, 1, 12, 0, 0),
    )

    admin_job = _build_workflow_run_admin_job(
        run=run,
        workflow_name="Nightly Enrichment",
        organization_name="Acme Corp",
    )

    assert admin_job.type == "workflow"
    assert admin_job.status == "running"
    assert admin_job.title == "Workflow run: Nightly Enrichment"
    assert admin_job.organization_name == "Acme Corp"
    assert admin_job.metadata is not None
    assert admin_job.metadata.get("source") == "workflow_runs"
