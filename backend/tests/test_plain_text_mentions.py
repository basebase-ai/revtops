import pytest
from uuid import uuid4, UUID
from unittest.mock import AsyncMock, patch, MagicMock
from services.chat_messages import resolve_agent_responding

@pytest.mark.asyncio
async def test_resolve_agent_responding_text_fallback():
    """Verify that plain-text mentions work as fallback for agent and users."""
    conv_id = str(uuid4())
    org_id = str(uuid4())
    user_id = uuid4()
    
    # 1. Mock session
    mock_session = MagicMock()
    mock_session.execute = AsyncMock()
    mock_session.commit = AsyncMock()
    
    # 2. Mock conversation fetch results
    mock_conv_res_disabled = MagicMock()
    mock_conv_res_disabled.one_or_none.return_value = (False, [])
    
    mock_conv_res_enabled = MagicMock()
    mock_conv_res_enabled.one_or_none.return_value = (True, [])
    
    # 3. Mock user resolution fetch results
    mock_user_res_rafi = MagicMock()
    mock_user_res_rafi.all.return_value = [(user_id, "Rafi", "rafi@example.com")]

    # Track calls to conversations to alternate state
    conv_call_count = 0

    async def mock_execute(stmt):
        nonlocal conv_call_count
        stmt_str = str(stmt).lower()
        
        if "select" in stmt_str and "conversations" in stmt_str:
            if "update" in stmt_str:
                return MagicMock()
            conv_call_count += 1
            if conv_call_count == 1:
                return mock_conv_res_disabled
            return mock_conv_res_enabled
            
        if "select" in stmt_str and ("users" in stmt_str or "org_members" in stmt_str):
            return mock_user_res_rafi
            
        return MagicMock()

    mock_session.execute.side_effect = mock_execute

    with patch("services.chat_messages.get_session") as mock_get_session:
        mock_get_session.return_value.__aenter__.return_value = mock_session
        
        # Test Case 1: Plain text @basebase re-enables agent
        should_run, suggests = await resolve_agent_responding(
            conversation_id=conv_id,
            organization_id=org_id,
            mentions=[],
            message_text="Hey @basebase can you help?"
        )
        assert should_run is True
        assert suggests == []
        
        # Test Case 2: Plain text @rafi suggests participant AND disables agent
        # (Start with agent enabled, then mention @rafi)
        should_run, suggests = await resolve_agent_responding(
            conversation_id=conv_id,
            organization_id=org_id,
            mentions=[],
            message_text="Adding @rafi to the chat"
        )
        assert should_run is False 
        assert len(suggests) == 1
        assert suggests[0]["name"] == "Rafi"
