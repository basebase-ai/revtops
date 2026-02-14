import asyncio

from agents import tools


def test_write_to_system_of_record_routes_to_dispatcher(monkeypatch) -> None:
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

    async def _fake_write_to_system_of_record(
        params: dict[str, object],
        organization_id: str,
        user_id: str | None,
        skip_approval: bool,
        conversation_id: str | None = None,
    ) -> dict[str, object]:
        called["params"] = params
        called["organization_id"] = organization_id
        called["user_id"] = user_id
        called["skip_approval"] = skip_approval
        called["conversation_id"] = conversation_id
        return {"status": "completed", "message": "ok"}

    monkeypatch.setattr(tools, "_should_skip_approval", _fake_should_skip_approval)
    monkeypatch.setattr(tools, "_write_to_system_of_record", _fake_write_to_system_of_record)

    result = asyncio.run(
        tools.execute_tool(
            tool_name="write_to_system_of_record",
            tool_input={"target_system": "hubspot", "record_type": "contact", "operation": "create", "records": [{"email": "a@b.com"}]},
            organization_id="00000000-0000-0000-0000-000000000001",
            user_id="00000000-0000-0000-0000-000000000002",
            context={"conversation_id": "00000000-0000-0000-0000-000000000003"},
        )
    )

    assert result["status"] == "completed"
    assert called["skip_tool_name"] == "write_to_system_of_record"
    assert called["organization_id"] == "00000000-0000-0000-0000-000000000001"
    assert called["user_id"] == "00000000-0000-0000-0000-000000000002"
    assert called["skip_approval"] is True
    assert called["conversation_id"] == "00000000-0000-0000-0000-000000000003"
