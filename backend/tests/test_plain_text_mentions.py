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
    
    # 2. Mock conversation fetch
    # We return a single row: (agent_responding, participating_user_ids)
    mock_conv_res = MagicMock()
    mock_conv_res.one_or_none.side_effect = [
        (False, []), # Call 1: agent disabled
        (True, []),  # Call 2: agent enabled
    ]
    
    # 3. Mock user resolution fetch
    mock_user_res = MagicMock()
    mock_user_res.all.return_value = [(user_id, "Rafi", "rafi@example.com")]
    mock_user_res.scalar_one_or_none.return_value = user_id

    async def mock_execute(stmt):
        stmt_str = str(stmt).lower()
        if "conversations" in stmt_str:
            return mock_conv_res
        if "users" in stmt_str or "org_members" in stmt_str:
            return mock_user_res
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
        
        # Test Case 2: Plain text @rafi suggests participant
        should_run, suggests = await resolve_agent_responding(
            conversation_id=conv_id,
            organization_id=org_id,
            mentions=[],
            message_text="Adding @rafi to the chat"
        )
        assert should_run is True 
        assert len(suggests) == 1
        assert suggests[0]["name"] == "Rafi"
