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

    def __init__(self, organization_id: str) -> None:
        """Initialize connector with owner cache."""
        super().__init__(organization_id)
        # Cache for HubSpot owner ID -> internal user ID mapping
        self._owner_cache: dict[str, Optional[uuid.UUID]] = {}

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
            
            # If error, try to get detailed error message from HubSpot
            if response.status_code >= 400:
                error_detail = ""
                try:
                    error_body = response.json()
                    # HubSpot error format: {"message": "...", "errors": [...]}
                    error_detail = error_body.get("message", "")
                    if error_body.get("errors"):
                        error_details = [e.get("message", str(e)) for e in error_body["errors"]]
                        error_detail = f"{error_detail}: {'; '.join(error_details)}"
                except Exception:
                    error_detail = response.text[:500] if response.text else ""
                
                raise httpx.HTTPStatusError(
                    f"HubSpot API error ({response.status_code}): {error_detail}",
                    request=response.request,
                    response=response,
                )
            
            return response.json()

    async def _paginate_results(
        self,
        endpoint: str,
        properties: list[str],
        limit: int = 100,
        associations: Optional[list[str]] = None,
    ) -> list[dict[str, Any]]:
        """Paginate through HubSpot API results."""
        all_results: list[dict[str, Any]] = []
        after: Optional[str] = None

        while True:
            params: dict[str, Any] = {
                "limit": limit,
                "properties": ",".join(properties),
            }
            if associations:
                params["associations"] = ",".join(associations)
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
                hs_id = raw_deal.get("id", "")
                
                # Check if deal already exists
                result = await session.execute(
                    select(Deal).where(
                        Deal.organization_id == uuid.UUID(self.organization_id),
                        Deal.source_system == self.source_system,
                        Deal.source_id == hs_id,
                    )
                )
                existing = result.scalar_one_or_none()
                
                deal = await self._normalize_deal(raw_deal, existing_id=existing.id if existing else None)
                await session.merge(deal)
                count += 1
            await session.commit()

        return count

    async def _normalize_deal(
        self, hs_deal: dict[str, Any], existing_id: Optional[uuid.UUID] = None
    ) -> Deal:
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
                dt = datetime.fromisoformat(
                    props["closedate"].replace("Z", "+00:00")
                )
                close_date = dt.date()
            except (ValueError, TypeError):
                pass

        created_date = None
        if props.get("createdate"):
            try:
                dt = datetime.fromisoformat(
                    props["createdate"].replace("Z", "+00:00")
                )
                # Convert to naive datetime (strip timezone)
                created_date = dt.replace(tzinfo=None)
            except (ValueError, TypeError):
                pass

        last_modified = None
        if props.get("hs_lastmodifieddate"):
            try:
                dt = datetime.fromisoformat(
                    props["hs_lastmodifieddate"].replace("Z", "+00:00")
                )
                # Convert to naive datetime (strip timezone)
                last_modified = dt.replace(tzinfo=None)
            except (ValueError, TypeError):
                pass

        return Deal(
            id=existing_id or uuid.uuid4(),
            organization_id=uuid.UUID(self.organization_id),
            source_system=self.source_system,
            source_id=hs_id,
            name=props.get("dealname") or "Untitled Deal",
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
                hs_id = raw_company.get("id", "")
                
                # Check if account already exists
                result = await session.execute(
                    select(Account).where(
                        Account.organization_id == uuid.UUID(self.organization_id),
                        Account.source_system == self.source_system,
                        Account.source_id == hs_id,
                    )
                )
                existing = result.scalar_one_or_none()
                
                account = await self._normalize_account(raw_company, existing_id=existing.id if existing else None)
                await session.merge(account)
                count += 1
            await session.commit()

        return count

    async def _normalize_account(
        self, hs_company: dict[str, Any], existing_id: Optional[uuid.UUID] = None
    ) -> Account:
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

        # Name is required - use domain or fallback if name is None/empty
        name = props.get("name")
        if not name:
            name = props.get("domain") or f"Company {hs_id}"

        return Account(
            id=existing_id or uuid.uuid4(),
            organization_id=uuid.UUID(self.organization_id),
            source_system=self.source_system,
            source_id=hs_id,
            name=name,
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

        # Fetch contacts with company associations
        raw_contacts = await self._paginate_results(
            "/crm/v3/objects/contacts",
            properties=properties,
            associations=["companies"],
        )

        # Build a map of HubSpot company IDs to internal account IDs
        hs_company_id_to_account_id: dict[str, uuid.UUID] = {}
        async with get_session() as session:
            result = await session.execute(
                select(Account).where(
                    Account.organization_id == uuid.UUID(self.organization_id),
                    Account.source_system == self.source_system,
                )
            )
            accounts = result.scalars().all()
            for account in accounts:
                hs_company_id_to_account_id[account.source_id] = account.id

        async with get_session() as session:
            count = 0
            for raw_contact in raw_contacts:
                hs_id = raw_contact.get("id", "")
                
                # Extract associated company ID from associations
                account_id: Optional[uuid.UUID] = None
                associations = raw_contact.get("associations", {})
                companies_assoc = associations.get("companies", {})
                company_results = companies_assoc.get("results", [])
                if company_results:
                    # Take the first (primary) company association
                    hs_company_id = company_results[0].get("id")
                    if hs_company_id:
                        account_id = hs_company_id_to_account_id.get(hs_company_id)
                
                # Check if contact already exists
                result = await session.execute(
                    select(Contact).where(
                        Contact.organization_id == uuid.UUID(self.organization_id),
                        Contact.source_system == self.source_system,
                        Contact.source_id == hs_id,
                    )
                )
                existing = result.scalar_one_or_none()
                
                contact = self._normalize_contact(
                    raw_contact,
                    existing_id=existing.id if existing else None,
                    account_id=account_id,
                )
                await session.merge(contact)
                count += 1
            await session.commit()

        return count

    def _normalize_contact(
        self,
        hs_contact: dict[str, Any],
        existing_id: Optional[uuid.UUID] = None,
        account_id: Optional[uuid.UUID] = None,
    ) -> Contact:
        """Transform HubSpot Contact to our Contact model."""
        props = hs_contact.get("properties", {})
        hs_id = hs_contact.get("id", "")

        # Combine first and last name
        first_name = props.get("firstname") or ""
        last_name = props.get("lastname") or ""
        full_name = f"{first_name} {last_name}".strip()
        
        # Use email or ID as fallback if no name
        if not full_name:
            full_name = props.get("email") or f"Contact {hs_id}"

        return Contact(
            id=existing_id or uuid.uuid4(),
            organization_id=uuid.UUID(self.organization_id),
            source_system=self.source_system,
            source_id=hs_id,
            name=full_name,
            email=props.get("email"),
            title=props.get("jobtitle"),
            phone=props.get("phone"),
            account_id=account_id,
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
                # Use naive datetime (no timezone)
                activity_date = datetime.utcfromtimestamp(ts / 1000)
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
            organization_id=uuid.UUID(self.organization_id),
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
        """
        Map HubSpot owner ID to our internal user ID by fetching owner email.
        
        If no matching user exists, creates a stub user with status='crm_only'
        that can be upgraded when the person signs up for Revtops.
        """
        if not hs_owner_id:
            return None

        # Check cache first
        if hs_owner_id in self._owner_cache:
            return self._owner_cache[hs_owner_id]

        # Fetch owner details from HubSpot to get their email and name
        try:
            owner_data = await self._make_request(
                "GET", f"/crm/v3/owners/{hs_owner_id}"
            )
            owner_email: Optional[str] = owner_data.get("email")
            if not owner_email:
                self._owner_cache[hs_owner_id] = None
                return None
            
            # Build owner name from firstName/lastName
            first_name: str = owner_data.get("firstName") or ""
            last_name: str = owner_data.get("lastName") or ""
            owner_name: Optional[str] = f"{first_name} {last_name}".strip() or None
        except httpx.HTTPStatusError:
            # If we can't fetch owner, fall back to None
            self._owner_cache[hs_owner_id] = None
            return None

        # Look up user by email (email is globally unique, not per-org)
        async with get_session() as session:
            result = await session.execute(
                select(User).where(User.email == owner_email)
            )
            user = result.scalar_one_or_none()
            
            if user:
                # User exists - check if they belong to this organization
                if user.organization_id == uuid.UUID(self.organization_id):
                    self._owner_cache[hs_owner_id] = user.id
                    return user.id
                else:
                    # User exists but in different org - can't assign cross-org ownership
                    self._owner_cache[hs_owner_id] = None
                    return None
            
            # No user with this email exists - create a stub user for this CRM owner
            stub_user = User(
                id=uuid.uuid4(),
                email=owner_email,
                name=owner_name,
                organization_id=uuid.UUID(self.organization_id),
                status="crm_only",  # Stub user from CRM sync, not yet signed up
            )
            session.add(stub_user)
            await session.commit()
            
            self._owner_cache[hs_owner_id] = stub_user.id
            return stub_user.id

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

    # =========================================================================
    # Write Operations
    # =========================================================================

    async def create_contact(self, properties: dict[str, Any]) -> dict[str, Any]:
        """
        Create a single contact in HubSpot.

        Args:
            properties: Contact properties (email, firstname, lastname, company, jobtitle, phone)

        Returns:
            Created contact data with HubSpot ID
        """
        data = await self._make_request(
            "POST",
            "/crm/v3/objects/contacts",
            json_data={"properties": properties},
        )
        return {
            "id": data.get("id"),
            "properties": data.get("properties", {}),
        }

    async def create_company(self, properties: dict[str, Any]) -> dict[str, Any]:
        """
        Create a single company in HubSpot.

        Args:
            properties: Company properties (name, domain, industry, etc.)

        Returns:
            Created company data with HubSpot ID
        """
        data = await self._make_request(
            "POST",
            "/crm/v3/objects/companies",
            json_data={"properties": properties},
        )
        return {
            "id": data.get("id"),
            "properties": data.get("properties", {}),
        }

    async def create_deal(self, properties: dict[str, Any]) -> dict[str, Any]:
        """
        Create a single deal in HubSpot.

        Args:
            properties: Deal properties (dealname, amount, dealstage, closedate, pipeline)

        Returns:
            Created deal data with HubSpot ID
        """
        data = await self._make_request(
            "POST",
            "/crm/v3/objects/deals",
            json_data={"properties": properties},
        )
        return {
            "id": data.get("id"),
            "properties": data.get("properties", {}),
        }

    async def update_contact(
        self, contact_id: str, properties: dict[str, Any]
    ) -> dict[str, Any]:
        """Update an existing contact in HubSpot."""
        data = await self._make_request(
            "PATCH",
            f"/crm/v3/objects/contacts/{contact_id}",
            json_data={"properties": properties},
        )
        return {
            "id": data.get("id"),
            "properties": data.get("properties", {}),
        }

    async def update_company(
        self, company_id: str, properties: dict[str, Any]
    ) -> dict[str, Any]:
        """Update an existing company in HubSpot."""
        data = await self._make_request(
            "PATCH",
            f"/crm/v3/objects/companies/{company_id}",
            json_data={"properties": properties},
        )
        return {
            "id": data.get("id"),
            "properties": data.get("properties", {}),
        }

    async def update_deal(
        self, deal_id: str, properties: dict[str, Any]
    ) -> dict[str, Any]:
        """Update an existing deal in HubSpot."""
        data = await self._make_request(
            "PATCH",
            f"/crm/v3/objects/deals/{deal_id}",
            json_data={"properties": properties},
        )
        return {
            "id": data.get("id"),
            "properties": data.get("properties", {}),
        }

    async def create_contacts_batch(
        self, contacts: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """
        Batch create contacts in HubSpot (up to 100 per call).

        Args:
            contacts: List of contact property dicts

        Returns:
            Batch result with created contacts and any errors
        """
        if len(contacts) > 100:
            raise ValueError("HubSpot batch limit is 100 records per call")

        inputs = [{"properties": c} for c in contacts]
        data = await self._make_request(
            "POST",
            "/crm/v3/objects/contacts/batch/create",
            json_data={"inputs": inputs},
        )
        return {
            "status": data.get("status", "COMPLETE"),
            "results": [
                {"id": r.get("id"), "properties": r.get("properties", {})}
                for r in data.get("results", [])
            ],
            "errors": data.get("errors", []),
        }

    async def create_companies_batch(
        self, companies: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Batch create companies in HubSpot (up to 100 per call)."""
        if len(companies) > 100:
            raise ValueError("HubSpot batch limit is 100 records per call")

        inputs = [{"properties": c} for c in companies]
        data = await self._make_request(
            "POST",
            "/crm/v3/objects/companies/batch/create",
            json_data={"inputs": inputs},
        )
        return {
            "status": data.get("status", "COMPLETE"),
            "results": [
                {"id": r.get("id"), "properties": r.get("properties", {})}
                for r in data.get("results", [])
            ],
            "errors": data.get("errors", []),
        }

    async def create_deals_batch(self, deals: list[dict[str, Any]]) -> dict[str, Any]:
        """Batch create deals in HubSpot (up to 100 per call)."""
        if len(deals) > 100:
            raise ValueError("HubSpot batch limit is 100 records per call")

        inputs = [{"properties": d} for d in deals]
        data = await self._make_request(
            "POST",
            "/crm/v3/objects/deals/batch/create",
            json_data={"inputs": inputs},
        )
        return {
            "status": data.get("status", "COMPLETE"),
            "results": [
                {"id": r.get("id"), "properties": r.get("properties", {})}
                for r in data.get("results", [])
            ],
            "errors": data.get("errors", []),
        }

    async def find_contact_by_email(self, email: str) -> dict[str, Any] | None:
        """
        Search for a contact by email address.

        Args:
            email: Email address to search for

        Returns:
            Contact data if found, None otherwise
        """
        try:
            data = await self._make_request(
                "POST",
                "/crm/v3/objects/contacts/search",
                json_data={
                    "filterGroups": [
                        {
                            "filters": [
                                {
                                    "propertyName": "email",
                                    "operator": "EQ",
                                    "value": email,
                                }
                            ]
                        }
                    ],
                    "properties": ["email", "firstname", "lastname", "company"],
                    "limit": 1,
                },
            )
            results = data.get("results", [])
            if results:
                return {
                    "id": results[0].get("id"),
                    "properties": results[0].get("properties", {}),
                }
            return None
        except httpx.HTTPStatusError:
            return None

    async def find_company_by_domain(self, domain: str) -> dict[str, Any] | None:
        """
        Search for a company by domain.

        Args:
            domain: Company domain to search for

        Returns:
            Company data if found, None otherwise
        """
        try:
            data = await self._make_request(
                "POST",
                "/crm/v3/objects/companies/search",
                json_data={
                    "filterGroups": [
                        {
                            "filters": [
                                {
                                    "propertyName": "domain",
                                    "operator": "EQ",
                                    "value": domain,
                                }
                            ]
                        }
                    ],
                    "properties": ["name", "domain", "industry"],
                    "limit": 1,
                },
            )
            results = data.get("results", [])
            if results:
                return {
                    "id": results[0].get("id"),
                    "properties": results[0].get("properties", {}),
                }
            return None
        except httpx.HTTPStatusError:
            return None

    async def find_deal_by_name(self, name: str) -> dict[str, Any] | None:
        """
        Search for a deal by name.

        Args:
            name: Deal name to search for

        Returns:
            Deal data if found, None otherwise
        """
        try:
            data = await self._make_request(
                "POST",
                "/crm/v3/objects/deals/search",
                json_data={
                    "filterGroups": [
                        {
                            "filters": [
                                {
                                    "propertyName": "dealname",
                                    "operator": "EQ",
                                    "value": name,
                                }
                            ]
                        }
                    ],
                    "properties": ["dealname", "amount", "dealstage"],
                    "limit": 1,
                },
            )
            results = data.get("results", [])
            if results:
                return {
                    "id": results[0].get("id"),
                    "properties": results[0].get("properties", {}),
                }
            return None
        except httpx.HTTPStatusError:
            return None
