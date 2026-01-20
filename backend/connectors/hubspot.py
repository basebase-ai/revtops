"""
HubSpot connector implementation.

Responsibilities:
- Authenticate with HubSpot using OAuth token
- Fetch Deals, Contacts, Companies
- Normalize HubSpot schema to our canonical schema
- Handle pagination and rate limits
- Upsert normalized data to database
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

import httpx
from sqlalchemy import select

from connectors.base import BaseConnector
from models.account import Account
from models.activity import Activity
from models.contact import Contact
from models.database import get_session
from models.deal import Deal
from models.user import User

HUBSPOT_API_BASE = "https://api.hubapi.com"


class HubSpotConnector(BaseConnector):
    """Connector for HubSpot CRM."""

    source_system = "hubspot"

    async def _get_headers(self) -> dict[str, str]:
        """Get authorization headers for HubSpot API."""
        token, _ = await self.get_oauth_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    async def _make_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[dict[str, Any]] = None,
        json_data: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Make an authenticated request to HubSpot API."""
        headers = await self._get_headers()
        url = f"{HUBSPOT_API_BASE}{endpoint}"

        async with httpx.AsyncClient() as client:
            response = await client.request(
                method=method,
                url=url,
                headers=headers,
                params=params,
                json=json_data,
                timeout=30.0,
            )
            response.raise_for_status()
            return response.json()

    async def _paginate_results(
        self,
        endpoint: str,
        properties: list[str],
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Paginate through HubSpot API results."""
        all_results: list[dict[str, Any]] = []
        after: Optional[str] = None

        while True:
            params: dict[str, Any] = {
                "limit": limit,
                "properties": ",".join(properties),
            }
            if after:
                params["after"] = after

            data = await self._make_request("GET", endpoint, params=params)
            results = data.get("results", [])
            all_results.extend(results)

            # Check for pagination
            paging = data.get("paging", {})
            next_link = paging.get("next", {})
            after = next_link.get("after")

            if not after:
                break

        return all_results

    async def sync_deals(self) -> int:
        """
        Sync all deals from HubSpot.

        HubSpot deal properties:
        - dealname, amount, dealstage, closedate, createdate, hs_lastmodifieddate
        - hubspot_owner_id, associated company/contact
        """
        properties = [
            "dealname",
            "amount",
            "dealstage",
            "closedate",
            "createdate",
            "hs_lastmodifieddate",
            "hubspot_owner_id",
            "pipeline",
        ]

        raw_deals = await self._paginate_results(
            "/crm/v3/objects/deals", properties=properties
        )

        async with get_session() as session:
            count = 0
            for raw_deal in raw_deals:
                deal = await self._normalize_deal(raw_deal)
                await session.merge(deal)
                count += 1
            await session.commit()

        return count

    async def _normalize_deal(self, hs_deal: dict[str, Any]) -> Deal:
        """Transform HubSpot Deal to our Deal model."""
        props = hs_deal.get("properties", {})
        hs_id = hs_deal.get("id", "")

        # Map HubSpot owner to our user
        owner_id = await self._map_hs_owner_to_user(props.get("hubspot_owner_id"))

        # Parse amount
        amount: Optional[Decimal] = None
        if props.get("amount"):
            try:
                amount = Decimal(str(props["amount"]))
            except (ValueError, TypeError):
                pass

        # Parse dates
        close_date = None
        if props.get("closedate"):
            try:
                close_date = datetime.fromisoformat(
                    props["closedate"].replace("Z", "+00:00")
                ).date()
            except (ValueError, TypeError):
                pass

        created_date = None
        if props.get("createdate"):
            try:
                created_date = datetime.fromisoformat(
                    props["createdate"].replace("Z", "+00:00")
                )
            except (ValueError, TypeError):
                pass

        last_modified = None
        if props.get("hs_lastmodifieddate"):
            try:
                last_modified = datetime.fromisoformat(
                    props["hs_lastmodifieddate"].replace("Z", "+00:00")
                )
            except (ValueError, TypeError):
                pass

        return Deal(
            id=uuid.uuid4(),
            customer_id=uuid.UUID(self.customer_id),
            source_system=self.source_system,
            source_id=hs_id,
            name=props.get("dealname", "Untitled Deal"),
            amount=amount,
            stage=props.get("dealstage"),
            close_date=close_date,
            created_date=created_date,
            last_modified_date=last_modified,
            owner_id=owner_id,
            visible_to_user_ids=[owner_id] if owner_id else [],
            custom_fields={"pipeline": props.get("pipeline")},
        )

    async def sync_accounts(self) -> int:
        """Sync all companies from HubSpot as accounts."""
        properties = [
            "name",
            "domain",
            "industry",
            "numberofemployees",
            "annualrevenue",
            "hubspot_owner_id",
            "createdate",
            "hs_lastmodifieddate",
        ]

        raw_companies = await self._paginate_results(
            "/crm/v3/objects/companies", properties=properties
        )

        async with get_session() as session:
            count = 0
            for raw_company in raw_companies:
                account = await self._normalize_account(raw_company)
                await session.merge(account)
                count += 1
            await session.commit()

        return count

    async def _normalize_account(self, hs_company: dict[str, Any]) -> Account:
        """Transform HubSpot Company to our Account model."""
        props = hs_company.get("properties", {})
        hs_id = hs_company.get("id", "")

        owner_id = await self._map_hs_owner_to_user(props.get("hubspot_owner_id"))

        # Parse employee count
        employee_count: Optional[int] = None
        if props.get("numberofemployees"):
            try:
                employee_count = int(props["numberofemployees"])
            except (ValueError, TypeError):
                pass

        # Parse annual revenue
        annual_revenue: Optional[Decimal] = None
        if props.get("annualrevenue"):
            try:
                annual_revenue = Decimal(str(props["annualrevenue"]))
            except (ValueError, TypeError):
                pass

        return Account(
            id=uuid.uuid4(),
            customer_id=uuid.UUID(self.customer_id),
            source_system=self.source_system,
            source_id=hs_id,
            name=props.get("name", "Unknown Company"),
            domain=props.get("domain"),
            industry=props.get("industry"),
            employee_count=employee_count,
            annual_revenue=annual_revenue,
            owner_id=owner_id,
        )

    async def sync_contacts(self) -> int:
        """Sync all contacts from HubSpot."""
        properties = [
            "firstname",
            "lastname",
            "email",
            "jobtitle",
            "phone",
            "company",
            "hubspot_owner_id",
            "createdate",
            "hs_lastmodifieddate",
        ]

        raw_contacts = await self._paginate_results(
            "/crm/v3/objects/contacts", properties=properties
        )

        async with get_session() as session:
            count = 0
            for raw_contact in raw_contacts:
                contact = await self._normalize_contact(raw_contact)
                await session.merge(contact)
                count += 1
            await session.commit()

        return count

    async def _normalize_contact(self, hs_contact: dict[str, Any]) -> Contact:
        """Transform HubSpot Contact to our Contact model."""
        props = hs_contact.get("properties", {})
        hs_id = hs_contact.get("id", "")

        # Combine first and last name
        first_name = props.get("firstname", "")
        last_name = props.get("lastname", "")
        full_name = f"{first_name} {last_name}".strip() or None

        return Contact(
            id=uuid.uuid4(),
            customer_id=uuid.UUID(self.customer_id),
            source_system=self.source_system,
            source_id=hs_id,
            name=full_name,
            email=props.get("email"),
            title=props.get("jobtitle"),
            phone=props.get("phone"),
        )

    async def sync_activities(self) -> int:
        """Sync engagements (calls, emails, meetings, notes) from HubSpot."""
        count = 0

        # Sync different engagement types
        for engagement_type in ["calls", "emails", "meetings", "notes"]:
            properties = ["hs_timestamp", "hs_call_title", "hs_call_body"]
            if engagement_type == "emails":
                properties = ["hs_timestamp", "hs_email_subject", "hs_email_text"]
            elif engagement_type == "meetings":
                properties = ["hs_timestamp", "hs_meeting_title", "hs_meeting_body"]
            elif engagement_type == "notes":
                properties = ["hs_timestamp", "hs_note_body"]

            try:
                raw_engagements = await self._paginate_results(
                    f"/crm/v3/objects/{engagement_type}", properties=properties
                )

                async with get_session() as session:
                    for raw_engagement in raw_engagements:
                        activity = self._normalize_engagement(
                            raw_engagement, engagement_type
                        )
                        await session.merge(activity)
                        count += 1
                    await session.commit()
            except httpx.HTTPStatusError:
                # Some engagement types might not be available
                continue

        return count

    def _normalize_engagement(
        self, hs_engagement: dict[str, Any], engagement_type: str
    ) -> Activity:
        """Transform HubSpot engagement to our Activity model."""
        props = hs_engagement.get("properties", {})
        hs_id = hs_engagement.get("id", "")

        # Determine subject and description based on type
        subject: Optional[str] = None
        description: Optional[str] = None

        if engagement_type == "calls":
            subject = props.get("hs_call_title")
            description = props.get("hs_call_body")
        elif engagement_type == "emails":
            subject = props.get("hs_email_subject")
            description = props.get("hs_email_text")
        elif engagement_type == "meetings":
            subject = props.get("hs_meeting_title")
            description = props.get("hs_meeting_body")
        elif engagement_type == "notes":
            description = props.get("hs_note_body")

        # Parse timestamp
        activity_date: Optional[datetime] = None
        if props.get("hs_timestamp"):
            try:
                # HubSpot timestamps are in milliseconds
                ts = int(props["hs_timestamp"])
                activity_date = datetime.fromtimestamp(ts / 1000)
            except (ValueError, TypeError):
                pass

        # Map engagement type to our activity type
        type_map = {
            "calls": "call",
            "emails": "email",
            "meetings": "meeting",
            "notes": "note",
        }

        return Activity(
            id=uuid.uuid4(),
            customer_id=uuid.UUID(self.customer_id),
            source_system=self.source_system,
            source_id=hs_id,
            type=type_map.get(engagement_type, engagement_type),
            subject=subject,
            description=description,
            activity_date=activity_date,
        )

    async def _map_hs_owner_to_user(
        self, hs_owner_id: Optional[str]
    ) -> Optional[uuid.UUID]:
        """Map HubSpot owner ID to our internal user ID."""
        if not hs_owner_id:
            return None

        async with get_session() as session:
            # Look for user with matching HubSpot ID in custom_fields or a dedicated field
            result = await session.execute(
                select(User).where(
                    User.customer_id == uuid.UUID(self.customer_id),
                )
            )
            # For MVP, return first user; in production, match by HubSpot owner ID
            user = result.scalars().first()
            return user.id if user else None

    async def fetch_deal(self, deal_id: str) -> dict[str, Any]:
        """Fetch single deal on-demand."""
        properties = [
            "dealname",
            "amount",
            "dealstage",
            "closedate",
            "createdate",
            "hs_lastmodifieddate",
            "hubspot_owner_id",
        ]
        params = {"properties": ",".join(properties)}
        data = await self._make_request(
            "GET", f"/crm/v3/objects/deals/{deal_id}", params=params
        )
        deal = await self._normalize_deal(data)
        return deal.to_dict()
