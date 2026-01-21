"""
Reusable database query functions.
"""

from typing import Any, Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.account import Account
from models.deal import Deal


async def get_pipeline_summary(
    session: AsyncSession, organization_id: UUID, user_id: Optional[UUID] = None
) -> dict[str, Any]:
    """
    Get pipeline summary statistics.

    Args:
        session: Database session
        organization_id: Customer UUID to filter by
        user_id: Optional user UUID to filter by owner

    Returns:
        Dictionary with pipeline summary stats
    """
    query = select(
        Deal.stage,
        func.count(Deal.id).label("count"),
        func.sum(Deal.amount).label("total_amount"),
        func.avg(Deal.amount).label("avg_amount"),
    ).where(Deal.organization_id == organization_id)

    if user_id:
        query = query.where(Deal.owner_id == user_id)

    query = query.group_by(Deal.stage)

    result = await session.execute(query)
    rows = result.all()

    stages: dict[str, dict[str, Any]] = {}
    for row in rows:
        stages[row.stage or "Unknown"] = {
            "count": row.count,
            "total_amount": float(row.total_amount) if row.total_amount else 0,
            "avg_amount": float(row.avg_amount) if row.avg_amount else 0,
        }

    # Calculate totals
    total_count = sum(s["count"] for s in stages.values())
    total_amount = sum(s["total_amount"] for s in stages.values())

    return {
        "by_stage": stages,
        "total_deals": total_count,
        "total_pipeline_value": total_amount,
    }


async def get_deals_closing_soon(
    session: AsyncSession,
    organization_id: UUID,
    days: int = 30,
    user_id: Optional[UUID] = None,
) -> list[dict[str, Any]]:
    """
    Get deals closing within the specified number of days.

    Args:
        session: Database session
        organization_id: Customer UUID to filter by
        days: Number of days to look ahead (default 30)
        user_id: Optional user UUID to filter by owner

    Returns:
        List of deal dictionaries
    """
    from datetime import datetime, timedelta

    today = datetime.utcnow().date()
    end_date = today + timedelta(days=days)

    query = (
        select(Deal)
        .where(Deal.organization_id == organization_id)
        .where(Deal.close_date >= today)
        .where(Deal.close_date <= end_date)
        .order_by(Deal.close_date)
    )

    if user_id:
        query = query.where(Deal.owner_id == user_id)

    result = await session.execute(query)
    deals = result.scalars().all()

    return [deal.to_dict() for deal in deals]


async def get_top_accounts_by_deal_value(
    session: AsyncSession, organization_id: UUID, limit: int = 10
) -> list[dict[str, Any]]:
    """
    Get top accounts by total deal value.

    Args:
        session: Database session
        organization_id: Customer UUID to filter by
        limit: Maximum number of accounts to return

    Returns:
        List of account dictionaries with deal stats
    """
    query = (
        select(
            Account,
            func.count(Deal.id).label("deal_count"),
            func.sum(Deal.amount).label("total_deal_value"),
        )
        .join(Deal, Deal.account_id == Account.id, isouter=True)
        .where(Account.organization_id == organization_id)
        .group_by(Account.id)
        .order_by(func.sum(Deal.amount).desc().nullslast())
        .limit(limit)
    )

    result = await session.execute(query)
    rows = result.all()

    accounts: list[dict[str, Any]] = []
    for row in rows:
        account_dict = row.Account.to_dict()
        account_dict["deal_count"] = row.deal_count
        account_dict["total_deal_value"] = (
            float(row.total_deal_value) if row.total_deal_value else 0
        )
        accounts.append(account_dict)

    return accounts
