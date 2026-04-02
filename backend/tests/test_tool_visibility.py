from pathlib import Path

from agents.registry import get_tools_for_claude, requires_approval


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


def test_run_sql_query_documents_conversation_tables() -> None:
    run_sql_query_tool = next(tool for tool in get_tools_for_claude(in_workflow=True) if tool["name"] == "run_sql_query")
    description = run_sql_query_tool["description"]
    assert "- conversations:" in description
    assert "- chat_messages:" in description


def test_run_sql_query_allows_conversation_tables() -> None:
    tools_source = (Path(__file__).resolve().parents[1] / "agents" / "tools.py").read_text()
    assert '"conversations", "chat_messages"' in tools_source
