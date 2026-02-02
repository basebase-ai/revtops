"""
Data inspector endpoints for viewing synced data.

Allows users to browse their synced contacts, accounts, deals, and activities
in a paginated table view.
"""
from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import asc, desc, func, select

from models.account import Account
from models.activity import Activity
from models.contact import Contact
from models.database import get_session
from models.deal import Deal

router = APIRouter()


class DataRow(BaseModel):
    """Generic data row for inspector."""
    id: str
    data: dict[str, str | int | float | bool | None]


class DataResponse(BaseModel):
    """Response for data inspector queries."""
    table: str
    rows: list[DataRow]
    total: int
    page: int
    page_size: int
    columns: list[str]
    sort_by: Optional[str] = None
    sort_order: Optional[str] = None


class TableSummary(BaseModel):
    """Summary of a data table."""
    name: str
    display_name: str
    count: int


class DataSummaryResponse(BaseModel):
    """Response with counts for all tables."""
    organization_id: str
    tables: list[TableSummary]


class ActivityTypesResponse(BaseModel):
    """Response with distinct activity types."""
    types: list[str]


class FilterOptionsResponse(BaseModel):
    """Response with filter options for a table."""
    source_systems: list[str]
    activity_types: list[str] | None = None  # Only for activities table


@router.get("/summary", response_model=DataSummaryResponse)
async def get_data_summary(
    organization_id: str,
) -> DataSummaryResponse:
    """Get counts for all synced data tables."""
    try:
        org_uuid = UUID(organization_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid organization ID")

    async with get_session() as session:
        # Count each table
        contacts_count = await session.scalar(
            select(func.count(Contact.id)).where(Contact.organization_id == org_uuid)
        ) or 0
        
        accounts_count = await session.scalar(
            select(func.count(Account.id)).where(Account.organization_id == org_uuid)
        ) or 0
        
        deals_count = await session.scalar(
            select(func.count(Deal.id)).where(Deal.organization_id == org_uuid)
        ) or 0
        
        activities_count = await session.scalar(
            select(func.count(Activity.id)).where(Activity.organization_id == org_uuid)
        ) or 0

    return DataSummaryResponse(
        organization_id=organization_id,
        tables=[
            TableSummary(name="contacts", display_name="Contacts", count=contacts_count),
            TableSummary(name="accounts", display_name="Accounts", count=accounts_count),
            TableSummary(name="deals", display_name="Deals", count=deals_count),
            TableSummary(name="activities", display_name="Activities", count=activities_count),
        ],
    )


@router.get("/activities/types", response_model=ActivityTypesResponse)
async def get_activity_types(
    organization_id: str,
) -> ActivityTypesResponse:
    """Get distinct activity types for filtering."""
    try:
        org_uuid = UUID(organization_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid organization ID")

    async with get_session() as session:
        result = await session.execute(
            select(Activity.type)
            .where(Activity.organization_id == org_uuid)
            .where(Activity.type.isnot(None))
            .distinct()
            .order_by(Activity.type)
        )
        types = [row[0] for row in result.fetchall() if row[0]]

    return ActivityTypesResponse(types=types)


@router.get("/{table}/filters", response_model=FilterOptionsResponse)
async def get_filter_options(
    table: str,
    organization_id: str,
) -> FilterOptionsResponse:
    """Get available filter options for a table."""
    try:
        org_uuid = UUID(organization_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid organization ID")

    table_models: dict[str, type] = {
        "contacts": Contact,
        "accounts": Account,
        "deals": Deal,
        "activities": Activity,
    }

    if table not in table_models:
        raise HTTPException(status_code=400, detail=f"Unknown table: {table}")

    model = table_models[table]

    async with get_session() as session:
        # Get distinct source systems
        result = await session.execute(
            select(model.source_system)
            .where(model.organization_id == org_uuid)
            .where(model.source_system.isnot(None))
            .distinct()
            .order_by(model.source_system)
        )
        source_systems = [row[0] for row in result.fetchall() if row[0]]

        # Get activity types if this is the activities table
        activity_types: list[str] | None = None
        if table == "activities":
            result = await session.execute(
                select(Activity.type)
                .where(Activity.organization_id == org_uuid)
                .where(Activity.type.isnot(None))
                .distinct()
                .order_by(Activity.type)
            )
            activity_types = [row[0] for row in result.fetchall() if row[0]]

    return FilterOptionsResponse(
        source_systems=source_systems,
        activity_types=activity_types,
    )


@router.get("/{table}", response_model=DataResponse)
async def get_data(
    table: str,
    organization_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    search: Optional[str] = None,
    sort_by: Optional[str] = None,
    sort_order: Literal["asc", "desc"] = "asc",
    type_filter: Optional[str] = None,
    source_system: Optional[str] = None,
) -> DataResponse:
    """Get paginated data from a synced table."""
    try:
        org_uuid = UUID(organization_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid organization ID")

    # Map table names to models and their display columns
    table_config: dict[str, dict] = {
        "contacts": {
            "model": Contact,
            "columns": ["name", "email", "title", "phone", "source_system"],
            "search_field": "name",
        },
        "accounts": {
            "model": Account,
            "columns": ["name", "domain", "industry", "employee_count", "source_system"],
            "search_field": "name",
        },
        "deals": {
            "model": Deal,
            "columns": ["name", "amount", "stage", "close_date", "source_system"],
            "search_field": "name",
        },
        "activities": {
            "model": Activity,
            "columns": ["type", "subject", "activity_date", "source_system"],
            "search_field": "subject",
        },
    }

    if table not in table_config:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown table: {table}. Available: {list(table_config.keys())}",
        )

    config = table_config[table]
    model = config["model"]
    columns: list[str] = config["columns"]

    async with get_session() as session:
        # Base query
        base_query = select(model).where(model.organization_id == org_uuid)

        # Add source system filter (works for all tables)
        if source_system and hasattr(model, "source_system"):
            base_query = base_query.where(model.source_system == source_system)

        # Add type filter for activities
        if table == "activities" and type_filter:
            base_query = base_query.where(Activity.type == type_filter)

        # Add search filter if provided
        if search and hasattr(model, config["search_field"]):
            search_field = getattr(model, config["search_field"])
            base_query = base_query.where(search_field.ilike(f"%{search}%"))

        # Get total count
        count_query = select(func.count()).select_from(base_query.subquery())
        total: int = await session.scalar(count_query) or 0

        # Determine sort column
        effective_sort_by: Optional[str] = None
        if sort_by and sort_by in columns and hasattr(model, sort_by):
            sort_column = getattr(model, sort_by)
            order_func = desc if sort_order == "desc" else asc
            base_query = base_query.order_by(order_func(sort_column))
            effective_sort_by = sort_by
        else:
            # Default sort by id
            base_query = base_query.order_by(model.id)

        # Get paginated results
        offset = (page - 1) * page_size
        results = await session.execute(
            base_query.offset(offset).limit(page_size)
        )
        rows_data = results.scalars().all()

        # Convert to response format
        rows: list[DataRow] = []
        for row in rows_data:
            row_data: dict[str, str | int | float | bool | None] = {}
            for col in columns:
                value = getattr(row, col, None)
                # Convert non-JSON-serializable types
                if hasattr(value, "isoformat"):
                    value = value.isoformat()
                elif isinstance(value, UUID):
                    value = str(value)
                row_data[col] = value
            rows.append(DataRow(id=str(row.id), data=row_data))

    return DataResponse(
        table=table,
        rows=rows,
        total=total,
        page=page,
        page_size=page_size,
        columns=columns,
        sort_by=effective_sort_by,
        sort_order=sort_order if effective_sort_by else None,
    )
