"""
Salesforce connector implementation.

Responsibilities:
- Authenticate with Salesforce using OAuth token
- Fetch Opportunities, Accounts, Contacts, Activities
- Normalize Salesforce schema to our canonical schema
- Handle pagination and rate limits
- Upsert normalized data to database
"""

import uuid
from datetime import datetime
from typing import Any, Optional

from simple_salesforce import Salesforce
from sqlalchemy import select

from connectors.base import BaseConnector
from models.account import Account
from models.activity import Activity
from models.contact import Contact
from models.database import get_session
from models.deal import Deal
from models.user import User


class SalesforceConnector(BaseConnector):
    """Connector for Salesforce CRM."""

    source_system = "salesforce"

    async def _get_client(self) -> Salesforce:
        """Initialize Salesforce client with OAuth token."""
        token, instance_url = await self.get_oauth_token()
        return Salesforce(instance_url=instance_url, session_id=token)

    async def _map_sf_user_to_our_user(
        self, sf_user_id: Optional[str]
    ) -> Optional[uuid.UUID]:
        """Map Salesforce user ID to our internal user ID."""
        if not sf_user_id:
            return None

        async with get_session() as session:
            result = await session.execute(
                select(User).where(User.salesforce_user_id == sf_user_id)
            )
            user = result.scalar_one_or_none()
            return user.id if user else None

    async def _map_sf_account_to_our_account(
        self, sf_account_id: Optional[str]
    ) -> Optional[uuid.UUID]:
        """Map Salesforce account ID to our internal account ID."""
        if not sf_account_id:
            return None

        async with get_session() as session:
            result = await session.execute(
                select(Account).where(
                    Account.source_id == sf_account_id,
                    Account.organization_id == uuid.UUID(self.organization_id),
                )
            )
            account = result.scalar_one_or_none()
            return account.id if account else None

    async def sync_deals(self) -> int:
        """
        Sync all opportunities from Salesforce.

        Query opportunities modified in last 24 hours.
        Normalize to Deal model.
        Upsert to database.
        """
        sf = await self._get_client()

        # Query Salesforce
        query = """
            SELECT Id, Name, AccountId, OwnerId, Amount, StageName,
                   Probability, CloseDate, CreatedDate, LastModifiedDate
            FROM Opportunity
            WHERE LastModifiedDate > LAST_N_DAYS:1
        """

        raw_opportunities = sf.query_all(query)["records"]

        # Normalize and upsert to database
        async with get_session() as session:
            count = 0
            for opp in raw_opportunities:
                deal = await self._normalize_deal(opp)
                await session.merge(deal)
                count += 1
            await session.commit()

        return count

    async def _normalize_deal(self, sf_opp: dict[str, Any]) -> Deal:
        """Transform Salesforce Opportunity to our Deal model."""
        owner_id = await self._map_sf_user_to_our_user(sf_opp.get("OwnerId"))
        account_id = await self._map_sf_account_to_our_account(sf_opp.get("AccountId"))

        # Parse dates
        close_date = None
        if sf_opp.get("CloseDate"):
            close_date = datetime.strptime(sf_opp["CloseDate"], "%Y-%m-%d").date()

        created_date = None
        if sf_opp.get("CreatedDate"):
            created_date = datetime.fromisoformat(
                sf_opp["CreatedDate"].replace("Z", "+00:00")
            )

        last_modified_date = None
        if sf_opp.get("LastModifiedDate"):
            last_modified_date = datetime.fromisoformat(
                sf_opp["LastModifiedDate"].replace("Z", "+00:00")
            )

        return Deal(
            id=uuid.uuid4(),  # Generate new UUID for our system
            organization_id=uuid.UUID(self.organization_id),
            source_system="salesforce",
            source_id=sf_opp["Id"],
            name=sf_opp["Name"],
            account_id=account_id,
            owner_id=owner_id,
            amount=sf_opp.get("Amount"),
            stage=sf_opp.get("StageName"),
            probability=sf_opp.get("Probability"),
            close_date=close_date,
            created_date=created_date,
            last_modified_date=last_modified_date,
            visible_to_user_ids=[owner_id] if owner_id else [],
        )

    async def sync_accounts(self) -> int:
        """Sync all accounts from Salesforce."""
        sf = await self._get_client()

        query = """
            SELECT Id, Name, Website, Industry, NumberOfEmployees,
                   AnnualRevenue, OwnerId, LastModifiedDate
            FROM Account
            WHERE LastModifiedDate > LAST_N_DAYS:1
        """

        raw_accounts = sf.query_all(query)["records"]

        async with get_session() as session:
            count = 0
            for acc in raw_accounts:
                account = await self._normalize_account(acc)
                await session.merge(account)
                count += 1
            await session.commit()

        return count

    async def _normalize_account(self, sf_acc: dict[str, Any]) -> Account:
        """Transform Salesforce Account to our Account model."""
        owner_id = await self._map_sf_user_to_our_user(sf_acc.get("OwnerId"))

        # Extract domain from website
        domain = None
        if sf_acc.get("Website"):
            website = sf_acc["Website"]
            domain = website.replace("https://", "").replace("http://", "").split("/")[0]

        return Account(
            id=uuid.uuid4(),
            organization_id=uuid.UUID(self.organization_id),
            source_system="salesforce",
            source_id=sf_acc["Id"],
            name=sf_acc["Name"],
            domain=domain,
            industry=sf_acc.get("Industry"),
            employee_count=sf_acc.get("NumberOfEmployees"),
            annual_revenue=sf_acc.get("AnnualRevenue"),
            owner_id=owner_id,
        )

    async def sync_contacts(self) -> int:
        """Sync all contacts from Salesforce."""
        sf = await self._get_client()

        query = """
            SELECT Id, AccountId, Name, Email, Title, Phone, LastModifiedDate
            FROM Contact
            WHERE LastModifiedDate > LAST_N_DAYS:1
        """

        raw_contacts = sf.query_all(query)["records"]

        async with get_session() as session:
            count = 0
            for cont in raw_contacts:
                contact = await self._normalize_contact(cont)
                await session.merge(contact)
                count += 1
            await session.commit()

        return count

    async def _normalize_contact(self, sf_cont: dict[str, Any]) -> Contact:
        """Transform Salesforce Contact to our Contact model."""
        account_id = await self._map_sf_account_to_our_account(sf_cont.get("AccountId"))

        return Contact(
            id=uuid.uuid4(),
            organization_id=uuid.UUID(self.organization_id),
            source_system="salesforce",
            source_id=sf_cont["Id"],
            account_id=account_id,
            name=sf_cont.get("Name"),
            email=sf_cont.get("Email"),
            title=sf_cont.get("Title"),
            phone=sf_cont.get("Phone"),
        )

    async def sync_activities(self) -> int:
        """Sync Tasks and Events from Salesforce."""
        sf = await self._get_client()

        # Sync Tasks
        task_query = """
            SELECT Id, WhatId, WhoId, Subject, Description, ActivityDate, OwnerId
            FROM Task
            WHERE LastModifiedDate > LAST_N_DAYS:1
        """

        raw_tasks = sf.query_all(task_query)["records"]

        # Sync Events
        event_query = """
            SELECT Id, WhatId, WhoId, Subject, Description, StartDateTime, OwnerId
            FROM Event
            WHERE LastModifiedDate > LAST_N_DAYS:1
        """

        raw_events = sf.query_all(event_query)["records"]

        async with get_session() as session:
            count = 0

            for task in raw_tasks:
                activity = await self._normalize_task(task)
                await session.merge(activity)
                count += 1

            for event in raw_events:
                activity = await self._normalize_event(event)
                await session.merge(activity)
                count += 1

            await session.commit()

        return count

    async def _normalize_task(self, sf_task: dict[str, Any]) -> Activity:
        """Transform Salesforce Task to our Activity model."""
        created_by_id = await self._map_sf_user_to_our_user(sf_task.get("OwnerId"))

        activity_date = None
        if sf_task.get("ActivityDate"):
            activity_date = datetime.strptime(sf_task["ActivityDate"], "%Y-%m-%d")

        return Activity(
            id=uuid.uuid4(),
            organization_id=uuid.UUID(self.organization_id),
            source_system="salesforce",
            source_id=sf_task["Id"],
            type="task",
            subject=sf_task.get("Subject"),
            description=sf_task.get("Description"),
            activity_date=activity_date,
            created_by_id=created_by_id,
        )

    async def _normalize_event(self, sf_event: dict[str, Any]) -> Activity:
        """Transform Salesforce Event to our Activity model."""
        created_by_id = await self._map_sf_user_to_our_user(sf_event.get("OwnerId"))

        activity_date = None
        if sf_event.get("StartDateTime"):
            activity_date = datetime.fromisoformat(
                sf_event["StartDateTime"].replace("Z", "+00:00")
            )

        return Activity(
            id=uuid.uuid4(),
            organization_id=uuid.UUID(self.organization_id),
            source_system="salesforce",
            source_id=sf_event["Id"],
            type="meeting",
            subject=sf_event.get("Subject"),
            description=sf_event.get("Description"),
            activity_date=activity_date,
            created_by_id=created_by_id,
        )

    async def fetch_deal(self, deal_id: str) -> dict[str, Any]:
        """Fetch single deal on-demand for real-time queries."""
        sf = await self._get_client()
        raw_opp = sf.Opportunity.get(deal_id)
        deal = await self._normalize_deal(raw_opp)
        return deal.to_dict()
