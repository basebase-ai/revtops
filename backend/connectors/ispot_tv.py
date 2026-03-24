"""
iSpot.tv connector implementation.

TV advertising analytics: sync brands as accounts, TV airings as activities,
and support ad-hoc QUERY against the iSpot v4 REST API.
Uses OAuth 2.0 client_credentials flow; credentials stored in integration.extra_data.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from connectors.base import BaseConnector
from connectors.models import AccountRecord, ActivityRecord
from connectors.registry import (
    AuthField,
    AuthType,
    Capability,
    ConnectorMeta,
    ConnectorScope,
)

logger = logging.getLogger(__name__)

ISPOT_API_BASE = "https://api.ispot.tv/v4"
ISPOT_TOKEN_URL = "https://api.ispot.tv/v4/oauth2/token"
TOKEN_EXPIRY_BUFFER = timedelta(hours=1)
DEFAULT_AIRINGS_PAGE_SIZE = 10000
DEFAULT_BRANDS_PAGE_SIZE = 10000
MAX_429_RETRIES = 5
DEFAULT_SYNC_DAYS = 30


class ISpotTvConnector(BaseConnector):
    """Connector for iSpot.tv TV advertising analytics."""

    source_system = "ispot_tv"

    meta = ConnectorMeta(
        name="iSpot.tv",
        slug="ispot_tv",
        description="TV advertising analytics - airings, spend, impressions, and conversions",
        auth_type=AuthType.CUSTOM,
        scope=ConnectorScope.ORGANIZATION,
        entity_types=["accounts", "activities"],
        capabilities=[Capability.QUERY],
        query_description=(
            "Query iSpot.tv for TV ad analytics. Supports: "
            "airings, spots, brands, spend, impressions, conversions, attention metrics. "
            "Pass a JSON object: "
            '{"endpoint": "metrics/tv/airings", "filters": {"start_date": "2025-01-01", "end_date": "2025-01-31", "brand": "12345"}, "include": "brand,network"}'
        ),
        auth_fields=[
            AuthField(name="client_id", label="OAuth Client ID", required=True),
            AuthField(
                name="client_secret",
                label="OAuth Client Secret",
                type="password",
                required=True,
            ),
        ],
    )

    def __init__(
        self,
        organization_id: str,
        user_id: str | None = None,
        *,
        sync_since_override: datetime | None = None,
    ) -> None:
        super().__init__(
            organization_id, user_id, sync_since_override=sync_since_override
        )
        self._token_expires_at: datetime | None = None

    async def _get_credentials(self) -> tuple[str, str]:
        """Load client_id and client_secret from integration extra_data."""
        if not self._integration:
            await self._load_integration()
        if not self._integration:
            raise ValueError(
                f"No active {self.source_system} integration for organization: {self.organization_id}"
            )
        extra: dict[str, Any] = self._integration.extra_data or {}
        client_id: str | None = extra.get("client_id")
        client_secret: str | None = extra.get("client_secret")
        if not client_id or not client_secret:
            raise ValueError(
                "iSpot.tv integration missing client_id or client_secret in extra_data"
            )
        return client_id, client_secret

    async def _fetch_token(self) -> str:
        """Exchange client credentials for a bearer token; cache with expiry."""
        now = datetime.now(timezone.utc)
        if (
            self._token is not None
            and self._token_expires_at is not None
            and now < self._token_expires_at - TOKEN_EXPIRY_BUFFER
        ):
            return self._token

        client_id, client_secret = await self._get_credentials()
        async with httpx.AsyncClient() as client:
            response: httpx.Response = await client.post(
                ISPOT_TOKEN_URL,
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=15.0,
            )
        if response.status_code >= 400:
            err_text: str = response.text[:500] if response.text else ""
            raise ValueError(
                f"iSpot.tv token exchange failed ({response.status_code}): {err_text}"
            )
        body: dict[str, Any] = response.json()
        access_token: str | None = body.get("access_token")
        if not access_token:
            raise ValueError("iSpot.tv token response missing access_token")
        expires_in: int = int(body.get("expires_in", 86400))
        self._token = access_token
        self._token_expires_at = now + timedelta(seconds=expires_in)
        return self._token

    async def get_oauth_token(self) -> tuple[str, str]:
        """Return (access_token, "") for iSpot API; uses client_credentials, not Nango."""
        token: str = await self._fetch_token()
        return token, ""

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        _max_retries: int = MAX_429_RETRIES,
    ) -> dict[str, Any]:
        """Make an authenticated request to iSpot v4 API with 429 retry."""
        token: str = await self._fetch_token()
        url: str = f"{ISPOT_API_BASE}/{path.lstrip('/')}"
        headers: dict[str, str] = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        last_exc: httpx.HTTPStatusError | None = None
        for attempt in range(_max_retries + 1):
            async with httpx.AsyncClient() as client:
                response: httpx.Response = await client.request(
                    method=method,
                    url=url,
                    headers=headers,
                    params=params,
                    timeout=60.0,
                )
                if response.status_code == 429 and attempt < _max_retries:
                    retry_after: float = float(
                        response.headers.get("Retry-After", "60")
                    )
                    wait_secs: float = min(retry_after, 60.0)
                    logger.warning(
                        "iSpot.tv 429 on %s, retry in %ss (attempt %s/%s)",
                        path,
                        wait_secs,
                        attempt + 1,
                        _max_retries,
                    )
                    await asyncio.sleep(wait_secs)
                    continue
                if response.status_code >= 400:
                    err_text = response.text[:500] if response.text else ""
                    last_exc = httpx.HTTPStatusError(
                        f"iSpot.tv API error ({response.status_code}): {err_text}",
                        request=response.request,
                        response=response,
                    )
                    raise last_exc
                return response.json()
        if last_exc:
            raise last_exc
        return {}

    async def sync_deals(self) -> list[Any]:
        """iSpot.tv has no deals; return empty list."""
        return []

    async def sync_contacts(self) -> list[Any]:
        """iSpot.tv has no contacts; return empty list."""
        return []

    async def fetch_deal(self, deal_id: str) -> dict[str, Any]:
        raise NotImplementedError("iSpot.tv does not support deals")

    async def sync_accounts(self) -> list[AccountRecord]:
        """Fetch all brands and return as AccountRecords."""
        await self.ensure_sync_active("sync_accounts")
        all_brands: list[AccountRecord] = []
        page_number: int = 1
        while True:
            params: dict[str, Any] = {
                "include": "industry,parent",
                "page[size]": DEFAULT_BRANDS_PAGE_SIZE,
                "page[number]": page_number,
            }
            data: dict[str, Any] = await self._request("GET", "brands", params=params)
            raw_list: list[dict[str, Any]] = data.get("data") or []
            for b in raw_list:
                bid: str = str(b.get("id", ""))
                attrs: dict[str, Any] = b.get("attributes") or b
                name: str = str(attrs.get("name", "") or attrs.get("brand_name", "") or bid)
                industry_id: Any = attrs.get("industry_id") or (b.get("relationships") or {}).get("industry", {}).get("data", {}).get("id")
                parent_id: Any = attrs.get("parent_id") or (b.get("relationships") or {}).get("parent", {}).get("data", {}).get("id")
                custom: dict[str, Any] = {}
                if industry_id is not None:
                    custom["industry_id"] = industry_id
                if parent_id is not None:
                    custom["parent_brand_id"] = parent_id
                all_brands.append(
                    AccountRecord(
                        source_id=bid,
                        name=name,
                        industry=str(industry_id) if industry_id is not None else None,
                        custom_fields=custom if custom else None,
                        source_system=self.source_system,
                    )
                )
            if len(raw_list) < DEFAULT_BRANDS_PAGE_SIZE:
                break
            page_number += 1
        return all_brands

    def _airings_date_range(self) -> tuple[str, str]:
        """Return (start_date, end_date) in ISO format for airings filter."""
        sync_since = self.sync_since
        if sync_since is not None:
            start = sync_since
        else:
            start = datetime.now(timezone.utc) - timedelta(days=DEFAULT_SYNC_DAYS)
        end = datetime.now(timezone.utc)
        return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")

    async def sync_activities(self) -> list[ActivityRecord]:
        """Fetch TV airings in date range and return as ActivityRecords."""
        await self.ensure_sync_active("sync_activities")
        start_date, end_date = self._airings_date_range()
        all_activities: list[ActivityRecord] = []
        page_number = 1
        while True:
            params = {
                "filter[start_date]": start_date,
                "filter[end_date]": end_date,
                "filter[airing_type]": "N,R",
                "filter[national_only]": "1",
                "filter[airings_min]": "1",
                "include": "industry,brand,episode,show,genre,sub_genre,network,day_part,day_of_week",
                "page[size]": DEFAULT_AIRINGS_PAGE_SIZE,
                "page[number]": page_number,
                "sort": "-airing_date_et",
                "metrics[include_audience_lifetime]": "0",
                "metrics[include_airings]": "1",
                "metrics[include_audience]": "0",
                "metrics[include_audience_attention]": "0",
                "metrics[include_audience_demo]": "0",
            }
            data = await self._request("GET", "metrics/tv/airings", params=params)
            raw_list = data.get("data") or []
            for row in raw_list:
                airing_id: str = str(row.get("id", ""))
                attrs = row.get("attributes") or row
                airing_date_str: str | None = attrs.get("airing_date_et") or attrs.get("airing_date")
                activity_date: datetime | None = None
                if airing_date_str:
                    try:
                        activity_date = datetime.fromisoformat(
                            airing_date_str.replace("Z", "+00:00")
                        )
                    except (ValueError, TypeError):
                        pass
                subject: str = str(
                    attrs.get("spot_name") or attrs.get("creative_name") or airing_id
                )
                custom: dict[str, Any] = {
                    "network": attrs.get("network"),
                    "show": attrs.get("show"),
                    "est_spend": attrs.get("est_spend"),
                    "duration": attrs.get("duration"),
                    "airing_type": attrs.get("airing_type"),
                    "brand_id": attrs.get("brand_id"),
                    "spot_id": attrs.get("spot_id"),
                }
                all_activities.append(
                    ActivityRecord(
                        source_id=airing_id,
                        type="tv_airing",
                        subject=subject,
                        description=None,
                        activity_date=activity_date,
                        custom_fields=custom,
                        source_system=self.source_system,
                    )
                )
            if len(raw_list) < DEFAULT_AIRINGS_PAGE_SIZE:
                break
            page_number += 1
        return all_activities

    async def get_schema(self) -> list[dict[str, Any]]:
        """Return queryable schema for QUERY capability."""
        return [
            {"entity": "brands", "fields": ["id", "name", "industry_id", "parent_id"]},
            {
                "entity": "spots",
                "fields": ["id", "name", "brand_id", "start_date", "end_date"],
            },
            {
                "entity": "airings",
                "fields": [
                    "id",
                    "airing_date_et",
                    "spot_id",
                    "network",
                    "show",
                    "est_spend",
                    "impressions",
                ],
            },
        ]

    async def query(self, request: str) -> dict[str, Any]:
        """Execute an ad-hoc iSpot API query. Request is JSON with endpoint, filters, include, metrics, sort, page_size."""
        try:
            payload: dict[str, Any] = json.loads(request) if isinstance(request, str) else request
        except json.JSONDecodeError as e:
            return {"error": f"Invalid JSON request: {e}", "results": []}
        endpoint: str | None = payload.get("endpoint")
        if not endpoint:
            return {"error": "Missing 'endpoint' in request", "results": []}
        path = endpoint.strip().strip("/")
        params_flat: dict[str, Any] = {}
        filters: dict[str, Any] = payload.get("filters") or {}
        for k, v in filters.items():
            if v is None:
                continue
            params_flat[f"filter[{k}]"] = v
        include: str | None = payload.get("include")
        if include:
            params_flat["include"] = include if isinstance(include, str) else ",".join(include)
        metrics: dict[str, Any] = payload.get("metrics") or {}
        for k, v in metrics.items():
            if v is None:
                continue
            params_flat[f"metrics[{k}]"] = v
        sort: str | None = payload.get("sort")
        if sort:
            params_flat["sort"] = sort
        page_size: int = int(payload.get("page_size", 100))
        params_flat["page[size]"] = min(page_size, 10000)
        params_flat["page[number]"] = 1
        data = await self._request("GET", path, params=params_flat)
        return {"data": data.get("data", []), "meta": data.get("meta", {}), "query": payload}
