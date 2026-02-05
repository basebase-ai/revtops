"""
Salesforce connector implementation.

Responsibilities:
- Authenticate with Salesforce using OAuth token via Nango
- Fetch Opportunities, Accounts, Contacts, Activities
- Normalize Salesforce schema to our canonical schema
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

# Salesforce API version
SF_API_VERSION = "v59.0"


class SalesforceConnector(BaseConnector):
    """Connector for Salesforce CRM."""

    source_system = "salesforce"

    def __init__(self, organization_id: str) -> None:
        """Initialize the connector."""
        super().__init__(organization_id)
        self._instance_url: Optional[str] = None

    async def _get_instance_url(self) -> str:
        """Get Salesforce instance URL from Nango credentials."""
        if self._instance_url:
            return self._instance_url

        credentials = await self.get_credentials()
        # Nango stores Salesforce instance URL in credentials
        instance_url = credentials.get("instance_url") or credentials.get("instanceUrl")
        if not instance_url:
            raise ValueError("Salesforce instance URL not found in credentials")

        self._instance_url = instance_url.rstrip("/")
        return self._instance_url

    async def _get_headers(self) -> dict[str, str]:
        """Get authorization headers for Salesforce API."""
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
        """Make an authenticated request to Salesforce API."""
        headers = await self._get_headers()
        instance_url = await self._get_instance_url()
        url = f"{instance_url}/services/data/{SF_API_VERSION}{endpoint}"

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

    async def _query_soql(self, soql: str) -> list[dict[str, Any]]:
        """
        Execute a SOQL query and paginate through all results.

        Salesforce returns results with a 'records' array and optionally
        'nextRecordsUrl' for pagination.
        """
        all_records: list[dict[str, Any]] = []
        headers = await self._get_headers()
        instance_url = await self._get_instance_url()

        # Initial query
        url = f"{instance_url}/services/data/{SF_API_VERSION}/query"
        params = {"q": soql}

        async with httpx.AsyncClient() as client:
            while True:
                response = await client.get(
                    url,
                    headers=headers,
                    params=params,
                    timeout=30.0,
                )
                response.raise_for_status()
                data = response.json()

                records = data.get("records", [])
                all_records.extend(records)

                # Check for more records
                next_url = data.get("nextRecordsUrl")
                if not next_url:
                    break

                # Pagination uses full URL path, no query params needed
                url = f"{instance_url}{next_url}"
                params = {}  # Clear params for subsequent requests

        return all_records

    async def sync_deals(self) -> int:
        """
        Sync all opportunities from Salesforce.

        Salesforce Opportunity fields:
        - Id, Name, AccountId, OwnerId, Amount, StageName
        - Probability, CloseDate, CreatedDate, LastModifiedDate
        """
        soql = """
            SELECT Id, Name, AccountId, OwnerId, Amount, StageName,
                   Probability, CloseDate, CreatedDate, LastModifiedDate
            FROM Opportunity
        """

        raw_opportunities = await self._query_soql(soql)

        async with get_session(organization_id=self.organization_id) as session:
            count = 0
            for opp in raw_opportunities:
                sf_id = opp.get("Id", "")

                # Check if deal already exists
                result = await session.execute(
                    select(Deal).where(
                        Deal.organization_id == uuid.UUID(self.organization_id),
                        Deal.source_system == self.source_system,
                        Deal.source_id == sf_id,
                    )
                )
                existing = result.scalar_one_or_none()

                deal = await self._normalize_deal(
                    opp, existing_id=existing.id if existing else None
                )
                await session.merge(deal)
                count += 1
            await session.commit()

        return count

    async def _normalize_deal(
        self, sf_opp: dict[str, Any], existing_id: Optional[uuid.UUID] = None
    ) -> Deal:
        """Transform Salesforce Opportunity to our Deal model."""
        sf_id = sf_opp.get("Id", "")
        owner_id = await self._map_sf_owner_to_user(sf_opp.get("OwnerId"))
        account_id = await self._map_sf_account_to_our_account(sf_opp.get("AccountId"))

        # Parse amount
        amount: Optional[Decimal] = None
        if sf_opp.get("Amount") is not None:
            try:
                amount = Decimal(str(sf_opp["Amount"]))
            except (ValueError, TypeError):
                pass

        # Parse probability
        probability: Optional[int] = None
        if sf_opp.get("Probability") is not None:
            try:
                probability = int(sf_opp["Probability"])
            except (ValueError, TypeError):
                pass

        # Parse dates
        close_date = None
        if sf_opp.get("CloseDate"):
            try:
                close_date = datetime.strptime(sf_opp["CloseDate"], "%Y-%m-%d").date()
            except (ValueError, TypeError):
                pass

        created_date = None
        if sf_opp.get("CreatedDate"):
            try:
                dt = datetime.fromisoformat(
                    sf_opp["CreatedDate"].replace("Z", "+00:00")
                )
                created_date = dt.replace(tzinfo=None)
            except (ValueError, TypeError):
                pass

        last_modified_date = None
        if sf_opp.get("LastModifiedDate"):
            try:
                dt = datetime.fromisoformat(
                    sf_opp["LastModifiedDate"].replace("Z", "+00:00")
                )
                last_modified_date = dt.replace(tzinfo=None)
            except (ValueError, TypeError):
                pass

        return Deal(
            id=existing_id or uuid.uuid4(),
            organization_id=uuid.UUID(self.organization_id),
            source_system=self.source_system,
            source_id=sf_id,
            name=sf_opp.get("Name") or "Untitled Opportunity",
            account_id=account_id,
            owner_id=owner_id,
            amount=amount,
            stage=sf_opp.get("StageName"),
            probability=probability,
            close_date=close_date,
            created_date=created_date,
            last_modified_date=last_modified_date,
            visible_to_user_ids=[owner_id] if owner_id else [],
        )

    async def sync_accounts(self) -> int:
        """Sync all accounts from Salesforce."""
        soql = """
            SELECT Id, Name, Website, Industry, NumberOfEmployees,
                   AnnualRevenue, OwnerId, CreatedDate, LastModifiedDate
            FROM Account
        """

        raw_accounts = await self._query_soql(soql)

        async with get_session(organization_id=self.organization_id) as session:
            count = 0
            for acc in raw_accounts:
                sf_id = acc.get("Id", "")

                # Check if account already exists
                result = await session.execute(
                    select(Account).where(
                        Account.organization_id == uuid.UUID(self.organization_id),
                        Account.source_system == self.source_system,
                        Account.source_id == sf_id,
                    )
                )
                existing = result.scalar_one_or_none()

                account = await self._normalize_account(
                    acc, existing_id=existing.id if existing else None
                )
                await session.merge(account)
                count += 1
            await session.commit()

        return count

    async def _normalize_account(
        self, sf_acc: dict[str, Any], existing_id: Optional[uuid.UUID] = None
    ) -> Account:
        """Transform Salesforce Account to our Account model."""
        sf_id = sf_acc.get("Id", "")
        owner_id = await self._map_sf_owner_to_user(sf_acc.get("OwnerId"))

        # Extract domain from website
        domain: Optional[str] = None
        if sf_acc.get("Website"):
            website = sf_acc["Website"]
            domain = (
                website.replace("https://", "")
                .replace("http://", "")
                .split("/")[0]
            )

        # Parse employee count
        employee_count: Optional[int] = None
        if sf_acc.get("NumberOfEmployees") is not None:
            try:
                employee_count = int(sf_acc["NumberOfEmployees"])
            except (ValueError, TypeError):
                pass

        # Parse annual revenue
        annual_revenue: Optional[Decimal] = None
        if sf_acc.get("AnnualRevenue") is not None:
            try:
                annual_revenue = Decimal(str(sf_acc["AnnualRevenue"]))
            except (ValueError, TypeError):
                pass

        # Name is required - use domain or fallback if name is None/empty
        name = sf_acc.get("Name")
        if not name:
            name = domain or f"Account {sf_id}"

        return Account(
            id=existing_id or uuid.uuid4(),
            organization_id=uuid.UUID(self.organization_id),
            source_system=self.source_system,
            source_id=sf_id,
            name=name,
            domain=domain,
            industry=sf_acc.get("Industry"),
            employee_count=employee_count,
            annual_revenue=annual_revenue,
            owner_id=owner_id,
        )

    async def sync_contacts(self) -> int:
        """Sync all contacts from Salesforce."""
        soql = """
            SELECT Id, AccountId, FirstName, LastName, Name, Email,
                   Title, Phone, CreatedDate, LastModifiedDate
            FROM Contact
        """

        raw_contacts = await self._query_soql(soql)

        async with get_session(organization_id=self.organization_id) as session:
            count = 0
            for cont in raw_contacts:
                sf_id = cont.get("Id", "")

                # Check if contact already exists
                result = await session.execute(
                    select(Contact).where(
                        Contact.organization_id == uuid.UUID(self.organization_id),
                        Contact.source_system == self.source_system,
                        Contact.source_id == sf_id,
                    )
                )
                existing = result.scalar_one_or_none()

                contact = await self._normalize_contact(
                    cont, existing_id=existing.id if existing else None
                )
                await session.merge(contact)
                count += 1
            await session.commit()

        return count

    async def _normalize_contact(
        self, sf_cont: dict[str, Any], existing_id: Optional[uuid.UUID] = None
    ) -> Contact:
        """Transform Salesforce Contact to our Contact model."""
        sf_id = sf_cont.get("Id", "")
        account_id = await self._map_sf_account_to_our_account(
            sf_cont.get("AccountId")
        )

        # Combine first and last name, or use Name field
        first_name = sf_cont.get("FirstName") or ""
        last_name = sf_cont.get("LastName") or ""
        full_name = f"{first_name} {last_name}".strip()

        # Use Name field if available, otherwise constructed name
        if not full_name:
            full_name = sf_cont.get("Name") or ""

        # Use email or ID as fallback if no name
        if not full_name:
            full_name = sf_cont.get("Email") or f"Contact {sf_id}"

        return Contact(
            id=existing_id or uuid.uuid4(),
            organization_id=uuid.UUID(self.organization_id),
            source_system=self.source_system,
            source_id=sf_id,
            account_id=account_id,
            name=full_name,
            email=sf_cont.get("Email"),
            title=sf_cont.get("Title"),
            phone=sf_cont.get("Phone"),
        )

    async def sync_activities(self) -> int:
        """Sync Tasks and Events from Salesforce."""
        count = 0

        # Sync Tasks
        task_soql = """
            SELECT Id, WhatId, WhoId, Subject, Description,
                   ActivityDate, OwnerId, CreatedDate, LastModifiedDate
            FROM Task
        """

        try:
            raw_tasks = await self._query_soql(task_soql)

            async with get_session(organization_id=self.organization_id) as session:
                for task in raw_tasks:
                    sf_id = task.get("Id", "")

                    # Check if activity already exists
                    result = await session.execute(
                        select(Activity).where(
                            Activity.organization_id == uuid.UUID(self.organization_id),
                            Activity.source_system == self.source_system,
                            Activity.source_id == sf_id,
                        )
                    )
                    existing = result.scalar_one_or_none()

                    activity = await self._normalize_task(
                        task, existing_id=existing.id if existing else None
                    )
                    await session.merge(activity)
                    count += 1
                await session.commit()
        except httpx.HTTPStatusError:
            # Tasks might not be accessible
            pass

        # Sync Events
        event_soql = """
            SELECT Id, WhatId, WhoId, Subject, Description,
                   StartDateTime, EndDateTime, OwnerId, CreatedDate, LastModifiedDate
            FROM Event
        """

        try:
            raw_events = await self._query_soql(event_soql)

            async with get_session(organization_id=self.organization_id) as session:
                for event in raw_events:
                    sf_id = event.get("Id", "")

                    # Check if activity already exists
                    result = await session.execute(
                        select(Activity).where(
                            Activity.organization_id == uuid.UUID(self.organization_id),
                            Activity.source_system == self.source_system,
                            Activity.source_id == sf_id,
                        )
                    )
                    existing = result.scalar_one_or_none()

                    activity = await self._normalize_event(
                        event, existing_id=existing.id if existing else None
                    )
                    await session.merge(activity)
                    count += 1
                await session.commit()
        except httpx.HTTPStatusError:
            # Events might not be accessible
            pass

        return count

    async def _normalize_task(
        self, sf_task: dict[str, Any], existing_id: Optional[uuid.UUID] = None
    ) -> Activity:
        """Transform Salesforce Task to our Activity model."""
        sf_id = sf_task.get("Id", "")
        created_by_id = await self._map_sf_owner_to_user(sf_task.get("OwnerId"))

        activity_date: Optional[datetime] = None
        if sf_task.get("ActivityDate"):
            try:
                activity_date = datetime.strptime(sf_task["ActivityDate"], "%Y-%m-%d")
            except (ValueError, TypeError):
                pass

        return Activity(
            id=existing_id or uuid.uuid4(),
            organization_id=uuid.UUID(self.organization_id),
            source_system=self.source_system,
            source_id=sf_id,
            type="task",
            subject=sf_task.get("Subject"),
            description=sf_task.get("Description"),
            activity_date=activity_date,
            created_by_id=created_by_id,
        )

    async def _normalize_event(
        self, sf_event: dict[str, Any], existing_id: Optional[uuid.UUID] = None
    ) -> Activity:
        """Transform Salesforce Event to our Activity model."""
        sf_id = sf_event.get("Id", "")
        created_by_id = await self._map_sf_owner_to_user(sf_event.get("OwnerId"))

        activity_date: Optional[datetime] = None
        if sf_event.get("StartDateTime"):
            try:
                dt = datetime.fromisoformat(
                    sf_event["StartDateTime"].replace("Z", "+00:00")
                )
                activity_date = dt.replace(tzinfo=None)
            except (ValueError, TypeError):
                pass

        return Activity(
            id=existing_id or uuid.uuid4(),
            organization_id=uuid.UUID(self.organization_id),
            source_system=self.source_system,
            source_id=sf_id,
            type="meeting",
            subject=sf_event.get("Subject"),
            description=sf_event.get("Description"),
            activity_date=activity_date,
            created_by_id=created_by_id,
        )

    async def _map_sf_owner_to_user(
        self, sf_user_id: Optional[str]
    ) -> Optional[uuid.UUID]:
        """Map Salesforce user ID to our internal user ID."""
        if not sf_user_id:
            return None

        async with get_session(organization_id=self.organization_id) as session:
            # First try to match by salesforce_user_id within the organization
            result = await session.execute(
                select(User).where(
                    User.organization_id == uuid.UUID(self.organization_id),
                    User.salesforce_user_id == sf_user_id,
                )
            )
            user = result.scalar_one_or_none()

            if user:
                return user.id

            # Fallback: return first user in organization (for MVP)
            result = await session.execute(
                select(User).where(
                    User.organization_id == uuid.UUID(self.organization_id),
                )
            )
            user = result.scalars().first()
            return user.id if user else None

    async def _map_sf_account_to_our_account(
        self, sf_account_id: Optional[str]
    ) -> Optional[uuid.UUID]:
        """Map Salesforce account ID to our internal account ID."""
        if not sf_account_id:
            return None

        async with get_session(organization_id=self.organization_id) as session:
            result = await session.execute(
                select(Account).where(
                    Account.organization_id == uuid.UUID(self.organization_id),
                    Account.source_system == self.source_system,
                    Account.source_id == sf_account_id,
                )
            )
            account = result.scalar_one_or_none()
            return account.id if account else None

    async def fetch_deal(self, deal_id: str) -> dict[str, Any]:
        """Fetch single deal on-demand for real-time queries."""
        data = await self._make_request(
            "GET", f"/sobjects/Opportunity/{deal_id}"
        )
        deal = await self._normalize_deal(data)
        return deal.to_dict()
