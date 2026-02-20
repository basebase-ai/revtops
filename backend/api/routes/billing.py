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
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from api.auth_middleware import AuthContext, require_organization
from config import settings
from models.database import get_admin_session
from models.organization import Organization
from services.credits import ACTIVE_SUBSCRIPTION_STATUSES

logger = logging.getLogger(__name__)

router = APIRouter()

# Tier config: name, price_cents, credits_included
PLANS: dict[str, dict[str, Any]] = {
    "starter": {"name": "Starter", "price_cents": 2000, "credits_included": 100},
    "pro": {"name": "Pro", "price_cents": 10000, "credits_included": 500},
    "business": {"name": "Business", "price_cents": 25000, "credits_included": 2500},
    "scale": {"name": "Scale", "price_cents": 59900, "credits_included": 8000},
}

# Rollover cap multiplier per tier (e.g. Pro: unused credits up to 2x included)
ROLLOVER_CAP: dict[str, int] = {
    "starter": 0,
    "pro": 2,
    "business": 2,
    "scale": 3,
}

# Stripe Price IDs (Dashboard → Products → [your product] → Pricing → copy "Price ID", e.g. price_1ABC...)
# Subscription.create requires price IDs, not product IDs (prod_xxx).
STRIPE_PRICE_IDS: dict[str, str] = {
    "starter": "price_1T2zFwBB0TvgbMzReazNnwin",   # paste price_xxx for Starter
    "pro": "price_1T2zG6BB0TvgbMzRkCwxwTKm",
    "business": "price_1T2zGkBB0TvgbMzRYy2b7Y0r",
    "scale": "price_1T2zH1BB0TvgbMzRmJF4RglP",
}


def _stripe_price_id_for_tier(tier: str) -> Optional[str]:
    pid = STRIPE_PRICE_IDS.get(tier)
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
    payment_method_id: str = Field(..., min_length=1)
    tier: str = Field(..., pattern="^(starter|pro|business|scale)$")


class ChangePlanRequest(BaseModel):
    tier: str = Field(..., pattern="^(starter|pro|business|scale)$")


class PlanItem(BaseModel):
    tier: str
    name: str
    price_cents: int
    credits_included: int
    stripe_product_id: Optional[str] = None


class PlansResponse(BaseModel):
    plans: list[PlanItem]


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
        # Create subscription; webhook will set tier, period, credits
        sub = stripe.Subscription.create(
            customer=customer_id,
            items=[{"price": price_id}],
            payment_behavior="default_incomplete",
            expand=["latest_invoice"],
            metadata={"organization_id": str(org_id), "tier": body.tier},
        )
        org.stripe_subscription_id = sub.id
        org.subscription_tier = body.tier
        org.subscription_status = sub.status or "active"
        plan = PLANS.get(body.tier, {})
        org.credits_included = plan.get("credits_included", 100)
        if sub.status == "active":
            org.credits_balance = org.credits_included
            li = sub.latest_invoice
            if li and getattr(li, "current_period_end", None):
                org.current_period_end = datetime.fromtimestamp(
                    li.current_period_end, tz=timezone.utc
                )
            if li and getattr(li, "current_period_start", None):
                org.current_period_start = datetime.fromtimestamp(
                    li.current_period_start, tz=timezone.utc
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


@router.get("/plans", response_model=PlansResponse)
async def list_plans() -> PlansResponse:
    """Return available plans for the plan selector."""
    plans_list = [
        PlanItem(
            tier=tier,
            name=info["name"],
            price_cents=info["price_cents"],
            credits_included=info["credits_included"],
            stripe_product_id=_stripe_price_id_for_tier(tier),
        )
        for tier, info in PLANS.items()
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
    if not org_id or not tier:
        return
    plan = PLANS.get(tier, {})
    credits_included = plan.get("credits_included", 100)
    cap = ROLLOVER_CAP.get(tier, 0)
    async with get_admin_session() as session:
        from sqlalchemy import select
        result = await session.execute(
            select(Organization).where(Organization.id == UUID(org_id))
        )
        org = result.scalar_one_or_none()
        if not org:
            return
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
