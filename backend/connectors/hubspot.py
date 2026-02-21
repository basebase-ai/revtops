"""
HubSpot connector implementation.

Responsibilities:
- Authenticate with HubSpot using OAuth token
- Fetch Deals, Contacts, Companies
- Normalize HubSpot schema to our canonical schema
- Handle pagination and rate limits
- Upsert normalized data to database
"""

import asyncio
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from api.websockets import broadcast_sync_progress
from connectors.base import BaseConnector
from connectors.registry import (
    AuthType, Capability, ConnectorMeta, ConnectorScope, WriteOperation,
)
from models.account import Account
from models.activity import Activity
from models.contact import Contact
from models.database import get_session
from models.deal import Deal
from models.goal import Goal
from models.org_member import OrgMember
from models.pipeline import Pipeline, PipelineStage
from models.slack_user_mapping import SlackUserMapping
from models.user import User

HUBSPOT_API_BASE = "https://api.hubapi.com"


class HubSpotConnector(BaseConnector):
    """Connector for HubSpot CRM."""

    source_system = "hubspot"
    meta = ConnectorMeta(
        name="HubSpot",
        slug="hubspot",
        auth_type=AuthType.OAUTH2,
        scope=ConnectorScope.ORGANIZATION,
        entity_types=["deals", "accounts", "contacts", "activities", "pipelines", "goals"],
        capabilities=[Capability.SYNC, Capability.QUERY, Capability.WRITE],
        write_operations=[
            WriteOperation(
                name="create_deal", entity_type="deal",
                description="Create a new deal in HubSpot",
                parameters=[
                    {"name": "dealname", "type": "string", "required": True, "description": "Deal name"},
                    {"name": "amount", "type": "number", "required": False, "description": "Deal amount"},
                    {"name": "dealstage", "type": "string", "required": False, "description": "Pipeline stage ID"},
                    {"name": "closedate", "type": "string", "required": False, "description": "Expected close date (ISO 8601)"},
                    {"name": "pipeline", "type": "string", "required": False, "description": "Pipeline ID"},
                ],
            ),
            WriteOperation(
                name="update_deal", entity_type="deal",
                description="Update an existing deal in HubSpot",
                parameters=[
                    {"name": "id", "type": "string", "required": True, "description": "HubSpot deal ID (source_id from deals table)"},
                    {"name": "hubspot_owner_id", "type": "string", "required": False, "description": "HubSpot numeric owner ID (from user_mappings_for_identity.external_userid where source='hubspot')"},
                    {"name": "dealname", "type": "string", "required": False, "description": "Deal name"},
                    {"name": "amount", "type": "number", "required": False, "description": "Deal amount"},
                    {"name": "dealstage", "type": "string", "required": False, "description": "Pipeline stage ID"},
                    {"name": "closedate", "type": "string", "required": False, "description": "Expected close date (ISO 8601)"},
                ],
            ),
            WriteOperation(
                name="create_contact", entity_type="contact",
                description="Create a new contact in HubSpot",
                parameters=[
                    {"name": "email", "type": "string", "required": True, "description": "Email address"},
                    {"name": "firstname", "type": "string", "required": False, "description": "First name"},
                    {"name": "lastname", "type": "string", "required": False, "description": "Last name"},
                    {"name": "company", "type": "string", "required": False, "description": "Company name"},
                    {"name": "jobtitle", "type": "string", "required": False, "description": "Job title"},
                    {"name": "phone", "type": "string", "required": False, "description": "Phone number"},
                ],
            ),
            WriteOperation(
                name="update_contact", entity_type="contact",
                description="Update an existing contact in HubSpot",
                parameters=[
                    {"name": "id", "type": "string", "required": True, "description": "HubSpot contact ID"},
                    {"name": "email", "type": "string", "required": False, "description": "Email address"},
                    {"name": "firstname", "type": "string", "required": False, "description": "First name"},
                    {"name": "lastname", "type": "string", "required": False, "description": "Last name"},
                    {"name": "company", "type": "string", "required": False, "description": "Company name"},
                    {"name": "jobtitle", "type": "string", "required": False, "description": "Job title"},
                ],
            ),
            WriteOperation(
                name="create_company", entity_type="company",
                description="Create a new company in HubSpot",
                parameters=[
                    {"name": "name", "type": "string", "required": True, "description": "Company name"},
                    {"name": "domain", "type": "string", "required": False, "description": "Company domain"},
                    {"name": "industry", "type": "string", "required": False, "description": "Industry"},
                    {"name": "numberofemployees", "type": "integer", "required": False, "description": "Number of employees"},
                ],
            ),
            WriteOperation(
                name="update_company", entity_type="company",
                description="Update an existing company in HubSpot",
                parameters=[
                    {"name": "id", "type": "string", "required": True, "description": "HubSpot company ID"},
                    {"name": "name", "type": "string", "required": False, "description": "Company name"},
                    {"name": "domain", "type": "string", "required": False, "description": "Company domain"},
                    {"name": "industry", "type": "string", "required": False, "description": "Industry"},
                ],
            ),
        ],
        nango_integration_id="hubspot",
        description="HubSpot CRM – deals, contacts, companies, and activities",
    )

    def __init__(self, organization_id: str, user_id: Optional[str] = None) -> None:
        """Initialize connector with owner and pipeline caches."""
        super().__init__(organization_id, user_id=user_id)
        # Cache for HubSpot owner ID -> internal user ID mapping
        self._owner_cache: dict[str, Optional[uuid.UUID]] = {}
        # Cache for HubSpot pipeline ID -> internal pipeline ID mapping
        self._pipeline_cache: dict[str, uuid.UUID] = {}
        # Pre-fetched HubSpot owner ID -> email (populated by _ensure_owner_email_cache)
        self._owner_email_cache: dict[str, str] = {}
        # HubSpot owner ID -> full owner dict (email, firstName, lastName) for proactive user creation
        self._owner_detail_cache: dict[str, dict[str, Any]] = {}
        self._owner_email_cache_loaded: bool = False

    async def sync_all(self) -> dict[str, int]:
        """Run all sync operations with progress broadcasting."""
        # Broadcast immediately so the UI shows activity
        await broadcast_sync_progress(
            organization_id=self.organization_id,
            provider=self.source_system,
            count=0,
            status="syncing",
            step="preparing",
        )

        # Call parent sync_all
        result = await super().sync_all()
        
        # Calculate total synced items
        total = sum(result.values())
        
        # Broadcast completion
        await broadcast_sync_progress(
            organization_id=self.organization_id,
            provider=self.source_system,
            count=total,
            status="completed",
        )
        
        return result

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
        _max_retries: int = 5,
    ) -> dict[str, Any]:
        """Make an authenticated request to HubSpot API with 429 retry."""
        headers: dict[str, str] = await self._get_headers()
        url: str = f"{HUBSPOT_API_BASE}{endpoint}"

        last_exc: Optional[httpx.HTTPStatusError] = None
        for attempt in range(_max_retries + 1):
            async with httpx.AsyncClient() as client:
                response: httpx.Response = await client.request(
                    method=method,
                    url=url,
                    headers=headers,
                    params=params,
                    json=json_data,
                    timeout=30.0,
                )

                # Retry on 429 rate limit
                if response.status_code == 429 and attempt < _max_retries:
                    retry_after: float = float(response.headers.get("Retry-After", "10"))
                    wait_secs: float = min(retry_after, 30.0)
                    print(f"[HubSpot] 429 rate limited on {endpoint}, retrying in {wait_secs}s (attempt {attempt + 1}/{_max_retries})")
                    await asyncio.sleep(wait_secs)
                    continue

                # If error, try to get detailed error message from HubSpot
                if response.status_code >= 400:
                    error_detail: str = ""
                    try:
                        error_body: dict[str, Any] = response.json()
                        # HubSpot error format: {"message": "...", "errors": [...]}
                        error_detail = error_body.get("message", "")
                        if error_body.get("errors"):
                            error_details: list[str] = [e.get("message", str(e)) for e in error_body["errors"]]
                            error_detail = f"{error_detail}: {'; '.join(error_details)}"
                    except Exception:
                        error_detail = response.text[:500] if response.text else ""

                    last_exc = httpx.HTTPStatusError(
                        f"HubSpot API error ({response.status_code}): {error_detail}",
                        request=response.request,
                        response=response,
                    )
                    raise last_exc

                return response.json()

        # Should not reach here, but satisfy type checker
        assert last_exc is not None
        raise last_exc

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
        page_count = 0

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
            page_count += 1
            
            # Log first page for debugging - include raw response keys on empty
            if page_count == 1:
                print(f"[HubSpot] {endpoint}: first page returned {len(results)} results")
                if len(results) == 0:
                    print(f"[HubSpot] {endpoint}: empty response, keys in data: {list(data.keys())}")
                    # Check if there's an error or message field
                    if "message" in data:
                        print(f"[HubSpot] {endpoint}: message: {data.get('message')}")
                    if "status" in data:
                        print(f"[HubSpot] {endpoint}: status: {data.get('status')}")

            # Check for pagination
            paging = data.get("paging", {})
            next_link = paging.get("next", {})
            after = next_link.get("after")

            if not after:
                break
        
        if page_count > 1:
            print(f"[HubSpot] {endpoint}: fetched {page_count} pages, {len(all_results)} total results")

        return all_results

    async def sync_pipelines(self) -> int:
        """
        Sync all deal pipelines and stages from HubSpot.

        Should be called before sync_deals to ensure pipeline_id can be set.
        Populates self._pipeline_cache for use by sync_deals.
        """
        pipelines_data = await self.get_pipelines()

        async with get_session(organization_id=self.organization_id) as session:
            count = 0
            for hs_pipeline in pipelines_data:
                hs_pipeline_id = hs_pipeline.get("id", "")

                # Check if pipeline already exists
                result = await session.execute(
                    select(Pipeline).where(
                        Pipeline.organization_id == uuid.UUID(self.organization_id),
                        Pipeline.source_system == self.source_system,
                        Pipeline.source_id == hs_pipeline_id,
                    )
                )
                existing = result.scalar_one_or_none()

                pipeline = Pipeline(
                    id=existing.id if existing else uuid.uuid4(),
                    organization_id=uuid.UUID(self.organization_id),
                    source_system=self.source_system,
                    source_id=hs_pipeline_id,
                    name=hs_pipeline.get("label") or "Unnamed Pipeline",
                    display_order=hs_pipeline.get("display_order"),
                    is_default=(hs_pipeline_id == "default"),
                    synced_at=datetime.utcnow(),
                )
                await session.merge(pipeline)

                # Cache the mapping for sync_deals
                self._pipeline_cache[hs_pipeline_id] = pipeline.id

                # Sync stages for this pipeline
                for hs_stage in hs_pipeline.get("stages", []):
                    hs_stage_id = hs_stage.get("id", "")

                    # Check if stage already exists
                    stage_result = await session.execute(
                        select(PipelineStage).where(
                            PipelineStage.pipeline_id == pipeline.id,
                            PipelineStage.source_id == hs_stage_id,
                        )
                    )
                    existing_stage = stage_result.scalar_one_or_none()

                    # Parse metadata for closed won/lost flags
                    metadata = hs_stage.get("metadata", {})
                    is_closed_won = metadata.get("isClosed") == "true" and metadata.get("probability") == "1.0"
                    is_closed_lost = metadata.get("isClosed") == "true" and metadata.get("probability") == "0.0"

                    # Parse probability
                    probability: Optional[int] = None
                    if metadata.get("probability"):
                        try:
                            probability = int(float(metadata["probability"]) * 100)
                        except (ValueError, TypeError):
                            pass

                    stage = PipelineStage(
                        id=existing_stage.id if existing_stage else uuid.uuid4(),
                        pipeline_id=pipeline.id,
                        source_id=hs_stage_id,
                        name=hs_stage.get("label") or "Unnamed Stage",
                        display_order=hs_stage.get("display_order"),
                        probability=probability,
                        is_closed_won=is_closed_won,
                        is_closed_lost=is_closed_lost,
                        synced_at=datetime.utcnow(),
                    )
                    await session.merge(stage)

                count += 1
            await session.commit()

        return count

    async def _ensure_pipeline_cache(self) -> None:
        """Load pipeline cache from database if not already loaded."""
        if self._pipeline_cache:
            return

        async with get_session(organization_id=self.organization_id) as session:
            result = await session.execute(
                select(Pipeline).where(
                    Pipeline.organization_id == uuid.UUID(self.organization_id),
                    Pipeline.source_system == self.source_system,
                )
            )
            pipelines = result.scalars().all()
            for pipeline in pipelines:
                self._pipeline_cache[pipeline.source_id] = pipeline.id

    async def sync_deals(self) -> int:
        """
        Sync all deals from HubSpot.

        HubSpot deal properties:
        - dealname, amount, dealstage, closedate, createdate, hs_lastmodifieddate
        - hubspot_owner_id, associated company/contact
        """
        await broadcast_sync_progress(
            organization_id=self.organization_id,
            provider=self.source_system,
            count=0,
            status="syncing",
            step="fetching deals",
        )
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
            "/crm/v3/objects/deals",
            properties=properties,
            associations=["companies"],
        )

        # Pre-load owner email cache
        await self._ensure_owner_email_cache()

        # Build a map of HubSpot company IDs -> internal account IDs for deal-to-account linking
        hs_company_id_to_account_id: dict[str, uuid.UUID] = {}
        async with get_session(organization_id=self.organization_id) as session:
            result = await session.execute(
                select(Account).where(
                    Account.organization_id == uuid.UUID(self.organization_id),
                    Account.source_system == self.source_system,
                )
            )
            accounts: list[Account] = list(result.scalars().all())
            for account in accounts:
                hs_company_id_to_account_id[account.source_id] = account.id
        print(f"[HubSpot] Pre-loaded {len(hs_company_id_to_account_id)} account IDs for deal-to-account linking")

        # Build existing source_id -> UUID map
        existing_map: dict[str, uuid.UUID] = {}
        async with get_session(organization_id=self.organization_id) as session:
            result = await session.execute(
                select(Deal.source_id, Deal.id).where(
                    Deal.organization_id == uuid.UUID(self.organization_id),
                    Deal.source_system == self.source_system,
                )
            )
            for row in result.all():
                existing_map[row[0]] = row[1]
        print(f"[HubSpot] Pre-loaded {len(existing_map)} existing deal IDs")

        # Build all row dicts in memory
        rows: list[dict[str, Any]] = []
        for raw_deal in raw_deals:
            # Extract associated company ID from associations
            deal_account_id: Optional[uuid.UUID] = None
            associations = raw_deal.get("associations", {})
            companies_assoc = associations.get("companies", {})
            company_results: list[dict[str, Any]] = companies_assoc.get("results", [])
            if company_results:
                hs_company_id: Optional[str] = company_results[0].get("id")
                if hs_company_id:
                    deal_account_id = hs_company_id_to_account_id.get(hs_company_id)

            deal: Deal = await self._normalize_deal(
                raw_deal,
                existing_id=existing_map.get(raw_deal.get("id", "")),
                account_id=deal_account_id,
            )
            rows.append({
                "id": deal.id, "organization_id": deal.organization_id,
                "source_system": deal.source_system, "source_id": deal.source_id,
                "name": deal.name, "amount": deal.amount, "stage": deal.stage,
                "pipeline_id": deal.pipeline_id, "close_date": deal.close_date,
                "created_date": deal.created_date,
                "last_modified_date": deal.last_modified_date,
                "owner_id": deal.owner_id,
                "account_id": deal.account_id,
                "visible_to_user_ids": deal.visible_to_user_ids,
                "custom_fields": deal.custom_fields,
                "synced_at": datetime.utcnow(), "sync_status": "synced",
            })

        # Bulk upsert in batches
        BATCH_SIZE: int = 500
        update_cols: list[str] = [
            "name", "amount", "stage", "pipeline_id", "close_date",
            "created_date", "last_modified_date", "owner_id", "account_id",
            "visible_to_user_ids", "custom_fields", "synced_at",
        ]
        async with get_session(organization_id=self.organization_id) as session:
            for i in range(0, len(rows), BATCH_SIZE):
                batch: list[dict[str, Any]] = rows[i : i + BATCH_SIZE]
                stmt = pg_insert(Deal).values(batch)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["id"],
                    set_={col: stmt.excluded[col] for col in update_cols},
                )
                await session.execute(stmt)
                await session.commit()
                count: int = i + len(batch)
                await broadcast_sync_progress(
                    organization_id=self.organization_id,
                    provider=self.source_system,
                    count=count,
                    status="syncing",
                    step="deals",
                )
                print(f"[HubSpot] Deals: {count}/{len(rows)}")
        print(f"[HubSpot] Committed {len(rows)} deals")

        return len(rows)

    async def _normalize_deal(
        self,
        hs_deal: dict[str, Any],
        existing_id: Optional[uuid.UUID] = None,
        account_id: Optional[uuid.UUID] = None,
    ) -> Deal:
        """Transform HubSpot Deal to our Deal model."""
        props = hs_deal.get("properties", {})
        hs_id = hs_deal.get("id", "")

        # Map HubSpot owner to our user
        owner_id = await self._map_hs_owner_to_user(props.get("hubspot_owner_id"))

        # Map HubSpot pipeline to our pipeline
        await self._ensure_pipeline_cache()
        hs_pipeline_id = props.get("pipeline")
        pipeline_id: Optional[uuid.UUID] = None
        if hs_pipeline_id:
            pipeline_id = self._pipeline_cache.get(hs_pipeline_id)

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
            pipeline_id=pipeline_id,
            close_date=close_date,
            created_date=created_date,
            last_modified_date=last_modified,
            owner_id=owner_id,
            account_id=account_id,
            visible_to_user_ids=[owner_id] if owner_id else [],
            custom_fields={"pipeline": hs_pipeline_id},  # Keep source ID for reference
        )

    async def sync_accounts(self) -> int:
        """Sync all companies from HubSpot as accounts."""
        await broadcast_sync_progress(
            organization_id=self.organization_id,
            provider=self.source_system,
            count=0,
            status="syncing",
            step="fetching accounts",
        )
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

        # Pre-load owner email cache so _normalize_account doesn't trigger per-row fetches
        await self._ensure_owner_email_cache()

        # Build existing source_id -> UUID map
        existing_map: dict[str, uuid.UUID] = {}
        async with get_session(organization_id=self.organization_id) as session:
            result = await session.execute(
                select(Account.source_id, Account.id).where(
                    Account.organization_id == uuid.UUID(self.organization_id),
                    Account.source_system == self.source_system,
                )
            )
            for row in result.all():
                existing_map[row[0]] = row[1]
        print(f"[HubSpot] Pre-loaded {len(existing_map)} existing account IDs")

        # Build all row dicts in memory
        rows: list[dict[str, Any]] = []
        for raw_company in raw_companies:
            account: Account = await self._normalize_account(
                raw_company, existing_id=existing_map.get(raw_company.get("id", ""))
            )
            rows.append({
                "id": account.id, "organization_id": account.organization_id,
                "source_system": account.source_system, "source_id": account.source_id,
                "name": account.name, "domain": account.domain,
                "industry": account.industry, "employee_count": account.employee_count,
                "annual_revenue": account.annual_revenue, "owner_id": account.owner_id,
                "synced_at": datetime.utcnow(), "sync_status": "synced",
            })

        # Bulk upsert in batches (single SQL per batch instead of per-row)
        BATCH_SIZE: int = 500
        update_cols: list[str] = [
            "name", "domain", "industry", "employee_count",
            "annual_revenue", "owner_id", "synced_at",
        ]
        async with get_session(organization_id=self.organization_id) as session:
            for i in range(0, len(rows), BATCH_SIZE):
                batch: list[dict[str, Any]] = rows[i : i + BATCH_SIZE]
                stmt = pg_insert(Account).values(batch)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["id"],
                    set_={col: stmt.excluded[col] for col in update_cols},
                )
                await session.execute(stmt)
                await session.commit()
                count: int = i + len(batch)
                await broadcast_sync_progress(
                    organization_id=self.organization_id,
                    provider=self.source_system,
                    count=count,
                    status="syncing",
                    step="accounts",
                )
                print(f"[HubSpot] Accounts: {count}/{len(rows)}")
        print(f"[HubSpot] Committed {len(rows)} accounts")

        return len(rows)

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
        await broadcast_sync_progress(
            organization_id=self.organization_id,
            provider=self.source_system,
            count=0,
            status="syncing",
            step="fetching contacts",
        )
        # Only request standard properties that exist in all HubSpot instances
        # Removed 'company' as it's a custom/optional text field
        properties = [
            "firstname",
            "lastname",
            "email",
            "jobtitle",
            "phone",
            "hubspot_owner_id",
            "createdate",
            "hs_lastmodifieddate",
        ]

        # Fetch contacts with company associations
        print(f"[HubSpot] Fetching contacts for org {self.organization_id}...")
        try:
            raw_contacts = await self._paginate_results(
                "/crm/v3/objects/contacts",
                properties=properties,
                associations=["companies"],
            )
            print(f"[HubSpot] Fetched {len(raw_contacts)} contacts from API")
        except Exception as e:
            print(f"[HubSpot] ERROR fetching contacts: {e}")
            raise

        # Build a map of HubSpot company IDs to internal account IDs
        hs_company_id_to_account_id: dict[str, uuid.UUID] = {}
        async with get_session(organization_id=self.organization_id) as session:
            result = await session.execute(
                select(Account).where(
                    Account.organization_id == uuid.UUID(self.organization_id),
                    Account.source_system == self.source_system,
                )
            )
            accounts = result.scalars().all()
            for account in accounts:
                hs_company_id_to_account_id[account.source_id] = account.id

        # Build existing source_id -> UUID map
        existing_map: dict[str, uuid.UUID] = {}
        async with get_session(organization_id=self.organization_id) as session:
            result = await session.execute(
                select(Contact.source_id, Contact.id).where(
                    Contact.organization_id == uuid.UUID(self.organization_id),
                    Contact.source_system == self.source_system,
                )
            )
            for row in result.all():
                existing_map[row[0]] = row[1]
        print(f"[HubSpot] Pre-loaded {len(existing_map)} existing contact IDs")

        # Build all row dicts in memory
        org_uuid: uuid.UUID = uuid.UUID(self.organization_id)
        rows: list[dict[str, Any]] = []
        for raw_contact in raw_contacts:
            hs_id: str = raw_contact.get("id", "")

            # Extract associated company ID
            account_id: Optional[uuid.UUID] = None
            associations = raw_contact.get("associations", {})
            companies_assoc = associations.get("companies", {})
            company_results: list[dict[str, Any]] = companies_assoc.get("results", [])
            if company_results:
                hs_company_id: Optional[str] = company_results[0].get("id")
                if hs_company_id:
                    account_id = hs_company_id_to_account_id.get(hs_company_id)

            contact: Contact = self._normalize_contact(
                raw_contact,
                existing_id=existing_map.get(hs_id),
                account_id=account_id,
            )
            rows.append({
                "id": contact.id, "organization_id": org_uuid,
                "source_system": self.source_system, "source_id": hs_id,
                "name": contact.name, "email": contact.email,
                "title": contact.title, "phone": contact.phone,
                "account_id": contact.account_id,
                "synced_at": datetime.utcnow(), "sync_status": "synced",
            })

        # Bulk upsert in batches
        BATCH_SIZE: int = 500
        update_cols: list[str] = [
            "name", "email", "title", "phone", "account_id", "synced_at",
        ]
        async with get_session(organization_id=self.organization_id) as session:
            for i in range(0, len(rows), BATCH_SIZE):
                batch: list[dict[str, Any]] = rows[i : i + BATCH_SIZE]
                stmt = pg_insert(Contact).values(batch)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["id"],
                    set_={col: stmt.excluded[col] for col in update_cols},
                )
                await session.execute(stmt)
                await session.commit()
                count: int = i + len(batch)
                await broadcast_sync_progress(
                    organization_id=self.organization_id,
                    provider=self.source_system,
                    count=count,
                    status="syncing",
                    step="contacts",
                )
                print(f"[HubSpot] Contacts: {count}/{len(rows)}")
        print(f"[HubSpot] Committed {len(rows)} contacts")

        return len(rows)

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

        # Truncate fields to match DB column limits
        email_val: Optional[str] = props.get("email")
        if email_val:
            email_val = email_val[:255]
        title_val: Optional[str] = props.get("jobtitle")
        if title_val:
            title_val = title_val[:255]
        phone_val: Optional[str] = props.get("phone")
        if phone_val:
            phone_val = phone_val[:50]

        return Contact(
            id=existing_id or uuid.uuid4(),
            organization_id=uuid.UUID(self.organization_id),
            source_system=self.source_system,
            source_id=hs_id,
            name=full_name[:255] if full_name else full_name,
            email=email_val,
            title=title_val,
            phone=phone_val,
            account_id=account_id,
        )

    async def sync_activities(self) -> int:
        """Sync engagements (calls, emails, meetings, notes) from HubSpot.

        Each HubSpot engagement can be associated with deals, contacts, and
        companies.  We request those associations from the API and resolve them
        to internal FK UUIDs so that activities are properly linked.
        """
        total_count: int = 0
        org_uuid: uuid.UUID = uuid.UUID(self.organization_id)

        # -- Pre-build lookup maps (HS source_id → internal UUID) -----------
        hs_deal_id_to_deal_id: dict[str, uuid.UUID] = {}
        hs_contact_id_to_contact_id: dict[str, uuid.UUID] = {}
        hs_company_id_to_account_id: dict[str, uuid.UUID] = {}

        async with get_session(organization_id=self.organization_id) as session:
            # Deals
            result = await session.execute(
                select(Deal.source_id, Deal.id).where(
                    Deal.organization_id == org_uuid,
                    Deal.source_system == self.source_system,
                )
            )
            for row in result.all():
                hs_deal_id_to_deal_id[row[0]] = row[1]

            # Contacts
            result = await session.execute(
                select(Contact.source_id, Contact.id).where(
                    Contact.organization_id == org_uuid,
                    Contact.source_system == self.source_system,
                )
            )
            for row in result.all():
                hs_contact_id_to_contact_id[row[0]] = row[1]

            # Accounts (companies)
            result = await session.execute(
                select(Account.source_id, Account.id).where(
                    Account.organization_id == org_uuid,
                    Account.source_system == self.source_system,
                )
            )
            for row in result.all():
                hs_company_id_to_account_id[row[0]] = row[1]

        print(
            f"[HubSpot] Activity association maps: "
            f"{len(hs_deal_id_to_deal_id)} deals, "
            f"{len(hs_contact_id_to_contact_id)} contacts, "
            f"{len(hs_company_id_to_account_id)} accounts"
        )

        # -- Sync each engagement type -------------------------------------
        for engagement_type in ["calls", "emails", "meetings", "notes"]:
            properties: list[str] = ["hs_timestamp", "hs_call_title", "hs_call_body"]
            if engagement_type == "emails":
                properties = ["hs_timestamp", "hs_email_subject", "hs_email_text"]
            elif engagement_type == "meetings":
                properties = ["hs_timestamp", "hs_meeting_title", "hs_meeting_body"]
            elif engagement_type == "notes":
                properties = ["hs_timestamp", "hs_note_body"]

            try:
                raw_engagements: list[dict[str, Any]] = await self._paginate_results(
                    f"/crm/v3/objects/{engagement_type}",
                    properties=properties,
                    associations=["deals", "contacts", "companies"],
                )

                if not raw_engagements:
                    continue

                # Build existing source_id -> UUID map
                existing_map: dict[str, uuid.UUID] = {}
                async with get_session(organization_id=self.organization_id) as session:
                    result = await session.execute(
                        select(Activity.source_id, Activity.id).where(
                            Activity.organization_id == org_uuid,
                            Activity.source_system == self.source_system,
                            Activity.source_id.isnot(None),
                        )
                    )
                    for row in result.all():
                        existing_map[row[0]] = row[1]
                print(f"[HubSpot] Pre-loaded {len(existing_map)} existing activity IDs for {engagement_type}")

                # Build row dicts
                rows: list[dict[str, Any]] = []
                for raw_engagement in raw_engagements:
                    activity: Activity = self._normalize_engagement(
                        raw_engagement,
                        engagement_type,
                        existing_id=existing_map.get(raw_engagement.get("id", "")),
                    )

                    # -- Resolve associations to internal FKs ---------------
                    assocs: dict[str, Any] = raw_engagement.get("associations", {})

                    deal_id: Optional[uuid.UUID] = None
                    deal_results: list[dict[str, Any]] = assocs.get("deals", {}).get("results", [])
                    for dr in deal_results:
                        resolved: Optional[uuid.UUID] = hs_deal_id_to_deal_id.get(dr.get("id", ""))
                        if resolved:
                            deal_id = resolved
                            break

                    contact_id: Optional[uuid.UUID] = None
                    contact_results: list[dict[str, Any]] = assocs.get("contacts", {}).get("results", [])
                    for cr in contact_results:
                        resolved = hs_contact_id_to_contact_id.get(cr.get("id", ""))
                        if resolved:
                            contact_id = resolved
                            break

                    account_id: Optional[uuid.UUID] = None
                    company_results: list[dict[str, Any]] = assocs.get("companies", {}).get("results", [])
                    for cmr in company_results:
                        resolved = hs_company_id_to_account_id.get(cmr.get("id", ""))
                        if resolved:
                            account_id = resolved
                            break

                    rows.append({
                        "id": activity.id,
                        "organization_id": activity.organization_id,
                        "source_system": activity.source_system,
                        "source_id": activity.source_id,
                        "type": activity.type,
                        "subject": activity.subject,
                        "description": activity.description,
                        "activity_date": activity.activity_date,
                        "deal_id": deal_id,
                        "contact_id": contact_id,
                        "account_id": account_id,
                        "synced_at": datetime.utcnow(),
                    })

                # Bulk upsert in batches
                BATCH_SIZE: int = 500
                update_cols: list[str] = [
                    "type", "subject", "description", "activity_date",
                    "deal_id", "contact_id", "account_id", "synced_at",
                ]
                async with get_session(organization_id=self.organization_id) as session:
                    for i in range(0, len(rows), BATCH_SIZE):
                        batch: list[dict[str, Any]] = rows[i : i + BATCH_SIZE]
                        stmt = pg_insert(Activity).values(batch)
                        stmt = stmt.on_conflict_do_update(
                            index_elements=["id"],
                            set_={col: stmt.excluded[col] for col in update_cols},
                        )
                        await session.execute(stmt)
                        await session.commit()
                        count: int = i + len(batch)
                        await broadcast_sync_progress(
                            organization_id=self.organization_id,
                            provider=self.source_system,
                            count=count,
                            status="syncing",
                            step="activities",
                        )
                        print(f"[HubSpot] Activities ({engagement_type}): {count}/{len(rows)}")
                total_count += len(rows)
                print(f"[HubSpot] Committed {len(rows)} {engagement_type}")
            except httpx.HTTPStatusError:
                # Some engagement types might not be available
                continue

        return total_count

    def _normalize_engagement(
        self,
        hs_engagement: dict[str, Any],
        engagement_type: str,
        existing_id: Optional[uuid.UUID] = None,
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
            id=existing_id or uuid.uuid4(),
            organization_id=uuid.UUID(self.organization_id),
            source_system=self.source_system,
            source_id=hs_id,
            type=type_map.get(engagement_type, engagement_type),
            subject=subject,
            description=description,
            activity_date=activity_date,
        )

    async def _discover_goal_owner_property(self) -> Optional[str]:
        """
        Discover the property name used for goal assignee/owner on goal_targets.

        HubSpot's documented goal properties don't include the assignee, but the
        CRM Properties API reveals all available properties including undocumented
        ones. We look for owner/assignee-related properties in priority order.
        """
        try:
            data: dict[str, Any] = await self._make_request(
                "GET", "/crm/v3/properties/goal_targets"
            )
            prop_names: set[str] = {
                p.get("name", "") for p in data.get("results", [])
            }
            print(f"[HubSpot] Discovered {len(prop_names)} goal_target properties: "
                  f"{sorted(n for n in prop_names if 'owner' in n or 'user' in n or 'assignee' in n)}")

            # Check candidates in priority order
            candidates: list[str] = [
                "hubspot_owner_id",       # Standard owner field on most CRM objects
                "hs_assignee_user_id",    # Possible assignee field
                "hs_user_id",             # Possible user field
                "hs_owner_id",            # Alternate owner field
            ]
            for candidate in candidates:
                if candidate in prop_names:
                    print(f"[HubSpot] Using '{candidate}' as goal owner/assignee property")
                    return candidate

            print("[HubSpot] WARNING: No owner/assignee property found on goal_targets")
            return None
        except httpx.HTTPStatusError as e:
            print(f"[HubSpot] WARNING: Could not discover goal properties ({e}), "
                  "goal owner mapping will be skipped")
            return None

    async def sync_goals(self) -> int:
        """Sync goals/quotas/targets from HubSpot."""
        await broadcast_sync_progress(
            organization_id=self.organization_id,
            provider=self.source_system,
            count=0,
            status="syncing",
            step="fetching goals",
        )

        # Discover the correct owner/assignee property for goal_targets
        goal_owner_prop: Optional[str] = await self._discover_goal_owner_property()

        properties: list[str] = [
            "hs_goal_name",
            "hs_target_amount",
            "hs_start_datetime",
            "hs_end_datetime",
            "hs_created_by_user_id",
        ]
        if goal_owner_prop and goal_owner_prop not in properties:
            properties.append(goal_owner_prop)

        try:
            raw_goals: list[dict[str, Any]] = await self._paginate_results(
                "/crm/v3/objects/goal_targets", properties=properties
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 403:
                print("[HubSpot] WARNING: No permission to fetch goals (requires crm.objects.goals.read scope), skipping")
                return 0
            raise

        if not raw_goals:
            print("[HubSpot] No goals found")
            return 0

        # Pre-load owner email cache for owner mapping
        await self._ensure_owner_email_cache()

        # Build existing source_id -> UUID map
        existing_map: dict[str, uuid.UUID] = {}
        async with get_session(organization_id=self.organization_id) as session:
            result = await session.execute(
                select(Goal.source_id, Goal.id).where(
                    Goal.organization_id == uuid.UUID(self.organization_id),
                    Goal.source_system == self.source_system,
                )
            )
            for row in result.all():
                existing_map[row[0]] = row[1]
        print(f"[HubSpot] Pre-loaded {len(existing_map)} existing goal IDs")

        # Build row dicts
        org_uuid: uuid.UUID = uuid.UUID(self.organization_id)
        rows: list[dict[str, Any]] = []
        for raw_goal in raw_goals:
            hs_id: str = raw_goal.get("id", "")
            props: dict[str, Any] = raw_goal.get("properties", {})

            # Parse dates
            start_date: Optional[datetime] = None
            end_date: Optional[datetime] = None
            if props.get("hs_start_datetime"):
                try:
                    start_date = datetime.fromisoformat(
                        props["hs_start_datetime"].replace("Z", "+00:00")
                    ).date()
                except (ValueError, TypeError):
                    pass
            if props.get("hs_end_datetime"):
                try:
                    end_date = datetime.fromisoformat(
                        props["hs_end_datetime"].replace("Z", "+00:00")
                    ).date()
                except (ValueError, TypeError):
                    pass

            # Parse target amount
            target_amount: Optional[Decimal] = None
            if props.get("hs_target_amount"):
                try:
                    target_amount = Decimal(str(props["hs_target_amount"]))
                except Exception:
                    pass

            # Map goal owner/assignee to internal user
            # The assignee (who the goal is FOR) differs from the creator
            owner_id: Optional[uuid.UUID] = None
            if goal_owner_prop:
                hs_owner_value: Optional[str] = props.get(goal_owner_prop)
                if hs_owner_value:
                    owner_id = await self._map_hs_owner_to_user(hs_owner_value)

            goal_name: str = props.get("hs_goal_name") or f"Goal {hs_id}"

            rows.append({
                "id": existing_map.get(hs_id, uuid.uuid4()),
                "organization_id": org_uuid,
                "source_system": self.source_system,
                "source_id": hs_id,
                "name": goal_name[:255],
                "target_amount": target_amount,
                "start_date": start_date,
                "end_date": end_date,
                "owner_id": owner_id,
                "synced_at": datetime.utcnow(),
                "sync_status": "synced",
            })

        # Bulk upsert in batches
        BATCH_SIZE: int = 500
        update_cols: list[str] = [
            "name", "target_amount", "start_date", "end_date",
            "owner_id", "synced_at",
        ]
        async with get_session(organization_id=self.organization_id) as session:
            for i in range(0, len(rows), BATCH_SIZE):
                batch: list[dict[str, Any]] = rows[i : i + BATCH_SIZE]
                stmt = pg_insert(Goal).values(batch)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["id"],
                    set_={col: stmt.excluded[col] for col in update_cols},
                )
                await session.execute(stmt)
                await session.commit()
                count: int = i + len(batch)
                await broadcast_sync_progress(
                    organization_id=self.organization_id,
                    provider=self.source_system,
                    count=count,
                    status="syncing",
                    step="goals",
                )
                print(f"[HubSpot] Goals: {count}/{len(rows)}")
        print(f"[HubSpot] Committed {len(rows)} goals")

        return len(rows)

    async def _ensure_identity_mapping(
        self,
        session: Any,
        *,
        hs_owner_id: str,
        hs_email: str | None,
        user_id: uuid.UUID | None = None,
        revtops_email: str | None = None,
        match_source: str = "hubspot_owner_email_match",
    ) -> None:
        """
        Upsert a row in ``user_mappings_for_identity`` for a HubSpot owner.

        If a mapping already exists for this (org, external_userid, source)
        it is updated only if we're upgrading from unmapped to mapped.
        Otherwise a new row is inserted (with ``user_id=None`` if unmatched).
        """
        org_uuid: uuid.UUID = uuid.UUID(self.organization_id)
        existing = await session.execute(
            select(SlackUserMapping).where(
                SlackUserMapping.organization_id == org_uuid,
                SlackUserMapping.external_userid == hs_owner_id,
                SlackUserMapping.source == "hubspot",
            )
        )
        mapping: SlackUserMapping | None = existing.scalar_one_or_none()

        if mapping:
            # Upgrade from unmapped to mapped if we now have a user
            if not mapping.user_id and user_id:
                mapping.user_id = user_id
                mapping.revtops_email = revtops_email
                mapping.match_source = match_source
        else:
            session.add(
                SlackUserMapping(
                    id=uuid.uuid4(),
                    organization_id=org_uuid,
                    user_id=user_id,
                    revtops_email=revtops_email,
                    external_userid=hs_owner_id,
                    external_email=hs_email,
                    source="hubspot",
                    match_source=match_source if user_id else "hubspot_directory_unmapped",
                )
            )

    async def _ensure_owner_email_cache(self) -> None:
        """Pre-fetch all HubSpot owners in bulk and cache their emails.

        Uses the list endpoint ``/crm/v3/owners`` which avoids per-owner
        403 errors that can occur with ``/crm/v3/owners/{id}``.
        """
        if self._owner_email_cache_loaded:
            return

        try:
            owners: list[dict[str, Any]] = await self.fetch_owners()
            for owner in owners:
                oid: str = owner.get("id", "")
                email: str | None = owner.get("email")
                if oid and email:
                    self._owner_email_cache[oid] = email
                    self._owner_detail_cache[oid] = {
                        "email": email,
                        "firstName": owner.get("firstName"),
                        "lastName": owner.get("lastName"),
                    }
            print(f"[HubSpot] Pre-fetched {len(self._owner_email_cache)} owner emails")
        except httpx.HTTPStatusError as exc:
            print(f"[HubSpot] WARNING: Could not fetch owners list ({exc}), owner mapping will be skipped")
        self._owner_email_cache_loaded = True

    async def _create_or_link_user_for_hubspot_owner(
        self,
        session: Any,
        *,
        hs_owner_id: str,
        email: str,
        first_name: Optional[str],
        last_name: Optional[str],
    ) -> Optional[uuid.UUID]:
        """Create crm_only User for HubSpot owner, or link existing user to org. Returns user_id or None."""
        org_uuid: uuid.UUID = uuid.UUID(self.organization_id)
        name: Optional[str] = (
            " ".join(filter(None, [first_name, last_name])).strip() or None
        )

        # 1. Check if user exists globally by email
        existing_result = await session.execute(select(User).where(User.email == email))
        user: Optional[User] = existing_result.scalar_one_or_none()

        if user:
            if user.organization_id == org_uuid:
                return user.id
            member_result = await session.execute(
                select(OrgMember).where(
                    OrgMember.user_id == user.id,
                    OrgMember.organization_id == org_uuid,
                )
            )
            if not member_result.scalar_one_or_none():
                session.add(
                    OrgMember(
                        user_id=user.id,
                        organization_id=org_uuid,
                        role="member",
                        status="active",
                    )
                )
            return user.id

        # 2. Create new user
        try:
            new_user: User = User(
                email=email,
                name=name,
                organization_id=org_uuid,
                status="crm_only",
                role="member",
            )
            session.add(new_user)
            await session.flush()
            session.add(
                OrgMember(
                    user_id=new_user.id,
                    organization_id=org_uuid,
                    role="member",
                    status="active",
                )
            )
            return new_user.id
        except IntegrityError:
            await session.rollback()
            retry_result = await session.execute(select(User).where(User.email == email))
            u: Optional[User] = retry_result.scalar_one_or_none()
            if u:
                if u.organization_id == org_uuid:
                    return u.id
                member_retry = await session.execute(
                    select(OrgMember).where(
                        OrgMember.user_id == u.id,
                        OrgMember.organization_id == org_uuid,
                    )
                )
                if not member_retry.scalar_one_or_none():
                    session.add(
                        OrgMember(
                            user_id=u.id,
                            organization_id=org_uuid,
                            role="member",
                            status="active",
                        )
                    )
                return u.id
            return None

    async def _map_hs_owner_to_user(
        self, hs_owner_id: Optional[str]
    ) -> Optional[uuid.UUID]:
        """
        Map HubSpot owner ID to our internal user ID by fetching owner email.

        Persists the mapping in ``user_mappings_for_identity``.
        If no matching local user exists, creates an unmapped identity row
        (``user_id=NULL``) that an admin can link later.
        """
        if not hs_owner_id:
            return None

        # Check cache first
        if hs_owner_id in self._owner_cache:
            return self._owner_cache[hs_owner_id]

        # Ensure bulk owner emails are loaded (single fetch for all owners)
        await self._ensure_owner_email_cache()

        owner_email: Optional[str] = self._owner_email_cache.get(hs_owner_id)
        if not owner_email:
            self._owner_cache[hs_owner_id] = None
            return None

        # Look up user by email within the organization
        async with get_session(organization_id=self.organization_id) as session:
            result = await session.execute(
                select(User).where(
                    User.email == owner_email,
                    User.organization_id == uuid.UUID(self.organization_id),
                )
            )
            user: User | None = result.scalar_one_or_none()

            if user:
                # Matched — persist identity mapping with user_id
                await self._ensure_identity_mapping(
                    session,
                    hs_owner_id=hs_owner_id,
                    hs_email=owner_email,
                    user_id=user.id,
                    revtops_email=user.email,
                )
                await session.commit()
                self._owner_cache[hs_owner_id] = user.id
                return user.id

            # No matching user — proactively create crm_only User (and OrgMember)
            owner_detail: dict[str, Any] = self._owner_detail_cache.get(
                hs_owner_id, {}
            )
            created_user_id: Optional[uuid.UUID] = (
                await self._create_or_link_user_for_hubspot_owner(
                    session,
                    hs_owner_id=hs_owner_id,
                    email=owner_email,
                    first_name=owner_detail.get("firstName"),
                    last_name=owner_detail.get("lastName"),
                )
            )
            if created_user_id:
                await self._ensure_identity_mapping(
                    session,
                    hs_owner_id=hs_owner_id,
                    hs_email=owner_email,
                    user_id=created_user_id,
                    revtops_email=owner_email,
                )
                await session.commit()
                self._owner_cache[hs_owner_id] = created_user_id
                return created_user_id

            # Fallback: create unmapped identity row (user_id=NULL), e.g. owner without email
            await self._ensure_identity_mapping(
                session,
                hs_owner_id=hs_owner_id,
                hs_email=owner_email,
            )
            await session.commit()
            self._owner_cache[hs_owner_id] = None
            return None

    async def map_user_to_hs_owner(self, user_id: uuid.UUID) -> Optional[str]:
        """Return the HubSpot owner ID for a local user, or ``None``.

        Resolution order:
        1. Existing identity mapping in ``user_mappings_for_identity``
           (source='hubspot', user_id=<user_id>).
        2. Fall back to the bulk owner email cache (email match).
        """
        # 1. Check identity mapping table
        async with get_session(organization_id=self.organization_id) as session:
            from models.slack_user_mapping import SlackUserMapping as IdentityMapping

            result = await session.execute(
                select(IdentityMapping).where(
                    IdentityMapping.organization_id == uuid.UUID(self.organization_id),
                    IdentityMapping.user_id == user_id,
                    IdentityMapping.source == "hubspot",
                    IdentityMapping.external_userid.isnot(None),
                )
            )
            mapping: Optional[IdentityMapping] = result.scalar_one_or_none()
            if mapping and mapping.external_userid:
                return mapping.external_userid

            # 2. Fetch user email and match against HubSpot owners
            user_result = await session.execute(
                select(User).where(User.id == user_id)
            )
            user: Optional[User] = user_result.scalar_one_or_none()
            if not user or not user.email:
                return None

        # Ensure owner email cache is populated
        await self._ensure_owner_email_cache()

        # Reverse lookup: email → owner ID
        user_email_lower: str = user.email.lower()
        for hs_owner_id, owner_email in self._owner_email_cache.items():
            if owner_email.lower() == user_email_lower:
                return hs_owner_id

        return None

    async def fetch_owners(self) -> list[dict[str, Any]]:
        """
        Fetch all HubSpot owners from the account.

        Requires the ``crm.objects.owners.read`` scope.

        Returns:
            List of owner dicts with id, email, firstName, lastName.
        """
        results: list[dict[str, Any]] = []
        after: str | None = None

        while True:
            params: dict[str, str | int] = {"limit": 100}
            if after:
                params["after"] = after

            data: dict[str, Any] = await self._make_request(
                "GET", "/crm/v3/owners", params=params
            )
            for o in data.get("results", []):
                results.append({
                    "id": str(o.get("id", "")),
                    "email": o.get("email"),
                    "firstName": o.get("firstName"),
                    "lastName": o.get("lastName"),
                })

            paging: dict[str, Any] | None = data.get("paging")
            if paging and paging.get("next", {}).get("after"):
                after = paging["next"]["after"]
            else:
                break

        return results

    async def match_owners_to_users(self) -> list[dict[str, Any]]:
        """
        Fetch all HubSpot owners, match them by email to local users,
        and persist mappings in ``user_mappings_for_identity``.

        Owners that don't match any local user get an unmapped row
        (``user_id=NULL``) rather than a stub user.

        Requires the ``crm.objects.owners.read`` scope.
        """
        hs_owners: list[dict[str, Any]] = await self.fetch_owners()

        # Build email -> hs_owner_id map and reverse lookup
        owner_email_map: dict[str, str] = {}
        owner_raw_emails: dict[str, str] = {}
        for owner in hs_owners:
            email: str | None = owner.get("email")
            oid: str = owner.get("id", "")
            if email and oid:
                owner_email_map[email.lower()] = oid
                owner_raw_emails[oid] = email

        results: list[dict[str, Any]] = []
        matched_owner_ids: set[str] = set()

        async with get_session(organization_id=self.organization_id) as session:
            # Match local users to HubSpot owners
            db_result = await session.execute(
                select(User).where(
                    User.organization_id == uuid.UUID(self.organization_id),
                    User.status != "crm_only",
                )
            )
            users: list[User] = list(db_result.scalars().all())

            for user in users:
                hs_owner_id: str | None = owner_email_map.get(user.email.lower())
                if hs_owner_id:
                    await self._ensure_identity_mapping(
                        session,
                        hs_owner_id=hs_owner_id,
                        hs_email=owner_raw_emails.get(hs_owner_id),
                        user_id=user.id,
                        revtops_email=user.email,
                    )
                    matched_owner_ids.add(hs_owner_id)
                    results.append({
                        "email": user.email,
                        "hubspot_owner_id": hs_owner_id,
                        "user_id": str(user.id),
                        "user_name": user.name,
                        "matched": True,
                    })
                else:
                    results.append({
                        "email": user.email,
                        "hubspot_owner_id": None,
                        "user_id": str(user.id),
                        "user_name": user.name,
                        "matched": False,
                    })

            # Create unmapped identity rows for HubSpot owners with no match
            for oid, raw_email in owner_raw_emails.items():
                if oid not in matched_owner_ids:
                    await self._ensure_identity_mapping(
                        session,
                        hs_owner_id=oid,
                        hs_email=raw_email,
                    )

            await session.commit()

        return results

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

    async def write(self, operation: str, data: dict[str, Any]) -> dict[str, Any]:
        """Dispatch a record-level write operation."""
        if operation == "create_deal":
            return await self.create_deal(data)
        if operation == "update_deal":
            deal_id: str = data.pop("deal_id", None) or data.pop("id")
            return await self.update_deal(deal_id, data)
        if operation == "create_contact":
            return await self.create_contact(data)
        if operation == "update_contact":
            contact_id: str = data.pop("contact_id", None) or data.pop("id")
            return await self.update_contact(contact_id, data)
        if operation == "create_company":
            return await self.create_company(data)
        if operation == "update_company":
            company_id: str = data.pop("company_id", None) or data.pop("id")
            return await self.update_company(company_id, data)
        raise ValueError(f"Unknown write operation: {operation}")

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

    # =========================================================================
    # Engagement Write Operations (calls, emails, meetings, notes)
    # =========================================================================

    # HubSpot V3 engagement object type to API path mapping
    _ENGAGEMENT_OBJECT_PATHS: dict[str, str] = {
        "call": "calls",
        "email": "emails",
        "meeting": "meetings",
        "note": "notes",
    }

    # Default association type IDs: engagement → CRM object
    # Source: https://developers.hubspot.com/docs/api-reference/crm-associations-v4/guide
    _ENGAGEMENT_ASSOC_TYPE_IDS: dict[str, dict[str, int]] = {
        "call":    {"contact": 194, "company": 182, "deal": 206},
        "email":   {"contact": 198, "company": 186, "deal": 210},
        "meeting": {"contact": 200, "company": 188, "deal": 212},
        "note":    {"contact": 202, "company": 190, "deal": 214},
    }

    async def create_engagement(
        self,
        engagement_type: str,
        properties: dict[str, Any],
        associations: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """
        Create a single engagement (call, email, meeting, or note) in HubSpot.

        Args:
            engagement_type: One of 'call', 'email', 'meeting', 'note'
            properties: HubSpot engagement properties (hs_timestamp required)
            associations: Optional list of association dicts for the v3 API, each with
                          ``{"to": {"id": <hs_id>}, "types": [{"associationCategory": ..., "associationTypeId": ...}]}``

        Returns:
            Created engagement data with HubSpot ID
        """
        object_path: str = self._ENGAGEMENT_OBJECT_PATHS[engagement_type]
        json_data: dict[str, Any] = {"properties": properties}
        if associations:
            json_data["associations"] = associations

        data: dict[str, Any] = await self._make_request(
            "POST",
            f"/crm/v3/objects/{object_path}",
            json_data=json_data,
        )
        return {
            "id": data.get("id"),
            "properties": data.get("properties", {}),
        }

    async def create_engagements_batch(
        self,
        engagement_type: str,
        engagements: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """
        Batch create engagements in HubSpot (up to 100 per call).

        Each item in *engagements* should be a dict with a ``properties`` key
        and an optional ``associations`` key.

        Args:
            engagement_type: One of 'call', 'email', 'meeting', 'note'
            engagements: List of engagement input dicts

        Returns:
            Batch result with created engagements and any errors
        """
        if len(engagements) > 100:
            raise ValueError("HubSpot batch limit is 100 records per call")

        object_path: str = self._ENGAGEMENT_OBJECT_PATHS[engagement_type]
        inputs: list[dict[str, Any]] = []
        for eng in engagements:
            item: dict[str, Any] = {"properties": eng.get("properties", eng)}
            if eng.get("associations"):
                item["associations"] = eng["associations"]
            inputs.append(item)

        data: dict[str, Any] = await self._make_request(
            "POST",
            f"/crm/v3/objects/{object_path}/batch/create",
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

    def build_engagement_associations(
        self,
        engagement_type: str,
        raw_associations: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """
        Convert simplified association dicts into HubSpot v3 association format.

        Each item in *raw_associations* should have:
        - ``to_object_type``: 'contact', 'company', or 'deal'
        - ``to_object_id``: HubSpot record ID (string)

        Returns:
            List of HubSpot v3 association objects ready for the API
        """
        hs_associations: list[dict[str, Any]] = []
        type_ids: dict[str, int] = self._ENGAGEMENT_ASSOC_TYPE_IDS.get(engagement_type, {})

        for assoc in raw_associations:
            to_type: str = assoc.get("to_object_type", "")
            to_id: str = str(assoc.get("to_object_id", ""))
            assoc_type_id: int | None = type_ids.get(to_type)

            if not to_id or assoc_type_id is None:
                continue

            hs_associations.append({
                "to": {"id": to_id},
                "types": [
                    {
                        "associationCategory": "HUBSPOT_DEFINED",
                        "associationTypeId": assoc_type_id,
                    }
                ],
            })

        return hs_associations

    async def get_pipelines(self) -> list[dict[str, Any]]:
        """
        Fetch all deal pipelines from HubSpot.

        Returns:
            List of pipelines with their stages
        """
        data = await self._make_request("GET", "/crm/v3/pipelines/deals")
        results = data.get("results", [])

        pipelines: list[dict[str, Any]] = []
        for pipeline in results:
            stages: list[dict[str, Any]] = []
            for stage in pipeline.get("stages", []):
                stages.append({
                    "id": stage.get("id"),
                    "label": stage.get("label"),
                    "display_order": stage.get("displayOrder"),
                    "metadata": stage.get("metadata", {}),
                })

            pipelines.append({
                "id": pipeline.get("id"),
                "label": pipeline.get("label"),
                "display_order": pipeline.get("displayOrder"),
                "stages": stages,
            })

        return pipelines
