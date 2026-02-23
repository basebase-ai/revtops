#!/usr/bin/env python3
"""
Grant free credits to an organization.

Usage:
    python scripts/grant_free_credits.py <org_id_or_domain> [--months 12] [--credits 2000]

Examples:
    python scripts/grant_free_credits.py company.com
    python scripts/grant_free_credits.py dbe0b687-6967-4874-a26d-10f6289ae350 --months 6
    python scripts/grant_free_credits.py acme.io --credits 5000 --months 12
"""
import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from uuid import UUID

sys.path.insert(0, str(__file__).rsplit("/scripts", 1)[0])

from sqlalchemy import text
from models.database import get_admin_session


async def find_org_by_domain(domain: str) -> UUID | None:
    """Find organization ID by email_domain."""
    async with get_admin_session() as session:
        result = await session.execute(
            text("""
                SELECT id
                FROM organizations
                WHERE email_domain = :domain
                LIMIT 1
            """),
            {"domain": domain},
        )
        return result.scalar_one_or_none()


async def grant_free_credits(
    org_id_or_domain: str,
    months: int = 12,
    credits: int = 2000,
) -> bool:
    """Grant partner tier access with free credits to an organization."""
    org_uuid: UUID | None = None

    try:
        org_uuid = UUID(org_id_or_domain)
    except ValueError:
        org_uuid = await find_org_by_domain(org_id_or_domain)
        if not org_uuid:
            print(f"Error: Could not find organization for domain '{org_id_or_domain}'")
            return False
        print(f"Found organization {org_uuid} for domain {org_id_or_domain}")

    now = datetime.now(timezone.utc)
    period_end = now + timedelta(days=30 * months)

    async with get_admin_session() as session:
        result = await session.execute(
            text("SELECT name FROM organizations WHERE id = :org_id"),
            {"org_id": str(org_uuid)},
        )
        row = result.fetchone()
        if not row:
            print(f"Error: Organization {org_uuid} not found")
            return False
        org_name: str = row[0]

        await session.execute(
            text("""
                UPDATE organizations
                SET subscription_tier = 'partner',
                    subscription_status = 'active',
                    credits_balance = :credits,
                    credits_included = :credits,
                    current_period_start = :period_start,
                    current_period_end = :period_end,
                    stripe_customer_id = NULL,
                    stripe_subscription_id = NULL
                WHERE id = :org_id
            """),
            {
                "org_id": str(org_uuid),
                "credits": credits,
                "period_start": now,
                "period_end": period_end,
            },
        )
        await session.commit()

        print(f"✓ Granted free credits to: {org_name}")
        print(f"  Tier: partner")
        print(f"  Credits: {credits}")
        print(f"  Valid until: {period_end.strftime('%Y-%m-%d')}")
        return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Grant free credits to an organization"
    )
    parser.add_argument(
        "org_id_or_domain",
        help="Organization ID (UUID) or domain (e.g., company.com)",
    )
    parser.add_argument(
        "--months",
        type=int,
        default=12,
        help="Number of months to grant access (default: 12)",
    )
    parser.add_argument(
        "--credits",
        type=int,
        default=2000,
        help="Number of credits to grant (default: 2000)",
    )
    args = parser.parse_args()

    success = asyncio.run(
        grant_free_credits(args.org_id_or_domain, args.months, args.credits)
    )
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
