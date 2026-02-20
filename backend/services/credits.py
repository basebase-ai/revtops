"""
Credit balance and metering for subscription tiers.

- get_balance / check_sufficient / deduct for usage tracking
- credits_for_tool maps tool names and context to credit cost
"""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.credit_transaction import CreditTransaction
from models.database import get_admin_session
from models.organization import Organization

logger = logging.getLogger(__name__)


ACTIVE_SUBSCRIPTION_STATUSES: frozenset[str] = frozenset({"active", "trialing"})


async def get_balance(organization_id: str) -> int:
    """Return current credits_balance for the organization."""
    async with get_admin_session() as session:
        result = await session.execute(
            select(Organization.credits_balance).where(
                Organization.id == UUID(organization_id)
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            return 0
        return int(row)


async def has_active_subscription(organization_id: str) -> bool:
    """Return True if the organization has an active or trialing subscription."""
    async with get_admin_session() as session:
        result = await session.execute(
            select(Organization.subscription_status).where(
                Organization.id == UUID(organization_id)
            )
        )
        status = result.scalar_one_or_none()
        return status in ACTIVE_SUBSCRIPTION_STATUSES


async def can_use_credits(organization_id: str) -> bool:
    """Return True if the org has an active subscription and at least one credit."""
    if not await has_active_subscription(organization_id):
        return False
    return await get_balance(organization_id) > 0


async def check_sufficient(organization_id: str, amount: int) -> bool:
    """Return True if the organization has at least `amount` credits."""
    if amount <= 0:
        return True
    balance = await get_balance(organization_id)
    return balance >= amount


async def deduct(
    organization_id: str,
    amount: int,
    reason: str,
    *,
    reference_type: str | None = None,
    reference_id: str | None = None,
    user_id: str | None = None,
    session: AsyncSession | None = None,
) -> bool:
    """
    Deduct credits from the organization. Appends a credit_transaction row.

    Returns True if deduction succeeded, False if insufficient balance.
    If session is provided, uses it and does not commit (caller commits).
    """
    if amount <= 0:
        return True

    async def _run(sess: AsyncSession) -> bool:
        result = await sess.execute(
            select(Organization).where(Organization.id == UUID(organization_id))
        )
        org: Organization | None = result.scalar_one_or_none()
        if org is None:
            logger.warning("[Credits] deduct: organization %s not found", organization_id)
            return False
        current = org.credits_balance
        if current < amount:
            logger.info(
                "[Credits] deduct: org %s insufficient balance %d < %d",
                organization_id, current, amount,
            )
            return False
        new_balance = current - amount
        await sess.execute(
            update(Organization)
            .where(Organization.id == UUID(organization_id))
            .values(credits_balance=new_balance)
        )
        tx = CreditTransaction(
            organization_id=UUID(organization_id),
            user_id=UUID(user_id) if user_id else None,
            amount=-amount,
            balance_after=new_balance,
            reason=reason[:64],
            reference_type=reference_type,
            reference_id=reference_id,
        )
        sess.add(tx)
        return True

    if session is not None:
        return await _run(session)
    async with get_admin_session() as sess:
        ok = await _run(sess)
        if ok:
            await sess.commit()
        return ok


def credits_for_tool(
    tool_name: str,
    tool_input: dict[str, Any],
    context: dict[str, Any] | None,
) -> int:
    """
    Map tool execution to credit cost (pricing doc: simple 1, cross-source 2-3,
    write-back 2, artifact 5-10, bulk ~1 per record, etc.).
    """
    # Simple read-only / list
    if tool_name in ("run_sql_query", "list_connected_systems"):
        return 1
    # CRM/system write-back
    if tool_name == "write_to_system":
        return 2
    # Query system often crosses sources
    if tool_name == "query_system":
        return 2
    # Artifacts / reports
    if tool_name == "create_artifact":
        return 5
    # Run action (enrichment, etc.)
    if tool_name == "run_action":
        return 3
    # Workflow run
    if tool_name == "run_workflow":
        return 3
    # Bulk: ~1 per item, cap at 50 for a single foreach
    if tool_name == "foreach":
        total = (
            (tool_input or {}).get("total_items")
            or (tool_input or {}).get("total")
            or 0
        )
        if isinstance(total, (int, float)):
            return min(max(1, int(total)), 50)
        return 5
    # Sync, create_app, keep_notes, manage_memory
    if tool_name in ("trigger_sync", "create_app", "keep_notes", "manage_memory"):
        return 1
    # run_sql_write
    if tool_name == "run_sql_write":
        return 2
    # Default for unknown tools
    return 1
