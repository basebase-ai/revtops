from api.routes.sync import (
    _extract_workflow_task_org_id,
    _is_trackable_admin_task,
    _parse_celery_kwargs,
)


def test_is_trackable_admin_task_supports_legacy_and_dotted_workflow_name() -> None:
    assert _is_trackable_admin_task("workers.tasks.workflows.execute_workflow") is True
    assert _is_trackable_admin_task("backend.workers.tasks.workflows.execute_workflow") is True
    assert _is_trackable_admin_task("workers.tasks.sync.sync_organization") is True
    assert _is_trackable_admin_task("workers.tasks.other.unrelated") is False


def test_parse_celery_kwargs_handles_dict_and_string() -> None:
    assert _parse_celery_kwargs({"organization_id": "org-1"}) == {"organization_id": "org-1"}
    assert _parse_celery_kwargs("{'organization_id': 'org-2'}") == {"organization_id": "org-2"}
    assert _parse_celery_kwargs("") == {}


def test_extract_workflow_task_org_id_prefers_kwargs_then_args() -> None:
    args = ["wf-1", "manual", None, None, "org-from-args"]
    assert _extract_workflow_task_org_id(args, {"organization_id": "org-from-kwargs"}) == "org-from-kwargs"
    assert _extract_workflow_task_org_id(args, {}) == "org-from-args"
