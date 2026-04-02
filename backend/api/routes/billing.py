"""
Billing and subscription API (Stripe).

- GET /status — current org subscription and credits (incl. cancel_at_period_end)
- POST /setup-intent — create SetupIntent for card collection
- POST /subscribe — create subscription with payment_method_id and tier
- PATCH /subscription — change plan (tier)
- POST /cancel — schedule cancel at period end
- GET /plans — list plans (tier, name, price, credits)
- POST /webhook — Stripe webhook (raw body, signature verification)
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from api.auth_middleware import AuthContext, require_organization
from config import settings
from models.database import get_admin_session
from models.org_member import OrgMember
from models.organization import Organization
from services.credits import ACTIVE_SUBSCRIPTION_STATUSES

logger = logging.getLogger(__name__)

router = APIRouter()


async def _is_org_admin(*, user_id: UUID, organization_id: UUID) -> bool:
    """Return True when the user is an active organization admin."""
    async with get_admin_session() as session:
        membership = (
            await session.execute(
                select(OrgMember).where(
                    OrgMember.user_id == user_id,
                    OrgMember.organization_id == organization_id,
                    OrgMember.status.in_(("active", "onboarding", "invited")),
                )
            )
        ).scalar_one_or_none()
    return bool(membership and membership.role == "admin")


def _norm_credit_ref_id(value: Any) -> Optional[str]:
    """Canonical UUID string for conversation reference_id (stable dict keys across DB drivers)."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        return str(UUID(s))
    except (ValueError, TypeError):
        return s


def _norm_user_id_str(value: Any) -> Optional[str]:
    """Canonical UUID string for credit transaction user_id."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        return str(UUID(s))
    except (ValueError, TypeError):
        return s

# Credit transactions returned for charts / history (deductions + grants such as renewals).
CREDIT_HISTORY_LOOKBACK_DAYS = 365

# Tier config: name, price_cents, credits_included
# "free" tier requires no credit card; "partner" tier is hidden and assigned manually
PLANS: dict[str, dict[str, Any]] = {
    "free": {"name": "Free", "price_cents": 0, "credits_included": 100},
    "pro": {"name": "Pro", "price_cents": 10000, "credits_included": 500},
    "business": {"name": "Business", "price_cents": 25000, "credits_included": 2500},
    "scale": {"name": "Scale", "price_cents": 60000, "credits_included": 8000},
    "partner": {"name": "Partner", "price_cents": 0, "credits_included": 2000, "hidden": True},
}

# Rollover cap multiplier per tier (e.g. Pro: unused credits up to 2x included)
ROLLOVER_CAP: dict[str, int] = {
    "free": 0,
    "pro": 2,
    "business": 2,
    "scale": 3,
    "partner": 3,
}

# Stripe Price IDs (Dashboard → Products → [your product] → Pricing → copy "Price ID", e.g. price_1ABC...)
# Subscription.create requires price IDs, not product IDs (prod_xxx).
# These are selected based on STRIPE_SECRET_KEY prefix (sk_live_ vs sk_test_)
# Note: "free" tier doesn't use Stripe, so no price ID needed
STRIPE_PRICE_IDS_TEST: dict[str, str] = {
    "pro": "price_1T2zG6BB0TvgbMzRkCwxwTKm",
    "business": "price_1T2zGkBB0TvgbMzRYy2b7Y0r",
    "scale": "price_1T2zH1BB0TvgbMzRmJF4RglP",
}
STRIPE_PRICE_IDS_LIVE: dict[str, str] = {
    "pro": "price_1T31ohP5SO7X9dBUQ1noH603",
    "business": "price_1T31oiP5SO7X9dBUeVPJdaiW",
    "scale": "price_1T31oiP5SO7X9dBUkbZiwevH",
}


def _get_stripe_price_ids() -> dict[str, str]:
    """Return the appropriate price IDs based on whether we're in live or test mode."""
    key = settings.STRIPE_SECRET_KEY or ""
    if key.startswith("sk_live_"):
        return STRIPE_PRICE_IDS_LIVE
    return STRIPE_PRICE_IDS_TEST


def _stripe_price_id_for_tier(tier: str) -> Optional[str]:
    price_ids = _get_stripe_price_ids()
    pid = price_ids.get(tier)
    return pid if pid and pid.strip() else None


# --- Response/request models ---


class BillingStatusResponse(BaseModel):
    subscription_tier: Optional[str] = None
    subscription_status: Optional[str] = None
    credits_balance: int = 0
    credits_included: int = 0
    current_period_end: Optional[str] = None
    cancel_at_period_end: Optional[str] = None  # ISO date when access ends, if cancel scheduled
    cancel_scheduled: bool = False  # True when Stripe has cancel_at_period_end (even if no date yet)
    subscription_required: bool = True


class SetupIntentResponse(BaseModel):
    client_secret: str


class SubscribeRequest(BaseModel):
    payment_method_id: Optional[str] = Field(None, min_length=1)
    tier: str = Field(..., pattern="^(free|pro|business|scale)$")


class ChangePlanRequest(BaseModel):
    tier: str = Field(..., pattern="^(free|pro|business|scale)$")


class PlanItem(BaseModel):
    tier: str
    name: str
    price_cents: int
    credits_included: int
    stripe_product_id: Optional[str] = None


class PlansResponse(BaseModel):
    plans: list[PlanItem]


class CreditTransactionItem(BaseModel):
    timestamp: str
    amount: int
    balance_after: int
    reason: str
    user_email: Optional[str] = None


class UserUsageItem(BaseModel):
    user_id: str
    user_email: str
    user_name: Optional[str] = None
    total_credits_used: int


class ConversationUserSlice(BaseModel):
    """Credits consumed in this conversation attributed to one user."""

    user_id: str
    total_credits_used: int


class ConversationUsageItem(BaseModel):
    conversation_id: str
    title: Optional[str] = None
    # Attributed debits (known user_id); matches team member / by_user sums.
    total_credits_used: int
    # Orphan debits (user_id NULL, e.g. after member removed); excluded from team totals.
    unattributed_credits_used: int = 0
    last_used_at: Optional[str] = None
    by_user: list[ConversationUserSlice] = []


class CreditDetailsResponse(BaseModel):
    transactions: list[CreditTransactionItem]
    usage_by_user: list[UserUsageItem]
    usage_by_conversation: list[ConversationUsageItem] = []
    # Sum of orphan chat debits in period (same as sum of per-conversation unattributed).
    unattributed_credits_used: int = 0
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    starting_balance: int = 0


# --- Endpoints ---


@router.get("/status", response_model=BillingStatusResponse)
async def get_billing_status(
    auth: AuthContext = Depends(require_organization),
) -> BillingStatusResponse:
    """Return current org subscription and credit balance."""
    org_id = auth.organization_id
    if not org_id:
        raise HTTPException(status_code=403, detail="Organization required")
    async with get_admin_session() as session:
        from sqlalchemy import select
        result = await session.execute(
            select(Organization).where(Organization.id == org_id)
        )
        org: Organization | None = result.scalar_one_or_none()
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")
        status_ok = org.subscription_status in ACTIVE_SUBSCRIPTION_STATUSES
        cancel_at_period_end: Optional[str] = None
        cancel_scheduled = False
        if org.stripe_subscription_id and settings.STRIPE_SECRET_KEY:
            import stripe
            stripe.api_key = settings.STRIPE_SECRET_KEY
            try:
                sub = stripe.Subscription.retrieve(org.stripe_subscription_id)
                if sub.cancel_at_period_end:
                    cancel_scheduled = True
                    if sub.current_period_end:
                        cancel_at_period_end = datetime.fromtimestamp(
                            sub.current_period_end, tz=timezone.utc
                        ).isoformat().replace("+00:00", "Z")
                    elif org.current_period_end:
                        cancel_at_period_end = org.current_period_end.isoformat().replace(
                            "+00:00", "Z"
                        )
            except Exception:
                pass
        return BillingStatusResponse(
            subscription_tier=org.subscription_tier,
            subscription_status=org.subscription_status,
            credits_balance=org.credits_balance,
            credits_included=org.credits_included,
            current_period_end=(
                org.current_period_end.isoformat().replace("+00:00", "Z")
                if org.current_period_end else None
            ),
            cancel_at_period_end=cancel_at_period_end,
            cancel_scheduled=cancel_scheduled,
            subscription_required=not status_ok,
        )


@router.post("/setup-intent", response_model=SetupIntentResponse)
async def create_setup_intent(
    auth: AuthContext = Depends(require_organization),
) -> SetupIntentResponse:
    """Create a Stripe SetupIntent for the org's customer (or create customer first)."""
    if not settings.STRIPE_SECRET_KEY:
        raise HTTPException(status_code=503, detail="Billing is not configured")
    org_id = auth.organization_id
    if not org_id:
        raise HTTPException(status_code=403, detail="Organization required")
    import stripe
    stripe.api_key = settings.STRIPE_SECRET_KEY
    try:
        async with get_admin_session() as session:
            from sqlalchemy import select
            result = await session.execute(
                select(Organization).where(Organization.id == org_id)
            )
            org = result.scalar_one_or_none()
            if not org:
                raise HTTPException(status_code=404, detail="Organization not found")
            customer_id: Optional[str] = org.stripe_customer_id
            if not customer_id:
                cust = stripe.Customer.create(
                    email=auth.email,
                    name=org.name,
                    metadata={"organization_id": str(org_id)},
                )
                customer_id = cust.id
                org.stripe_customer_id = customer_id
                await session.commit()
            intent = stripe.SetupIntent.create(
                customer=customer_id,
                usage="off_session",
                metadata={"organization_id": str(org_id)},
            )
            return SetupIntentResponse(client_secret=intent.client_secret or "")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail="Billing is temporarily unavailable. Please try again or contact support.",
        ) from e


@router.post("/subscribe")
async def subscribe(
    body: SubscribeRequest,
    auth: AuthContext = Depends(require_organization),
) -> dict[str, str]:
    """Create or update subscription with the given payment method and tier."""
    org_id = auth.organization_id
    if not org_id:
        raise HTTPException(status_code=403, detail="Organization required")

    plan = PLANS.get(body.tier)
    if not plan:
        raise HTTPException(status_code=400, detail=f"Unknown tier: {body.tier}")

    # Free tier: no Stripe interaction needed
    is_free_tier = plan.get("price_cents", 0) == 0

    if is_free_tier:
        async with get_admin_session() as session:
            from sqlalchemy import select
            result = await session.execute(
                select(Organization).where(Organization.id == org_id)
            )
            org = result.scalar_one_or_none()
            if not org:
                raise HTTPException(status_code=404, detail="Organization not found")

            now = datetime.now(timezone.utc)
            org.subscription_tier = body.tier
            org.subscription_status = "active"
            org.credits_included = plan.get("credits_included", 100)
            org.credits_balance = org.credits_included
            org.current_period_start = now
            org.current_period_end = now + timedelta(days=30)
            await session.commit()
            return {"status": "ok", "subscription_id": "free"}

    # Paid tier: require payment method and Stripe
    if not body.payment_method_id:
        raise HTTPException(
            status_code=400,
            detail="Payment method required for paid plans",
        )
    if not settings.STRIPE_SECRET_KEY:
        raise HTTPException(status_code=503, detail="Billing is not configured")
    price_id = _stripe_price_id_for_tier(body.tier)
    if not price_id:
        raise HTTPException(
            status_code=400,
            detail=f"Stripe price not configured for tier {body.tier}",
        )

    import stripe
    stripe.api_key = settings.STRIPE_SECRET_KEY
    async with get_admin_session() as session:
        from sqlalchemy import select
        result = await session.execute(
            select(Organization).where(Organization.id == org_id)
        )
        org = result.scalar_one_or_none()
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")
        customer_id: Optional[str] = org.stripe_customer_id
        if not customer_id:
            cust = stripe.Customer.create(
                email=auth.email,
                name=org.name,
                metadata={"organization_id": str(org_id)},
            )
            customer_id = cust.id
            org.stripe_customer_id = customer_id
            await session.flush()
        stripe.PaymentMethod.attach(
            body.payment_method_id,
            customer=customer_id,
        )
        stripe.Customer.modify(
            customer_id,
            invoice_settings={"default_payment_method": body.payment_method_id},
        )
        # Create subscription
        sub = stripe.Subscription.create(
            customer=customer_id,
            items=[{"price": price_id}],
            payment_behavior="default_incomplete",
            expand=["latest_invoice"],
            metadata={"organization_id": str(org_id), "tier": body.tier},
        )
        org.stripe_subscription_id = sub.id
        org.subscription_tier = body.tier
        org.credits_included = plan.get("credits_included", 100)

        # If subscription is incomplete, try to pay the first invoice immediately
        # so the user gets credits right away instead of waiting for webhook
        if sub.status == "incomplete" and sub.latest_invoice:
            invoice_id = (
                sub.latest_invoice.id
                if hasattr(sub.latest_invoice, "id")
                else sub.latest_invoice
            )
            try:
                paid_invoice = stripe.Invoice.pay(invoice_id)
                if paid_invoice.status == "paid":
                    # Re-fetch subscription to get updated status
                    sub = stripe.Subscription.retrieve(sub.id)
            except stripe.error.CardError as e:
                # Payment failed — let the user know
                raise HTTPException(
                    status_code=402,
                    detail=f"Payment failed: {e.user_message or 'Card declined'}",
                )

        org.subscription_status = sub.status or "active"
        if sub.status == "active":
            org.credits_balance = org.credits_included
            period_end = getattr(sub, "current_period_end", None)
            period_start = getattr(sub, "current_period_start", None)
            if period_end:
                org.current_period_end = datetime.fromtimestamp(
                    period_end, tz=timezone.utc
                )
            if period_start:
                org.current_period_start = datetime.fromtimestamp(
                    period_start, tz=timezone.utc
                )
        await session.commit()
        return {"status": "ok", "subscription_id": sub.id}


@router.patch("/subscription")
async def change_plan(
    body: ChangePlanRequest,
    auth: AuthContext = Depends(require_organization),
) -> dict[str, str]:
    """Change subscription to a different tier (upgrade/downgrade)."""
    if not settings.STRIPE_SECRET_KEY:
        raise HTTPException(status_code=503, detail="Billing is not configured")
    price_id = _stripe_price_id_for_tier(body.tier)
    if not price_id:
        raise HTTPException(
            status_code=400,
            detail=f"Stripe price not configured for tier {body.tier}",
        )
    org_id = auth.organization_id
    if not org_id:
        raise HTTPException(status_code=403, detail="Organization required")
    import stripe
    stripe.api_key = settings.STRIPE_SECRET_KEY
    async with get_admin_session() as session:
        from sqlalchemy import select
        result = await session.execute(
            select(Organization).where(Organization.id == org_id)
        )
        org: Organization | None = result.scalar_one_or_none()
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")
        sub_id: Optional[str] = org.stripe_subscription_id
        if not sub_id:
            raise HTTPException(
                status_code=400,
                detail="No active subscription to change",
            )
        sub = stripe.Subscription.retrieve(sub_id, expand=["items.data.price"])
        items = list(sub.get("items", {}).get("data", []) or [])
        if not items:
            raise HTTPException(
                status_code=400,
                detail="Subscription has no items",
            )
        item_id: str = items[0]["id"]
        stripe.Subscription.modify(
            sub_id,
            items=[{"id": item_id, "price": price_id}],
            metadata={"organization_id": str(org_id), "tier": body.tier},
            proration_behavior="create_prorations",
        )
        plan = PLANS.get(body.tier, {})
        org.subscription_tier = body.tier
        org.credits_included = plan.get("credits_included", org.credits_included)
        await session.commit()
        return {"status": "ok"}


@router.post("/cancel")
async def cancel_subscription(
    auth: AuthContext = Depends(require_organization),
) -> dict[str, str]:
    """Schedule subscription to cancel at period end (keeps access until then)."""
    if not settings.STRIPE_SECRET_KEY:
        raise HTTPException(status_code=503, detail="Billing is not configured")
    org_id = auth.organization_id
    if not org_id:
        raise HTTPException(status_code=403, detail="Organization required")
    async with get_admin_session() as session:
        from sqlalchemy import select
        result = await session.execute(
            select(Organization).where(Organization.id == org_id)
        )
        org: Organization | None = result.scalar_one_or_none()
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")
        sub_id: Optional[str] = org.stripe_subscription_id
        if not sub_id:
            raise HTTPException(
                status_code=400,
                detail="No active subscription to cancel",
            )
    import stripe
    stripe.api_key = settings.STRIPE_SECRET_KEY
    stripe.Subscription.modify(sub_id, cancel_at_period_end=True)
    return {"status": "ok"}


@router.get("/credit-details", response_model=CreditDetailsResponse)
async def get_credit_details(
    auth: AuthContext = Depends(require_organization),
) -> CreditDetailsResponse:
    """Return credit transactions (rolling history) and usage for the current billing period."""
    org_id = auth.organization_id
    if not org_id:
        raise HTTPException(status_code=403, detail="Organization required")

    is_org_admin = auth.is_global_admin or await _is_org_admin(
        user_id=auth.user_id,
        organization_id=org_id,
    )
    if not is_org_admin:
        logger.info(
            "Scoping credit transaction details to requesting user org=%s user=%s",
            org_id,
            auth.user_id,
        )

    async with get_admin_session() as session:
        from sqlalchemy import select, func
        from models.credit_transaction import CreditTransaction
        from models.user import User
        from models.conversation import Conversation
        from models.database import get_session
        
        # Get org with period info
        result = await session.execute(
            select(Organization).where(Organization.id == org_id)
        )
        org: Organization | None = result.scalar_one_or_none()
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")
        
        period_start = org.current_period_start
        period_end = org.current_period_end
        now = datetime.now(timezone.utc)
        history_cutoff = now - timedelta(days=CREDIT_HISTORY_LOOKBACK_DAYS)

        def build_transactions_query(filter_start: Optional[datetime]) -> Any:
            q = (
                select(CreditTransaction, User.email, User.name)
                .outerjoin(User, CreditTransaction.user_id == User.id)
                .where(CreditTransaction.organization_id == org_id)
                .order_by(CreditTransaction.created_at.asc())
            )
            if not is_org_admin:
                q = q.where(CreditTransaction.user_id == auth.user_id)
            if filter_start:
                q = q.where(CreditTransaction.created_at >= filter_start)
            return q

        def build_usage_query(filter_start: Optional[datetime]) -> Any:
            """Per-user totals for chat-attributed usage only (matches Usage by Chat / filter)."""
            q = (
                select(
                    CreditTransaction.user_id,
                    User.email,
                    User.name,
                    func.sum(func.abs(CreditTransaction.amount)).label("total_used"),
                )
                .outerjoin(User, CreditTransaction.user_id == User.id)
                .where(CreditTransaction.organization_id == org_id)
                .where(CreditTransaction.amount < 0)
                .where(CreditTransaction.reference_type == "conversation")
                .where(CreditTransaction.reference_id.isnot(None))
                .where(CreditTransaction.user_id.isnot(None))
                .group_by(CreditTransaction.user_id, User.email, User.name)
                .order_by(func.sum(func.abs(CreditTransaction.amount)).desc())
            )
            if not is_org_admin:
                q = q.where(CreditTransaction.user_id == auth.user_id)
            if filter_start:
                q = q.where(CreditTransaction.created_at >= filter_start)
            return q

        # No ledger rows in current billing period → align usage with all-time (same as main)
        period_check = await session.execute(build_transactions_query(period_start))
        period_tx_nonempty = bool(period_check.all())
        effective_period_start = period_start
        if not period_tx_nonempty and period_start:
            effective_period_start = None

        # Ledger for charts: rolling window in normal mode; full history when usage falls back to all-time
        tx_window = history_cutoff if effective_period_start is not None else None
        tx_result = await session.execute(build_transactions_query(tx_window))
        rows = tx_result.all()
        
        transactions: list[CreditTransactionItem] = []
        for tx, user_email, user_name in rows:
            transactions.append(CreditTransactionItem(
                timestamp=tx.created_at.isoformat().replace("+00:00", "Z"),
                amount=tx.amount,
                balance_after=tx.balance_after,
                reason=tx.reason,
                user_email=user_email,
            ))
        
        # Balance immediately before the first transaction in the returned window
        starting_balance = org.credits_included
        if transactions and effective_period_start:
            first_tx = transactions[0]
            starting_balance = first_tx.balance_after - first_tx.amount

        # Usage tables: current period, or all-time when the period has no ledger activity
        usage_result = await session.execute(build_usage_query(effective_period_start))
        usage_rows = usage_result.all()
        
        usage_by_user: list[UserUsageItem] = []
        for user_id, email, name, total_used in usage_rows:
            uid_key = _norm_user_id_str(user_id) if user_id else None
            usage_by_user.append(
                UserUsageItem(
                    user_id=uid_key if uid_key else "unknown",
                    user_email=email or "Unknown user",
                    user_name=name,
                    total_credits_used=int(total_used) if total_used else 0,
                )
            )

        # Aggregate usage by conversation (per-chat credit consumption).
        # Same attribution rules as build_usage_query / conv_user_query so per-chat totals
        # match teammate rows and by_user slices (excludes orphaned debits after user_id NULL).
        conv_usage_rows: list[tuple[str, int, datetime]] = []
        conv_usage_query = (
            select(
                CreditTransaction.reference_id,
                func.sum(func.abs(CreditTransaction.amount)).label("total_used"),
                func.max(CreditTransaction.created_at).label("last_used_at"),
            )
            .where(CreditTransaction.organization_id == org_id)
            .where(CreditTransaction.reference_type == "conversation")
            .where(CreditTransaction.amount < 0)
            .where(CreditTransaction.reference_id.isnot(None))
            .where(CreditTransaction.user_id.isnot(None))
        )
        if not is_org_admin:
            conv_usage_query = conv_usage_query.where(
                CreditTransaction.user_id == auth.user_id
            )
        if effective_period_start:
            conv_usage_query = conv_usage_query.where(
                CreditTransaction.created_at >= effective_period_start
            )
        conv_usage_result = await session.execute(
            conv_usage_query.group_by(CreditTransaction.reference_id)
        )
        conv_usage_rows = conv_usage_result.all()

        # Orphan conversation debits (user_id NULL) — same period; surfaced as "Former user" in UI
        conv_orphan_query = (
            select(
                CreditTransaction.reference_id,
                func.sum(func.abs(CreditTransaction.amount)).label("total_used"),
                func.max(CreditTransaction.created_at).label("last_used_at"),
            )
            .where(CreditTransaction.organization_id == org_id)
            .where(CreditTransaction.reference_type == "conversation")
            .where(CreditTransaction.amount < 0)
            .where(CreditTransaction.reference_id.isnot(None))
            .where(CreditTransaction.user_id.is_(None))
        )
        if not is_org_admin:
            conv_orphan_query = conv_orphan_query.where(
                CreditTransaction.user_id == auth.user_id
            )
        if effective_period_start:
            conv_orphan_query = conv_orphan_query.where(
                CreditTransaction.created_at >= effective_period_start
            )
        conv_orphan_result = await session.execute(
            conv_orphan_query.group_by(CreditTransaction.reference_id)
        )
        orphan_by_ref: dict[str, tuple[int, Optional[datetime]]] = {}
        for ref_id, total_used, last_used_at in conv_orphan_result.all():
            if not ref_id:
                continue
            ref_key = _norm_credit_ref_id(ref_id) or str(ref_id).strip()
            orphan_by_ref[ref_key] = (
                int(total_used) if total_used else 0,
                last_used_at,
            )
        unattributed_total = sum(t[0] for t in orphan_by_ref.values())

        # Per-(conversation, user) slices so the UI can filter chats by teammate
        conv_user_query = (
            select(
                CreditTransaction.reference_id,
                CreditTransaction.user_id,
                func.sum(func.abs(CreditTransaction.amount)).label("total_used"),
            )
            .where(CreditTransaction.organization_id == org_id)
            .where(CreditTransaction.reference_type == "conversation")
            .where(CreditTransaction.amount < 0)
            .where(CreditTransaction.reference_id.isnot(None))
            .where(CreditTransaction.user_id.isnot(None))
        )
        if not is_org_admin:
            conv_user_query = conv_user_query.where(
                CreditTransaction.user_id == auth.user_id
            )
        if effective_period_start:
            conv_user_query = conv_user_query.where(
                CreditTransaction.created_at >= effective_period_start
            )
        conv_user_result = await session.execute(
            conv_user_query.group_by(
                CreditTransaction.reference_id, CreditTransaction.user_id
            )
        )
        conv_id_to_slices: dict[str, list[ConversationUserSlice]] = {}
        for ref_id, uid, total_used in conv_user_result.all():
            ref_key = _norm_credit_ref_id(ref_id)
            uid_key = _norm_user_id_str(uid)
            if not ref_key or not uid_key:
                continue
            conv_id_to_slices.setdefault(ref_key, []).append(
                ConversationUserSlice(
                    user_id=uid_key,
                    total_credits_used=int(total_used) if total_used else 0,
                )
            )

        # Resolve conversation titles from the org-scoped database
        usage_by_conversation: list[ConversationUsageItem] = []
        if conv_usage_rows or orphan_by_ref:
            conv_ids_for_titles: set[str] = set()
            for ref_id, _tu, _lu in conv_usage_rows:
                if ref_id:
                    conv_ids_for_titles.add(
                        _norm_credit_ref_id(ref_id) or str(ref_id).strip()
                    )
            conv_ids_for_titles.update(orphan_by_ref.keys())
            conv_id_strs = list(conv_ids_for_titles)

            id_to_title: dict[str, Optional[str]] = {}
            if conv_id_strs:
                # Best-effort: some IDs may be invalid UUIDs; skip those
                valid_conv_ids: list[UUID] = []
                for cid in conv_id_strs:
                    try:
                        valid_conv_ids.append(UUID(cid))
                    except Exception:
                        continue

                if valid_conv_ids:
                    async with get_session(organization_id=str(org_id)) as org_session:
                        conv_result = await org_session.execute(
                            select(Conversation.id, Conversation.title).where(
                                Conversation.id.in_(valid_conv_ids)
                            )
                        )
                        for conv_id, title in conv_result.all():
                            id_to_title[str(conv_id)] = title

            # Copy so we can pop orphan-only keys remaining after attributed merge
            orphan_remaining = dict(orphan_by_ref)

            for ref_id, total_used, last_used_at in conv_usage_rows:
                if not ref_id:
                    continue
                ref_key = _norm_credit_ref_id(ref_id) or str(ref_id).strip()
                o_amt, o_last = orphan_remaining.pop(ref_key, (0, None))
                merged_last = last_used_at
                if o_last is not None:
                    if merged_last is None or o_last > merged_last:
                        merged_last = o_last
                title = id_to_title.get(ref_key) or id_to_title.get(str(ref_id))
                slices = conv_id_to_slices.get(ref_key, [])
                usage_by_conversation.append(
                    ConversationUsageItem(
                        conversation_id=ref_key,
                        title=title,
                        total_credits_used=int(total_used) if total_used else 0,
                        unattributed_credits_used=o_amt,
                        last_used_at=merged_last.isoformat().replace("+00:00", "Z")
                        if merged_last
                        else None,
                        by_user=slices,
                    )
                )

            # Conversations with only orphan debits (no attributed rows this period)
            for ref_key, (o_amt, o_last) in orphan_remaining.items():
                if o_amt <= 0:
                    continue
                title = id_to_title.get(ref_key) or id_to_title.get(str(ref_key))
                usage_by_conversation.append(
                    ConversationUsageItem(
                        conversation_id=ref_key,
                        title=title,
                        total_credits_used=0,
                        unattributed_credits_used=o_amt,
                        last_used_at=o_last.isoformat().replace("+00:00", "Z")
                        if o_last
                        else None,
                        by_user=[],
                    )
                )

            # Newest last activity first (reverse chronological)
            usage_by_conversation.sort(
                key=lambda item: item.last_used_at or "",
                reverse=True,
            )

        return CreditDetailsResponse(
            transactions=transactions,
            usage_by_user=usage_by_user,
            usage_by_conversation=usage_by_conversation,
            unattributed_credits_used=unattributed_total,
            period_start=effective_period_start.isoformat().replace("+00:00", "Z")
            if effective_period_start
            else None,
            period_end=period_end.isoformat().replace("+00:00", "Z")
            if period_end and effective_period_start
            else None,
            starting_balance=starting_balance,
        )


@router.get("/plans", response_model=PlansResponse)
async def list_plans() -> PlansResponse:
    """Return available plans for the plan selector (excludes hidden tiers like 'partner')."""
    plans_list = [
        PlanItem(
            tier=tier,
            name=info["name"],
            price_cents=info["price_cents"],
            credits_included=info["credits_included"],
            stripe_product_id=_stripe_price_id_for_tier(tier),
        )
        for tier, info in PLANS.items()
        if not info.get("hidden")
    ]
    return PlansResponse(plans=plans_list)


@router.post("/webhook")
async def stripe_webhook(request: Request) -> dict[str, str]:
    """Handle Stripe webhook events (invoice.paid, subscription updated/deleted)."""
    if not settings.STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=503, detail="Webhook secret not configured")
    payload: bytes = await request.body()
    sig = request.headers.get("stripe-signature", "")
    import stripe
    stripe.api_key = settings.STRIPE_SECRET_KEY
    try:
        event = stripe.Webhook.construct_event(
            payload, sig, settings.STRIPE_WEBHOOK_SECRET
        )
    except ValueError as e:
        logger.warning("Stripe webhook invalid payload: %s", e)
        raise HTTPException(status_code=400, detail="Invalid payload")
    except Exception as e:
        logger.warning("Stripe webhook signature verification failed: %s", e)
        raise HTTPException(status_code=400, detail="Invalid signature")
    if event.type == "invoice.paid":
        await _handle_invoice_paid(event.data.object)
    elif event.type == "customer.subscription.updated":
        await _handle_subscription_updated(event.data.object)
    elif event.type == "customer.subscription.deleted":
        await _handle_subscription_deleted(event.data.object)
    return {"status": "ok"}


async def _handle_invoice_paid(invoice: Any) -> None:
    """On renewal: set new period and reset credits (with optional rollover)."""
    sub_id = invoice.get("subscription")
    if not sub_id:
        return
    import stripe
    stripe.api_key = settings.STRIPE_SECRET_KEY
    sub = stripe.Subscription.retrieve(sub_id)
    org_id = sub.metadata.get("organization_id")
    tier = sub.metadata.get("tier") or _tier_from_price(invoice)
    if not org_id:
        return
    plan = PLANS.get(tier or "", {})
    async with get_admin_session() as session:
        from sqlalchemy import select
        result = await session.execute(
            select(Organization).where(Organization.id == UUID(org_id))
        )
        org = result.scalar_one_or_none()
        if not org:
            return

        # Use plan credits if tier found, otherwise keep org's existing credits_included
        # This prevents overwriting 500 with 100 if tier lookup fails
        credits_included: int = plan.get("credits_included") or org.credits_included or 100
        effective_tier: str = tier or org.subscription_tier or ""
        cap: int = ROLLOVER_CAP.get(effective_tier, 0)

        rollover = 0
        if cap > 1 and org.credits_balance > 0:
            rollover = min(
                org.credits_balance,
                credits_included * (cap - 1),
            )
        org.credits_balance = credits_included + rollover
        org.credits_included = credits_included
        org.current_period_start = datetime.fromtimestamp(
            invoice.get("period_start", 0), tz=timezone.utc
        )
        org.current_period_end = datetime.fromtimestamp(
            invoice.get("period_end", 0), tz=timezone.utc
        )
        await session.commit()


def _tier_from_price(invoice: Any) -> Optional[str]:
    """Infer tier from invoice line items price id."""
    for line in invoice.get("lines", {}).get("data", []):
        pid = line.get("price", {}).get("id")
        for tier, price_id in STRIPE_PRICE_IDS.items():
            if price_id and pid == price_id:
                return tier
    return None


async def _handle_subscription_updated(sub: Any) -> None:
    """Update org tier/status/period from subscription."""
    org_id = sub.metadata.get("organization_id")
    if not org_id:
        return
    tier = sub.metadata.get("tier") or _tier_from_subscription(sub)
    plan = PLANS.get(tier or "", {})
    async with get_admin_session() as session:
        from sqlalchemy import select
        result = await session.execute(
            select(Organization).where(Organization.id == UUID(org_id))
        )
        org = result.scalar_one_or_none()
        if not org:
            return
        org.subscription_status = sub.get("status") or "active"
        org.subscription_tier = tier
        org.credits_included = plan.get("credits_included", org.credits_included)
        if sub.get("current_period_end"):
            org.current_period_end = datetime.fromtimestamp(
                sub["current_period_end"], tz=timezone.utc
            )
        if sub.get("current_period_start"):
            org.current_period_start = datetime.fromtimestamp(
                sub["current_period_start"], tz=timezone.utc
            )
        await session.commit()


def _tier_from_subscription(sub: Any) -> Optional[str]:
    items = sub.get("items", {}).get("data", [])
    if not items:
        return None
    price_id = items[0].get("price", {}).get("id")
    for tier, pid in STRIPE_PRICE_IDS.items():
        if pid and price_id == pid:
            return tier
    return None


async def _handle_subscription_deleted(sub: Any) -> None:
    """Mark org subscription as canceled."""
    org_id = sub.metadata.get("organization_id")
    if not org_id:
        return
    async with get_admin_session() as session:
        from sqlalchemy import select, update
        await session.execute(
            update(Organization)
            .where(Organization.id == UUID(org_id))
            .values(
                subscription_status="canceled",
                stripe_subscription_id=None,
            )
        )
        await session.commit()
