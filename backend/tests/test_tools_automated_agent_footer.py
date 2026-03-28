import asyncio
from typing import Any

from agents import tools


def test_ensure_automated_agent_footer_appends_once() -> None:
    signed = tools._ensure_automated_agent_footer("Hello there")
    assert "Done by an automated agent" in signed
    assert signed.startswith("Hello there")

    signed_again = tools._ensure_automated_agent_footer(signed)
    assert signed_again == signed


def test_ensure_automated_agent_footer_handles_empty() -> None:
    signed = tools._ensure_automated_agent_footer("")
    assert signed.startswith("— Done by an automated agent")


def test_execute_linear_create_adds_footer() -> None:
    captured: dict[str, Any] = {}

    class FakeLinearConnector:
        async def create_issue(self, **kwargs: Any) -> dict[str, Any]:
            captured.update(kwargs)
            return {"identifier": "ENG-1", "title": kwargs["title"]}

    record = {"team_key": "ENG", "title": "Need fix", "description": "Please investigate"}
    result = asyncio.run(tools._execute_linear_create(FakeLinearConnector(), record))

    assert result["identifier"] == "ENG-1"
    assert "Done by an automated agent" in captured["description"]


def test_handle_github_write_create_issue_adds_footer(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    class FakeGitHubConnector:
        def __init__(self, organization_id: str) -> None:
            self.organization_id = organization_id

        async def write(self, operation: str, data: dict[str, Any]) -> dict[str, Any]:
            captured["operation"] = operation
            captured["data"] = data
            return {"number": 1, "title": data.get("title", "")}

    monkeypatch.setattr("connectors.github.GitHubConnector", FakeGitHubConnector)

    result = asyncio.run(
        tools._handle_github_write(
            records=[
                {
                    "repo_full_name": "acme/repo",
                    "title": "Agent-created issue",
                    "body": "Issue details",
                }
            ],
            organization_id="00000000-0000-0000-0000-000000000001",
            operation="create",
        )
    )

    assert result["status"] == "completed"
    assert captured["operation"] == "create_issue"
    assert "Done by an automated agent" in captured["data"]["body"]
