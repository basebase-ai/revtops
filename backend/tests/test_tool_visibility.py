from agents.registry import get_tools_for_claude, requires_approval
from agents.tools import ALLOWED_TABLES


def test_keep_notes_hidden_outside_workflow_context() -> None:
    tool_names = {tool["name"] for tool in get_tools_for_claude(in_workflow=False)}
    assert "keep_notes" not in tool_names


def test_keep_notes_exposed_inside_workflow_context() -> None:
    tool_names = {tool["name"] for tool in get_tools_for_claude(in_workflow=True)}
    assert "keep_notes" in tool_names


def test_keep_notes_does_not_require_approval_in_workflows() -> None:
    assert requires_approval("keep_notes") is False


def test_run_sql_query_documents_workflow_runs_table() -> None:
    run_sql_query_tool = next(tool for tool in get_tools_for_claude(in_workflow=True) if tool["name"] == "run_sql_query")
    description = run_sql_query_tool["description"]
    assert "- workflow_runs:" in description


def test_workflow_runs_is_queryable_from_run_sql_query() -> None:
    assert "workflow_runs" in ALLOWED_TABLES
