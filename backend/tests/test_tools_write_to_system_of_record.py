import asyncio

from agents import tools
from services import credits


def test_write_on_connector_routes_to_dispatcher(monkeypatch) -> None:
    """write_on_connector is the generic connector write tool; test that it routes and approval is applied."""
    called: dict[str, object] = {}

    async def _fake_should_skip_approval(
        tool_name: str,
        user_id: str | None,
        context: dict[str, object] | None,
    ) -> bool:
        called["skip_tool_name"] = tool_name
        called["skip_user_id"] = user_id
        called["skip_context"] = context
        return True

    async def _fake_write_on_connector(
        params: dict[str, object],
        organization_id: str,
        user_id: str | None,
        skip_approval: bool,
        context: dict[str, object] | None,
    ) -> dict[str, object]:
        called["params"] = params
        called["organization_id"] = organization_id
        called["user_id"] = user_id
        called["skip_approval"] = skip_approval
        called["context"] = context
        return {"status": "created", "message": "ok"}

    async def _fake_deduct_with_grace(*args, **kwargs):
        return True, False

    monkeypatch.setattr(tools, "_should_skip_approval", _fake_should_skip_approval)
    monkeypatch.setattr(tools, "_write_on_connector", _fake_write_on_connector)
    monkeypatch.setattr(credits, "deduct_with_grace", _fake_deduct_with_grace)

    result = asyncio.run(
        tools.execute_tool(
            tool_name="write_on_connector",
            tool_input={
                "connector": "hubspot",
                "operation": "create_contact",
                "data": {"email": "a@b.com"},
            },
            organization_id="00000000-0000-0000-0000-000000000001",
            user_id="00000000-0000-0000-0000-000000000002",
            context={"conversation_id": "00000000-0000-0000-0000-000000000003"},
        )
    )

    assert result.get("status") == "created"
    assert called["skip_tool_name"] == "write_on_connector"
    assert called["organization_id"] == "00000000-0000-0000-0000-000000000001"
    assert called["user_id"] == "00000000-0000-0000-0000-000000000002"
    assert called["skip_approval"] is True
    assert called["context"] == {"conversation_id": "00000000-0000-0000-0000-000000000003"}
