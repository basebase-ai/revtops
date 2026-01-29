"""
Deals endpoints for fetching deal data.

Endpoints:
- GET /api/deals - List deals (optionally filtered by pipeline)
- GET /api/deals/pipelines - List pipelines
"""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, text

from models.database import get_session
from models.deal import Deal
from models.pipeline import Pipeline, PipelineStage

router = APIRouter()


class DealResponse(BaseModel):
    """Response model for a deal."""
    id: str
    name: str
    amount: Optional[float]
    stage: Optional[str]
    close_date: Optional[str]
    pipeline_id: Optional[str]
    pipeline_name: Optional[str]


class DealListResponse(BaseModel):
    """Response model for listing deals."""
    deals: list[DealResponse]
    total: int


class StageResponse(BaseModel):
    """Response model for a pipeline stage."""
    id: str
    name: str
    display_order: Optional[int]
    probability: Optional[int]
    is_closed_won: bool
    is_closed_lost: bool


class PipelineResponse(BaseModel):
    """Response model for a pipeline."""
    id: str
    name: str
    display_order: Optional[int]
    is_default: bool
    stages: list[StageResponse]


class PipelineListResponse(BaseModel):
    """Response model for listing pipelines."""
    pipelines: list[PipelineResponse]
    total: int


@router.get("", response_model=DealListResponse)
async def list_deals(
    organization_id: str = Query(..., description="Organization ID"),
    pipeline_id: Optional[str] = Query(None, description="Filter by pipeline ID"),
    default_only: bool = Query(False, description="Only return deals in the default pipeline"),
    open_only: bool = Query(False, description="Only return open deals (excludes closed won/lost)"),
    limit: int = Query(50, ge=1, le=200, description="Max results"),
) -> DealListResponse:
    """List deals for an organization, optionally filtered by pipeline."""
    try:
        org_uuid = UUID(organization_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid organization ID")

    async with get_session() as session:
        # Set org context for RLS
        await session.execute(
            text("SELECT set_config('app.current_org_id', :org_id, true)"),
            {"org_id": organization_id}
        )

        # Build query
        query = (
            select(
                Deal.id,
                Deal.name,
                Deal.amount,
                Deal.stage,
                Deal.close_date,
                Deal.pipeline_id,
                Pipeline.name.label("pipeline_name"),
            )
            .outerjoin(Pipeline, Deal.pipeline_id == Pipeline.id)
            .where(Deal.organization_id == org_uuid)
            .order_by(Deal.close_date.asc().nullslast(), Deal.name)
            .limit(limit)
        )

        if pipeline_id:
            try:
                pipeline_uuid = UUID(pipeline_id)
                query = query.where(Deal.pipeline_id == pipeline_uuid)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid pipeline ID")
        elif default_only:
            query = query.where(Pipeline.is_default == True)

        # Filter to only open deals (exclude closed won/lost stages)
        if open_only:
            # Subquery to find closed stage names for each pipeline
            closed_stages_subquery = (
                select(PipelineStage.name, PipelineStage.pipeline_id)
                .where(
                    (PipelineStage.is_closed_won == True) | 
                    (PipelineStage.is_closed_lost == True)
                )
            ).subquery()
            
            # Exclude deals where stage matches a closed stage in the same pipeline
            query = query.outerjoin(
                closed_stages_subquery,
                (Deal.pipeline_id == closed_stages_subquery.c.pipeline_id) &
                (Deal.stage == closed_stages_subquery.c.name)
            ).where(closed_stages_subquery.c.name.is_(None))

        result = await session.execute(query)
        rows = result.fetchall()

        deals = [
            DealResponse(
                id=str(row.id),
                name=row.name,
                amount=float(row.amount) if row.amount else None,
                stage=row.stage,
                close_date=row.close_date.isoformat() if row.close_date else None,
                pipeline_id=str(row.pipeline_id) if row.pipeline_id else None,
                pipeline_name=row.pipeline_name,
            )
            for row in rows
        ]

        return DealListResponse(deals=deals, total=len(deals))


@router.get("/pipelines", response_model=PipelineListResponse)
async def list_pipelines(
    organization_id: str = Query(..., description="Organization ID"),
) -> PipelineListResponse:
    """List all pipelines for an organization."""
    try:
        org_uuid = UUID(organization_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid organization ID")

    async with get_session() as session:
        # Set org context for RLS
        await session.execute(
            text("SELECT set_config('app.current_org_id', :org_id, true)"),
            {"org_id": organization_id}
        )

        # Fetch pipelines with stages
        query = (
            select(Pipeline)
            .where(Pipeline.organization_id == org_uuid)
            .order_by(Pipeline.display_order.asc().nullslast(), Pipeline.name)
        )
        result = await session.execute(query)
        pipelines_db = result.scalars().all()

        pipelines: list[PipelineResponse] = []
        for p in pipelines_db:
            # Fetch stages for this pipeline
            stages_query = (
                select(PipelineStage)
                .where(PipelineStage.pipeline_id == p.id)
                .order_by(PipelineStage.display_order.asc().nullslast())
            )
            stages_result = await session.execute(stages_query)
            stages_db = stages_result.scalars().all()

            stages = [
                StageResponse(
                    id=str(s.id),
                    name=s.name,
                    display_order=s.display_order,
                    probability=s.probability,
                    is_closed_won=s.is_closed_won,
                    is_closed_lost=s.is_closed_lost,
                )
                for s in stages_db
            ]

            pipelines.append(
                PipelineResponse(
                    id=str(p.id),
                    name=p.name,
                    display_order=p.display_order,
                    is_default=p.is_default,
                    stages=stages,
                )
            )

        return PipelineListResponse(pipelines=pipelines, total=len(pipelines))
