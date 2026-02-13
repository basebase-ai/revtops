import asyncio
from uuid import UUID

from workers.tasks.workflows import (
    apply_user_auto_approve_permissions,
    compute_effective_auto_approve_tools,
)


class _FakeScalarResult:
    def __init__(self, values: list[str]) -> None:
        self._values = values

    def all(self) -> list[str]:
        return self._values


class _FakeExecuteResult:
    def __init__(self, values: list[str]) -> None:
        self._values = values

    def scalars(self) -> _FakeScalarResult:
        return _FakeScalarResult(self._values)


class _FakeSession:
    def __init__(self, allowed_tools: list[str]) -> None:
        self.allowed_tools = allowed_tools

    async def execute(self, _query: object) -> _FakeExecuteResult:
        return _FakeExecuteResult(self.allowed_tools)


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


def test_apply_user_permissions_removes_restricted_tools_without_explicit_allow() -> None:
    session = _FakeSession(allowed_tools=[])

    async def _run() -> list[str]:
        return await apply_user_auto_approve_permissions(
            session=session,
            user_id=UUID("00000000-0000-0000-0000-000000000001"),
            auto_approve_tools=["save_memory", "send_slack", "github_issues_access"],
        )

    effective = asyncio.run(_run())
    assert effective == ["send_slack"]


def test_apply_user_permissions_keeps_explicitly_allowed_restricted_tools() -> None:
    session = _FakeSession(allowed_tools=["save_memory", "github_issues_access"])

    async def _run() -> list[str]:
        return await apply_user_auto_approve_permissions(
            session=session,
            user_id=UUID("00000000-0000-0000-0000-000000000001"),
            auto_approve_tools=["save_memory", "send_slack", "github_issues_access"],
        )

    effective = asyncio.run(_run())
    assert effective == ["save_memory", "send_slack", "github_issues_access"]
