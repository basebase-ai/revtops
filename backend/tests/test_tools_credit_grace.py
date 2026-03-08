import asyncio

from agents import tools
from services import credits


def test_execute_tool_returns_credit_error_when_grace_exhausted(monkeypatch) -> None:
    async def _fake_deduct_with_grace(*args, **kwargs):
        return False, False

    async def _fake_should_skip_approval(*args, **kwargs) -> bool:
        return True

    monkeypatch.setattr(credits, "deduct_with_grace", _fake_deduct_with_grace)
    monkeypatch.setattr(tools, "_should_skip_approval", _fake_should_skip_approval)

    result = asyncio.run(
        tools.execute_tool(
            tool_name="list_connected_connectors",
            tool_input={},
            organization_id="00000000-0000-0000-0000-000000000001",
            user_id="00000000-0000-0000-0000-000000000002",
            context={},
        )
    )

    assert "out of credits" in result.get("error", "").lower()
    assert result.get("_out_of_credits_after_turn") is True


def test_execute_tool_marks_turn_for_credit_closeout_when_grace_used(monkeypatch) -> None:
    async def _fake_deduct_with_grace(*args, **kwargs):
        return True, True

    async def _fake_should_skip_approval(*args, **kwargs) -> bool:
        return True

    async def _fake_list_connected_connectors(_organization_id: str):
        return {"connectors": []}

    monkeypatch.setattr(credits, "deduct_with_grace", _fake_deduct_with_grace)
    monkeypatch.setattr(tools, "_should_skip_approval", _fake_should_skip_approval)
    monkeypatch.setattr(tools, "_list_connected_connectors", _fake_list_connected_connectors)

    result = asyncio.run(
        tools.execute_tool(
            tool_name="list_connected_connectors",
            tool_input={},
            organization_id="00000000-0000-0000-0000-000000000001",
            user_id="00000000-0000-0000-0000-000000000002",
            context={},
        )
    )

    assert result.get("connectors") == []
    assert result.get("_out_of_credits_after_turn") is True
