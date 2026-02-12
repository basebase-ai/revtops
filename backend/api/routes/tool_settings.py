"""
API routes for tool settings management.

Allows users to configure their approval preferences per tool,
similar to Cursor's "yolo mode" settings.
"""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from api.routes.auth import get_current_user
from models.database import get_session
from models.user import User
from models.user_tool_setting import UserToolSetting
from agents.registry import (
    get_all_tools,
    get_approval_required_tools,
    ToolCategory,
)

router = APIRouter(prefix="/tools", tags=["tools"])


class ToolInfo(BaseModel):
    """Information about a tool."""
    name: str
    description: str
    category: str
    default_requires_approval: bool
    user_auto_approve: bool | None = None  # User's current setting


class ToolSettingUpdate(BaseModel):
    """Request body for updating tool settings."""
    auto_approve: bool


class ToolSettingsResponse(BaseModel):
    """Response with all tool settings."""
    tools: list[ToolInfo]


@router.get("/registry", response_model=ToolSettingsResponse)
async def get_tool_registry(
    current_user: User = Depends(get_current_user),
) -> ToolSettingsResponse:
    """
    Get all available tools with their categories and current user settings.
    
    Returns tools organized by category with the user's auto_approve preferences.
    """
    # Get all tools from registry
    all_tools = get_all_tools()
    
    # Get user's current settings
    user_settings: dict[str, bool] = {}
    async with get_session() as session:
        result = await session.execute(
            select(UserToolSetting).where(
                UserToolSetting.user_id == current_user.id
            )
        )
        settings = result.scalars().all()
        for setting in settings:
            user_settings[setting.tool_name] = setting.auto_approve
    
    # Build response
    tools: list[ToolInfo] = []
    for tool in all_tools:
        tools.append(ToolInfo(
            name=tool.name,
            description=tool.description[:200] + "..." if len(tool.description) > 200 else tool.description,
            category=tool.category.value,
            default_requires_approval=tool.default_requires_approval,
            user_auto_approve=user_settings.get(tool.name),
        ))
    
    return ToolSettingsResponse(tools=tools)


@router.get("/settings")
async def get_user_tool_settings(
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """
    Get the current user's tool settings.
    
    Returns a dict of tool_name -> auto_approve for tools that have been configured.
    """
    async with get_session() as session:
        result = await session.execute(
            select(UserToolSetting).where(
                UserToolSetting.user_id == current_user.id
            )
        )
        settings = result.scalars().all()
    
    return {
        "settings": {s.tool_name: s.auto_approve for s in settings}
    }


@router.put("/settings/{tool_name}")
async def update_tool_setting(
    tool_name: str,
    update: ToolSettingUpdate,
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """
    Update the auto_approve setting for a specific tool.
    
    This controls whether the tool requires user approval before execution.
    Only approval-gated tools can have approval settings changed.
    """
    # Validate tool exists and requires approval by default
    all_tools = get_all_tools()
    tool = next((t for t in all_tools if t.name == tool_name), None)
    
    if not tool:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_name}' not found")
    
    if not tool.default_requires_approval:
        raise HTTPException(
            status_code=400, 
            detail=f"Tool '{tool_name}' does not require approval by default. "
                   "Only tools that require approval by default can have approval settings changed."
        )
    
    # Upsert the setting
    async with get_session() as session:
        stmt = insert(UserToolSetting).values(
            user_id=current_user.id,
            tool_name=tool_name,
            auto_approve=update.auto_approve,
        ).on_conflict_do_update(
            index_elements=["user_id", "tool_name"],
            set_={"auto_approve": update.auto_approve},
        )
        await session.execute(stmt)
        await session.commit()
    
    action = "enabled" if update.auto_approve else "disabled"
    return {
        "success": True,
        "message": f"Auto-approve {action} for {tool_name}",
        "tool_name": tool_name,
        "auto_approve": update.auto_approve,
    }


@router.get("/approval-required")
async def get_approval_required_tools_list(
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """
    Get list of tools that require approval by default.
    
    These are the tools that users can configure to auto-approve.
    """
    tools = get_approval_required_tools()
    
    # Get user's current settings
    async with get_session() as session:
        result = await session.execute(
            select(UserToolSetting).where(
                UserToolSetting.user_id == current_user.id
            )
        )
        settings = result.scalars().all()
        user_settings = {s.tool_name: s.auto_approve for s in settings}
    
    return {
        "tools": [
            {
                "name": t.name,
                "description": t.description[:200] + "..." if len(t.description) > 200 else t.description,
                "category": t.category.value,
                "user_auto_approve": user_settings.get(t.name, False),
            }
            for t in tools
        ]
    }


# Helper function to check if a tool is auto-approved for a user
async def is_tool_auto_approved(user_id: UUID, tool_name: str) -> bool:
    """
    Check if a tool is auto-approved for a specific user.
    
    Args:
        user_id: The user's UUID
        tool_name: The name of the tool
        
    Returns:
        True if auto-approved, False otherwise
    """
    async with get_session() as session:
        result = await session.execute(
            select(UserToolSetting).where(
                UserToolSetting.user_id == user_id,
                UserToolSetting.tool_name == tool_name,
            )
        )
        setting = result.scalar_one_or_none()
        
        if setting:
            return setting.auto_approve
        
        return False
