#!/usr/bin/env python3
"""
Grant partner access to an organization (free tier with generous credits).

Usage:
    python scripts/grant_partner_access.py <org_id_or_email> [--months 12] [--credits 2000]

Examples:
    python scripts/grant_partner_access.py partner@company.com
    python scripts/grant_partner_access.py dbe0b687-6967-4874-a26d-10f6289ae350 --months 6
    python scripts/grant_partner_access.py partner@company.com --credits 5000 --months 12
"""
import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from uuid import UUID

# Add parent directory to path for imports
sys.path.insert(0, str(__file__).rsplit("/scripts", 1)[0])

from sqlalchemy import text
from models.database import get_admin_session


async def find_org_by_email(email: str) -> UUID | None:
    """Find organization ID by user email (returns first org the user belongs to)."""
    async with get_admin_session() as session:
        result = await session.execute(
            text("""
                SELECT om.organization_id
                FROM org_members om
                JOIN users u ON u.id = om.user_id
                WHERE u.email = :email
                LIMIT 1
            """),
            {"email": email},
        )
        row = result.scalar_one_or_none()
        return row


async def grant_partner_access(
    org_id: str,
    months: int = 12,
    credits: int = 2000,
) -> bool:
    """Grant partner tier access to an organization."""
    try:
        org_uuid = UUID(org_id)
    except ValueError:
        # Might be an email, try to find the org
        org_uuid = await find_org_by_email(org_id)
        if not org_uuid:
            print(f"Error: Could not find organization for '{org_id}'")
            return False
        print(f"Found organization {org_uuid} for email {org_id}")

    now = datetime.now(timezone.utc)
    period_end = now + timedelta(days=30 * months)

    async with get_admin_session() as session:
        # First check if org exists and get its name
        result = await session.execute(
            text("SELECT name FROM organizations WHERE id = :org_id"),
            {"org_id": str(org_uuid)},
        )
        row = result.fetchone()
        if not row:
            print(f"Error: Organization {org_uuid} not found")
            return False
        org_name: str = row[0]

        # Update the organization
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

        print(f"✓ Granted partner access to: {org_name}")
        print(f"  Tier: partner")
        print(f"  Credits: {credits}")
        print(f"  Valid until: {period_end.strftime('%Y-%m-%d')}")
        return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Grant partner access to an organization"
    )
    parser.add_argument(
        "org_id",
        help="Organization ID (UUID) or user email address",
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
        grant_partner_access(args.org_id, args.months, args.credits)
    )
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
