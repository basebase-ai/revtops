"""
Deals endpoints for fetching deal data.

Endpoints:
- GET /api/deals - List deals (optionally filtered by pipeline)
- GET /api/deals/pipelines - List pipelines
"""

import logging
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select

from models.database import get_session
from models.deal import Deal
from models.pipeline import Pipeline, PipelineStage

router = APIRouter()
logger = logging.getLogger(__name__)


class DealResponse(BaseModel):
    """Response model for a deal."""
    id: str
    name: str
    amount: Optional[float]
    stage: Optional[str]
    stage_probability: Optional[int]  # 0-100 from pipeline_stages, for prob-adjusted value
    close_date: Optional[str]
    pipeline_id: Optional[str]
    pipeline_name: Optional[str]
    source_system: Optional[str]  # e.g. hubspot; for building CRM deal links
    source_id: Optional[str]  # CRM deal id; for building CRM deal links


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

    logger.info(
        "Listing deals",
        extra={
            "organization_id": str(org_uuid),
            "pipeline_id": pipeline_id,
            "default_only": default_only,
            "open_only": open_only,
            "limit": limit,
        },
    )

    async with get_session(organization_id=str(org_uuid)) as session:

        # Subquery to get stage name and probability by matching source_id within the same pipeline
        stage_name_subquery = (
            select(
                PipelineStage.source_id,
                PipelineStage.pipeline_id,
                PipelineStage.name.label("stage_name"),
                PipelineStage.probability.label("stage_probability"),
            )
        ).subquery()

        # Build query - join with pipeline_stages to get human-readable stage name and probability
        query = (
            select(
                Deal.id,
                Deal.name,
                Deal.amount,
                Deal.stage,
                stage_name_subquery.c.stage_name,
                stage_name_subquery.c.stage_probability,
                Deal.close_date,
                Deal.pipeline_id,
                Pipeline.name.label("pipeline_name"),
                Deal.source_system,
                Deal.source_id,
            )
            .outerjoin(Pipeline, Deal.pipeline_id == Pipeline.id)
            .outerjoin(
                stage_name_subquery,
                (Deal.pipeline_id == stage_name_subquery.c.pipeline_id) &
                (Deal.stage == stage_name_subquery.c.source_id)
            )
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
            # Subquery to find closed stage source_ids for each pipeline
            # Note: Deal.stage stores the source_id, not the stage name
            closed_stages_subquery = (
                select(PipelineStage.source_id, PipelineStage.pipeline_id)
                .where(
                    (PipelineStage.is_closed_won == True) | 
                    (PipelineStage.is_closed_lost == True)
                )
            ).subquery()
            
            # Exclude deals where stage matches a closed stage source_id in the same pipeline
            query = query.outerjoin(
                closed_stages_subquery,
                (Deal.pipeline_id == closed_stages_subquery.c.pipeline_id) &
                (Deal.stage == closed_stages_subquery.c.source_id)
            ).where(closed_stages_subquery.c.source_id.is_(None))

        result = await session.execute(query)
        rows = result.fetchall()

        logger.info(
            "Fetched deals",
            extra={
                "organization_id": str(org_uuid),
                "deal_count": len(rows),
            },
        )

        deals = [
            DealResponse(
                id=str(row.id),
                name=row.name,
                amount=float(row.amount) if row.amount else None,
                stage=row.stage_name or row.stage,  # Use human-readable name, fallback to source_id
                stage_probability=int(row.stage_probability) if row.stage_probability is not None else None,
                close_date=row.close_date.isoformat() if row.close_date else None,
                pipeline_id=str(row.pipeline_id) if row.pipeline_id else None,
                pipeline_name=row.pipeline_name,
                source_system=getattr(row, "source_system", None),
                source_id=getattr(row, "source_id", None),
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

    logger.info(
        "Listing pipelines",
        extra={
            "organization_id": str(org_uuid),
        },
    )

    async with get_session(organization_id=str(org_uuid)) as session:

        # Fetch pipelines with stages
        query = (
            select(Pipeline)
            .where(Pipeline.organization_id == org_uuid)
            .order_by(Pipeline.display_order.asc().nullslast(), Pipeline.name)
        )
        result = await session.execute(query)
        pipelines_db = result.scalars().all()
        logger.info(
            "Fetched pipelines",
            extra={
                "organization_id": str(org_uuid),
                "pipeline_count": len(pipelines_db),
            },
        )

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
