"""
Apollo.io connector implementation.

Responsibilities:
- Authenticate with Apollo using API key (Bearer token)
- Enrich people and organizations using Apollo's database
- Handle rate limits and batching for bulk operations

Note: Apollo is a data enrichment service, not a CRM. It doesn't store
deals/accounts/contacts to sync. Instead, it provides enrichment APIs
to augment data from other sources (HubSpot, Salesforce, etc.).
"""

import asyncio
import logging
from typing import Any, Optional

import httpx

from connectors.base import BaseConnector
from connectors.registry import AuthType, Capability, ConnectorMeta, ConnectorScope

logger = logging.getLogger(__name__)

APOLLO_API_BASE = "https://api.apollo.io/api/v1"


class ApolloConnector(BaseConnector):
    """
    Connector for Apollo.io data enrichment service.

    Apollo uses API key authentication via Bearer token.
    The API key is stored in Nango and retrieved automatically.
    """

    source_system = "apollo"
    meta = ConnectorMeta(
        name="Apollo",
        slug="apollo",
        auth_type=AuthType.API_KEY,
        scope=ConnectorScope.ORGANIZATION,
        entity_types=["contacts", "accounts"],
        capabilities=[Capability.QUERY],
        query_description=(
            "Enrich a person or company using Apollo.io. "
            "Pass a JSON string: "
            '{"type":"person","email":"alice@acme.com"} or '
            '{"type":"person","first_name":"Alice","last_name":"Smith","domain":"acme.com"} or '
            '{"type":"company","domain":"acme.com"}'
        ),
        nango_integration_id="apollo",
        description="Apollo.io data enrichment – contacts and company data",
    )

    def __init__(self, organization_id: str) -> None:
        """Initialize Apollo connector."""
        super().__init__(organization_id)

    async def _get_api_key(self) -> str:
        """Get the Apollo API key from Nango."""
        token, _ = await self.get_oauth_token()  # Works for API keys too
        return token

    async def _make_request(
        self,
        method: str,
        endpoint: str,
        json_data: Optional[dict[str, Any]] = None,
        params: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """
        Make an authenticated request to Apollo API.

        Apollo accepts the API key via:
        - Header: X-Api-Key (recommended per deprecation notice)
        - Request body: api_key field (legacy but still supported)
        
        We send both for maximum compatibility.

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint path (e.g., /people/match)
            json_data: Request body as dict
            params: Query parameters

        Returns:
            Response data as dict

        Raises:
            httpx.HTTPStatusError: On API errors
        """
        api_key: str = await self._get_api_key()
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
            "X-Api-Key": api_key,
        }
        url: str = f"{APOLLO_API_BASE}{endpoint}"

        # Also include api_key in the request body (legacy auth, still works)
        if json_data is None:
            json_data = {}
        json_data["api_key"] = api_key

        async with httpx.AsyncClient() as client:
            response = await client.request(
                method=method,
                url=url,
                headers=headers,
                json=json_data,
                params=params,
                timeout=60.0,  # Apollo enrichment can be slow
            )

            # Handle errors with detailed messages
            if response.status_code >= 400:
                error_detail = ""
                try:
                    error_body = response.json()
                    error_detail = error_body.get("message", "")
                    if error_body.get("error"):
                        error_detail = error_body["error"]
                except Exception:
                    error_detail = response.text[:500] if response.text else ""

                logger.error(
                    "[Apollo] API error %d on %s %s: %s",
                    response.status_code, method, endpoint, error_detail,
                )

                raise httpx.HTTPStatusError(
                    f"Apollo API error ({response.status_code}): {error_detail}",
                    request=response.request,
                    response=response,
                )

            return response.json()

    # =========================================================================
    # People Enrichment
    # =========================================================================

    async def enrich_person(
        self,
        email: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        domain: Optional[str] = None,
        linkedin_url: Optional[str] = None,
        organization_name: Optional[str] = None,
        reveal_personal_emails: bool = False,
        reveal_phone_number: bool = False,
    ) -> Optional[dict[str, Any]]:
        """
        Enrich a single person using Apollo's database.

        Apollo needs enough identifying information to find a match.
        The more information provided, the better the match quality.

        Args:
            email: Person's email address (best identifier)
            first_name: Person's first name
            last_name: Person's last name
            domain: Company domain (e.g., "acme.com")
            linkedin_url: Person's LinkedIn profile URL
            organization_name: Company name
            reveal_personal_emails: Request personal email addresses (uses credits)
            reveal_phone_number: Request phone numbers (uses credits)

        Returns:
            Enriched person data or None if no match found.
            Includes: name, title, company, email, phone, linkedin_url, etc.
        """
        payload: dict[str, Any] = {
            "reveal_personal_emails": reveal_personal_emails,
            "reveal_phone_number": reveal_phone_number,
        }

        if email:
            payload["email"] = email
        if first_name:
            payload["first_name"] = first_name
        if last_name:
            payload["last_name"] = last_name
        if domain:
            payload["domain"] = domain
        if linkedin_url:
            payload["linkedin_url"] = linkedin_url
        if organization_name:
            payload["organization_name"] = organization_name

        try:
            data = await self._make_request("POST", "/people/match", json_data=payload)
            person = data.get("person")
            if person:
                return self._normalize_person(person)
            return None
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

    async def bulk_enrich_people(
        self,
        people: list[dict[str, Any]],
        reveal_personal_emails: bool = False,
        reveal_phone_number: bool = False,
    ) -> list[dict[str, Any]]:
        """
        Enrich multiple people in a single API call (up to 10 per request).

        For larger batches, this method automatically chunks the requests
        and respects Apollo's rate limits.

        Args:
            people: List of person details dicts. Each dict can contain:
                - email: Person's email
                - first_name: First name
                - last_name: Last name
                - domain: Company domain
                - linkedin_url: LinkedIn URL
                - organization_name: Company name
            reveal_personal_emails: Request personal emails (uses credits)
            reveal_phone_number: Request phone numbers (uses credits)

        Returns:
            List of enriched person data (in same order as input).
            Items will be None where no match was found.
        """
        results: list[dict[str, Any]] = []

        # Apollo bulk endpoint supports max 10 people per request
        batch_size = 10

        for i in range(0, len(people), batch_size):
            batch = people[i : i + batch_size]

            # Build the details array for this batch
            details: list[dict[str, Any]] = []
            for person in batch:
                detail: dict[str, Any] = {}
                if person.get("email"):
                    detail["email"] = person["email"]
                if person.get("first_name"):
                    detail["first_name"] = person["first_name"]
                if person.get("last_name"):
                    detail["last_name"] = person["last_name"]
                if person.get("domain"):
                    detail["domain"] = person["domain"]
                if person.get("linkedin_url"):
                    detail["linkedin_url"] = person["linkedin_url"]
                if person.get("organization_name"):
                    detail["organization_name"] = person["organization_name"]
                details.append(detail)

            payload: dict[str, Any] = {
                "details": details,
                "reveal_personal_emails": reveal_personal_emails,
                "reveal_phone_number": reveal_phone_number,
            }

            try:
                data = await self._make_request(
                    "POST", "/people/bulk_match", json_data=payload
                )
                matches = data.get("matches", [])

                # Normalize each match
                for match in matches:
                    person_data = match.get("person")
                    if person_data:
                        results.append(self._normalize_person(person_data))
                    else:
                        results.append({})

            except httpx.HTTPStatusError as e:
                # Auth/permission errors should propagate, not be silently swallowed
                if e.response.status_code in (401, 403):
                    raise ValueError(
                        f"Apollo API authentication failed ({e.response.status_code}). "
                        f"Check that your API key is valid and has the required permissions."
                    )
                # Other errors: append empty results for this batch
                for _ in batch:
                    results.append({})
                # Log but don't fail entire operation
                await self.record_error(f"Bulk enrichment batch failed: {str(e)}")

            # Rate limit: brief pause between batches
            if i + batch_size < len(people):
                await asyncio.sleep(0.5)

        return results

    def _normalize_person(self, apollo_person: dict[str, Any]) -> dict[str, Any]:
        """
        Normalize Apollo person data to a consistent format.

        Args:
            apollo_person: Raw person data from Apollo API

        Returns:
            Normalized person dict with consistent field names
        """
        # Extract organization info
        org = apollo_person.get("organization") or {}

        return {
            "apollo_id": apollo_person.get("id"),
            "first_name": apollo_person.get("first_name"),
            "last_name": apollo_person.get("last_name"),
            "name": apollo_person.get("name"),
            "title": apollo_person.get("title"),
            "headline": apollo_person.get("headline"),
            "email": apollo_person.get("email"),
            "email_status": apollo_person.get("email_status"),
            "personal_emails": apollo_person.get("personal_emails", []),
            "phone_numbers": apollo_person.get("phone_numbers", []),
            "linkedin_url": apollo_person.get("linkedin_url"),
            "twitter_url": apollo_person.get("twitter_url"),
            "github_url": apollo_person.get("github_url"),
            "facebook_url": apollo_person.get("facebook_url"),
            "photo_url": apollo_person.get("photo_url"),
            "city": apollo_person.get("city"),
            "state": apollo_person.get("state"),
            "country": apollo_person.get("country"),
            # Organization/company info
            "company_name": org.get("name"),
            "company_domain": apollo_person.get("organization_domain") or org.get("primary_domain"),
            "company_industry": org.get("industry"),
            "company_employee_count": org.get("estimated_num_employees"),
            "company_linkedin_url": org.get("linkedin_url"),
            "company_website_url": org.get("website_url"),
            # Employment history (current)
            "seniority": apollo_person.get("seniority"),
            "departments": apollo_person.get("departments", []),
        }

    # =========================================================================
    # Organization Enrichment
    # =========================================================================

    async def enrich_organization(
        self, domain: str
    ) -> Optional[dict[str, Any]]:
        """
        Enrich an organization/company by domain.

        Args:
            domain: Company domain (e.g., "acme.com")

        Returns:
            Enriched organization data or None if not found.
            Includes: name, industry, employee_count, revenue, etc.
        """
        payload: dict[str, Any] = {
            "domain": domain,
        }

        try:
            data = await self._make_request(
                "POST", "/organizations/enrich", json_data=payload
            )
            org = data.get("organization")
            if org:
                return self._normalize_organization(org)
            return None
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

    def _normalize_organization(self, apollo_org: dict[str, Any]) -> dict[str, Any]:
        """
        Normalize Apollo organization data to a consistent format.

        Args:
            apollo_org: Raw organization data from Apollo API

        Returns:
            Normalized organization dict
        """
        return {
            "apollo_id": apollo_org.get("id"),
            "name": apollo_org.get("name"),
            "domain": apollo_org.get("primary_domain"),
            "website_url": apollo_org.get("website_url"),
            "linkedin_url": apollo_org.get("linkedin_url"),
            "twitter_url": apollo_org.get("twitter_url"),
            "facebook_url": apollo_org.get("facebook_url"),
            "phone": apollo_org.get("phone"),
            "logo_url": apollo_org.get("logo_url"),
            # Location
            "street_address": apollo_org.get("street_address"),
            "city": apollo_org.get("city"),
            "state": apollo_org.get("state"),
            "postal_code": apollo_org.get("postal_code"),
            "country": apollo_org.get("country"),
            # Industry & size
            "industry": apollo_org.get("industry"),
            "subindustry": apollo_org.get("subindustry"),
            "keywords": apollo_org.get("keywords", []),
            "employee_count": apollo_org.get("estimated_num_employees"),
            "employee_count_range": apollo_org.get("employees_range"),
            "annual_revenue": apollo_org.get("annual_revenue"),
            "annual_revenue_range": apollo_org.get("annual_revenue_printed"),
            # Metadata
            "founded_year": apollo_org.get("founded_year"),
            "short_description": apollo_org.get("short_description"),
            "seo_description": apollo_org.get("seo_description"),
            "technologies": apollo_org.get("technologies", []),
        }

    # =========================================================================
    # QUERY capability
    # =========================================================================

    async def query(self, request: str) -> dict[str, Any]:
        """Dispatch enrichment queries: person or company."""
        import json as _json

        try:
            payload: dict[str, Any] = _json.loads(request)
        except (ValueError, TypeError):
            payload = {"type": "person", "email": request.strip()}

        query_type: str = payload.get("type", "person")

        if query_type == "company":
            domain: str | None = payload.get("domain")
            if not domain:
                return {"error": "domain is required for company enrichment"}
            result = await self.enrich_organization(domain)
            return result if result else {"error": f"No company found for domain '{domain}'"}

        person_result = await self.enrich_person(
            email=payload.get("email"),
            first_name=payload.get("first_name"),
            last_name=payload.get("last_name"),
            domain=payload.get("domain"),
            linkedin_url=payload.get("linkedin_url"),
            organization_name=payload.get("organization_name"),
        )
        return person_result if person_result else {"error": "No person match found for the given identifiers"}

    # =========================================================================
    # BaseConnector Abstract Methods (Not applicable for Apollo)
    # =========================================================================

    async def sync_deals(self) -> int:
        """Not applicable for Apollo - it's an enrichment service, not a CRM."""
        return 0

    async def sync_accounts(self) -> int:
        """Not applicable for Apollo - it's an enrichment service, not a CRM."""
        return 0

    async def sync_contacts(self) -> int:
        """Not applicable for Apollo - it's an enrichment service, not a CRM."""
        return 0

    async def sync_activities(self) -> int:
        """Not applicable for Apollo - it's an enrichment service, not a CRM."""
        return 0

    async def fetch_deal(self, deal_id: str) -> dict[str, Any]:
        """Not applicable for Apollo - it's an enrichment service, not a CRM."""
        raise NotImplementedError("Apollo is an enrichment service, not a CRM")
