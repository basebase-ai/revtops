"""
Search routes for deals and accounts.

Provides unified search across CRM data with type-specific formatting.
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, or_

from models.database import get_session
from models.deal import Deal
from models.account import Account
from models.user import User

router = APIRouter()


# =============================================================================
# Response Models
# =============================================================================


class DealResult(BaseModel):
    """A deal search result."""
    
    type: str = "deal"
    id: str
    name: str
    amount: Optional[float]
    stage: Optional[str]
    close_date: Optional[str]
    account_name: Optional[str]
    owner_name: Optional[str]


class AccountResult(BaseModel):
    """An account search result."""
    
    type: str = "account"
    id: str
    name: str
    domain: Optional[str]
    industry: Optional[str]
    annual_revenue: Optional[float]
    deal_count: int


class SearchResponse(BaseModel):
    """Unified search response."""
    
    query: str
    deals: list[DealResult]
    accounts: list[AccountResult]
    total_deals: int
    total_accounts: int


# =============================================================================
# Search Endpoint
# =============================================================================


@router.get("", response_model=SearchResponse)
async def search(
    q: str,
    user_id: Optional[str] = None,
    organization_id: Optional[str] = None,
    limit: int = 10,
) -> SearchResponse:
    """
    Search deals and accounts by name.
    
    Uses case-insensitive ILIKE matching on name fields.
    Returns up to `limit` results per type.
    """
    # Validate inputs
    if not q or len(q.strip()) < 1:
        return SearchResponse(
            query=q,
            deals=[],
            accounts=[],
            total_deals=0,
            total_accounts=0,
        )
    
    # Get organization ID
    org_uuid: Optional[UUID] = None
    
    if organization_id:
        try:
            org_uuid = UUID(organization_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid organization ID")
    elif user_id:
        try:
            user_uuid = UUID(user_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid user ID")
        
        async with get_session() as session:
            user = await session.get(User, user_uuid)
            if not user or not user.organization_id:
                raise HTTPException(status_code=404, detail="User not found")
            org_uuid = user.organization_id
    else:
        raise HTTPException(status_code=400, detail="Either user_id or organization_id required")
    
    search_pattern = f"%{q.strip()}%"
    
    async with get_session() as session:
        # Search deals
        deals_query = (
            select(Deal)
            .where(
                Deal.organization_id == org_uuid,
                Deal.name.ilike(search_pattern),
            )
            .order_by(Deal.amount.desc().nullslast())
            .limit(limit)
        )
        deals_result = await session.execute(deals_query)
        deals = list(deals_result.scalars().all())
        
        # Get total count for deals
        deals_count_query = (
            select(Deal.id)
            .where(
                Deal.organization_id == org_uuid,
                Deal.name.ilike(search_pattern),
            )
        )
        deals_count_result = await session.execute(deals_count_query)
        total_deals = len(list(deals_count_result.scalars().all()))
        
        # Fetch related account names and owner names for deals
        deal_results: list[DealResult] = []
        for deal in deals:
            account_name: Optional[str] = None
            owner_name: Optional[str] = None
            
            if deal.account_id:
                account = await session.get(Account, deal.account_id)
                if account:
                    account_name = account.name
            
            if deal.owner_id:
                owner = await session.get(User, deal.owner_id)
                if owner:
                    owner_name = owner.name or owner.email
            
            deal_results.append(DealResult(
                id=str(deal.id),
                name=deal.name,
                amount=float(deal.amount) if deal.amount else None,
                stage=deal.stage,
                close_date=deal.close_date.isoformat() if deal.close_date else None,
                account_name=account_name,
                owner_name=owner_name,
            ))
        
        # Search accounts
        accounts_query = (
            select(Account)
            .where(
                Account.organization_id == org_uuid,
                or_(
                    Account.name.ilike(search_pattern),
                    Account.domain.ilike(search_pattern),
                ),
            )
            .order_by(Account.annual_revenue.desc().nullslast())
            .limit(limit)
        )
        accounts_result = await session.execute(accounts_query)
        accounts = list(accounts_result.scalars().all())
        
        # Get total count for accounts
        accounts_count_query = (
            select(Account.id)
            .where(
                Account.organization_id == org_uuid,
                or_(
                    Account.name.ilike(search_pattern),
                    Account.domain.ilike(search_pattern),
                ),
            )
        )
        accounts_count_result = await session.execute(accounts_count_query)
        total_accounts = len(list(accounts_count_result.scalars().all()))
        
        # Count deals per account
        account_results: list[AccountResult] = []
        for account in accounts:
            # Count deals for this account
            deal_count_query = (
                select(Deal.id)
                .where(Deal.account_id == account.id)
            )
            deal_count_result = await session.execute(deal_count_query)
            deal_count = len(list(deal_count_result.scalars().all()))
            
            account_results.append(AccountResult(
                id=str(account.id),
                name=account.name,
                domain=account.domain,
                industry=account.industry,
                annual_revenue=float(account.annual_revenue) if account.annual_revenue else None,
                deal_count=deal_count,
            ))
        
        return SearchResponse(
            query=q,
            deals=deal_results,
            accounts=account_results,
            total_deals=total_deals,
            total_accounts=total_accounts,
        )
