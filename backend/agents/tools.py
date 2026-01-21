"""
Tool definitions and execution for Claude.

Tools:
- query_deals: Search/filter deals from database
- query_accounts: Search/filter accounts
- create_artifact: Save analysis/dashboard
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import any_, select

from models.account import Account
from models.artifact import Artifact
from models.database import get_session
from models.deal import Deal


def get_tools() -> list[dict[str, Any]]:
    """Return tool definitions for Claude."""
    return [
        {
            "name": "query_deals",
            "description": "Query deals from the database with filters. Returns list of deals matching criteria.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "stage": {
                        "type": "string",
                        "description": "Filter by deal stage (e.g., 'Negotiation', 'Closed Won')",
                    },
                    "owner_id": {
                        "type": "string",
                        "description": "Filter by deal owner UUID",
                    },
                    "min_amount": {
                        "type": "number",
                        "description": "Minimum deal amount",
                    },
                    "max_amount": {
                        "type": "number",
                        "description": "Maximum deal amount",
                    },
                    "close_date_before": {
                        "type": "string",
                        "description": "Close date before this date (ISO format YYYY-MM-DD)",
                    },
                    "close_date_after": {
                        "type": "string",
                        "description": "Close date after this date (ISO format YYYY-MM-DD)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results to return (default 50)",
                        "default": 50,
                    },
                },
            },
        },
        {
            "name": "query_accounts",
            "description": "Query accounts from the database with filters.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "industry": {
                        "type": "string",
                        "description": "Filter by industry",
                    },
                    "min_revenue": {
                        "type": "number",
                        "description": "Minimum annual revenue",
                    },
                    "min_employees": {
                        "type": "integer",
                        "description": "Minimum employee count",
                    },
                    "name_contains": {
                        "type": "string",
                        "description": "Filter by name containing this string",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results to return (default 50)",
                        "default": 50,
                    },
                },
            },
        },
        {
            "name": "create_artifact",
            "description": "Save an analysis, report, or dashboard for the user to view later.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["dashboard", "report", "analysis"],
                        "description": "Type of artifact to create",
                    },
                    "title": {
                        "type": "string",
                        "description": "Title of the artifact",
                    },
                    "description": {
                        "type": "string",
                        "description": "Description of what this artifact contains",
                    },
                    "data": {
                        "type": "object",
                        "description": "The analysis data/content",
                    },
                    "is_live": {
                        "type": "boolean",
                        "description": "Whether to refresh data on load (true) or keep static snapshot (false)",
                        "default": False,
                    },
                },
                "required": ["type", "title", "data"],
            },
        },
    ]


async def execute_tool(
    tool_name: str, tool_input: dict[str, Any], organization_id: str | None, user_id: str
) -> dict[str, Any]:
    """Execute a tool and return results."""
    
    # If no organization, tools that need org data won't work
    if organization_id is None:
        return {"error": "No organization associated with user. Please complete onboarding."}

    if tool_name == "query_deals":
        return await _query_deals(tool_input, organization_id, user_id)

    elif tool_name == "query_accounts":
        return await _query_accounts(tool_input, organization_id)

    elif tool_name == "create_artifact":
        return await _create_artifact(tool_input, organization_id, user_id)

    else:
        return {"error": f"Unknown tool: {tool_name}"}


async def _query_deals(
    filters: dict[str, Any], organization_id: str, user_id: str
) -> dict[str, Any]:
    """Query deals with filters."""
    async with get_session() as session:
        query = select(Deal).where(Deal.organization_id == UUID(organization_id))

        # Apply filters
        if "stage" in filters and filters["stage"]:
            query = query.where(Deal.stage == filters["stage"])

        if "owner_id" in filters and filters["owner_id"]:
            query = query.where(Deal.owner_id == UUID(filters["owner_id"]))

        if "min_amount" in filters and filters["min_amount"] is not None:
            query = query.where(Deal.amount >= filters["min_amount"])

        if "max_amount" in filters and filters["max_amount"] is not None:
            query = query.where(Deal.amount <= filters["max_amount"])

        if "close_date_before" in filters and filters["close_date_before"]:
            date = datetime.strptime(filters["close_date_before"], "%Y-%m-%d").date()
            query = query.where(Deal.close_date < date)

        if "close_date_after" in filters and filters["close_date_after"]:
            date = datetime.strptime(filters["close_date_after"], "%Y-%m-%d").date()
            query = query.where(Deal.close_date > date)

        # Permission filter: user can only see their deals or deals they have access to
        user_uuid = UUID(user_id)
        query = query.where(user_uuid == any_(Deal.visible_to_user_ids))

        # Limit
        limit = filters.get("limit", 50)
        query = query.limit(limit)

        result = await session.execute(query)
        deals = result.scalars().all()

        return {
            "count": len(deals),
            "deals": [deal.to_dict() for deal in deals],
        }


async def _query_accounts(
    filters: dict[str, Any], organization_id: str
) -> dict[str, Any]:
    """Query accounts with filters."""
    async with get_session() as session:
        query = select(Account).where(Account.organization_id == UUID(organization_id))

        # Apply filters
        if "industry" in filters and filters["industry"]:
            query = query.where(Account.industry == filters["industry"])

        if "min_revenue" in filters and filters["min_revenue"] is not None:
            query = query.where(Account.annual_revenue >= filters["min_revenue"])

        if "min_employees" in filters and filters["min_employees"] is not None:
            query = query.where(Account.employee_count >= filters["min_employees"])

        if "name_contains" in filters and filters["name_contains"]:
            query = query.where(Account.name.ilike(f"%{filters['name_contains']}%"))

        # Limit
        limit = filters.get("limit", 50)
        query = query.limit(limit)

        result = await session.execute(query)
        accounts = result.scalars().all()

        return {
            "count": len(accounts),
            "accounts": [account.to_dict() for account in accounts],
        }


async def _create_artifact(
    data: dict[str, Any], organization_id: str, user_id: str
) -> dict[str, Any]:
    """Save an artifact."""
    async with get_session() as session:
        artifact = Artifact(
            user_id=UUID(user_id),
            organization_id=UUID(organization_id),
            type=data["type"],
            title=data["title"],
            description=data.get("description"),
            snapshot_data=data["data"],
            is_live=data.get("is_live", False),
        )
        session.add(artifact)
        await session.commit()
        await session.refresh(artifact)

        return {
            "success": True,
            "artifact_id": str(artifact.id),
            "url": f"/artifacts/{artifact.id}",
        }
