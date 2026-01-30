"""
Google Sheets import routes.

Endpoints for listing spreadsheets, previewing data with schema inference,
and importing data into the Revtops database.
"""

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from connectors.google_sheets import GoogleSheetsConnector
from models.database import get_session
from models.sheet_import import SheetImport
from models.user import User
from services.schema_inference import (
    get_target_schemas,
    infer_schema_mapping,
    validate_mapping,
)

router = APIRouter()


# =============================================================================
# Request/Response Models
# =============================================================================


class SpreadsheetInfo(BaseModel):
    """Basic spreadsheet info from Google Drive."""
    id: str
    name: str
    lastModified: Optional[str]
    owner: Optional[str]


class SpreadsheetListResponse(BaseModel):
    """Response for list spreadsheets endpoint."""
    spreadsheets: list[SpreadsheetInfo]


class TabPreview(BaseModel):
    """Preview data for a single tab."""
    tab_name: str
    headers: list[str]
    sample_rows: list[list[str]]
    row_count: int


class ColumnMapping(BaseModel):
    """Mapping from column to schema field."""
    tab_name: str
    entity_type: str  # "contact", "account", "deal"
    confidence: float
    column_mappings: dict[str, str]  # column_name -> field_name
    ignored_columns: list[str]
    notes: Optional[str]


class SpreadsheetPreviewResponse(BaseModel):
    """Response for spreadsheet preview with schema mappings."""
    spreadsheet_id: str
    title: str
    tabs: list[TabPreview]
    mappings: list[ColumnMapping]
    target_schemas: dict[str, dict[str, str]]


class TabMappingConfig(BaseModel):
    """Configuration for importing a single tab."""
    tab_name: str
    entity_type: str
    column_mappings: dict[str, str]
    skip_header_row: bool = True


class ImportRequest(BaseModel):
    """Request to start an import."""
    tab_mappings: list[TabMappingConfig]


class ImportResponse(BaseModel):
    """Response for import creation."""
    import_id: str
    status: str
    message: str


class ImportStatusResponse(BaseModel):
    """Response for import status check."""
    id: str
    status: str
    spreadsheet_name: Optional[str]
    results: Optional[dict[str, Any]]
    errors: Optional[list[dict[str, Any]]]
    error_message: Optional[str]
    created_at: Optional[str]
    started_at: Optional[str]
    completed_at: Optional[str]


# =============================================================================
# Endpoints
# =============================================================================


@router.get("/list", response_model=SpreadsheetListResponse)
async def list_spreadsheets(
    user_id: Optional[str] = None,
    organization_id: Optional[str] = None,
) -> SpreadsheetListResponse:
    """
    List spreadsheets accessible to the user.
    
    Requires Google Sheets integration to be connected.
    """
    org_id, usr_id = await _get_org_and_user(user_id, organization_id)
    
    try:
        connector = GoogleSheetsConnector(org_id, usr_id)
        spreadsheets = await connector.list_spreadsheets()
        
        return SpreadsheetListResponse(
            spreadsheets=[SpreadsheetInfo(**s) for s in spreadsheets]
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=500, 
            detail=f"Failed to list spreadsheets: {str(e)}"
        )


@router.get("/{spreadsheet_id}/preview", response_model=SpreadsheetPreviewResponse)
async def preview_spreadsheet(
    spreadsheet_id: str,
    user_id: Optional[str] = None,
    organization_id: Optional[str] = None,
) -> SpreadsheetPreviewResponse:
    """
    Get preview of spreadsheet with LLM-inferred schema mappings.
    
    Returns:
    - Tab names and sample data
    - Suggested entity types and column mappings
    - Target schemas for reference
    """
    org_id, usr_id = await _get_org_and_user(user_id, organization_id)
    
    try:
        connector = GoogleSheetsConnector(org_id, usr_id)
        preview = await connector.get_spreadsheet_preview(spreadsheet_id)
        
        # Run LLM schema inference
        inference_result = await infer_schema_mapping(preview["tabs"])
        
        return SpreadsheetPreviewResponse(
            spreadsheet_id=preview["spreadsheet_id"],
            title=preview["title"],
            tabs=[TabPreview(**t) for t in preview["tabs"]],
            mappings=[ColumnMapping(**m) for m in inference_result.get("mappings", [])],
            target_schemas=get_target_schemas(),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to preview spreadsheet: {str(e)}"
        )


@router.post("/{spreadsheet_id}/import", response_model=ImportResponse)
async def start_import(
    spreadsheet_id: str,
    request: ImportRequest,
    background_tasks: BackgroundTasks,
    user_id: Optional[str] = None,
    organization_id: Optional[str] = None,
) -> ImportResponse:
    """
    Start importing data from a spreadsheet.
    
    The import runs in the background. Use GET /imports/{import_id}
    to check status.
    """
    org_id, usr_id = await _get_org_and_user(user_id, organization_id)
    
    # Validate mappings
    for tab_mapping in request.tab_mappings:
        is_valid, error = validate_mapping(
            tab_mapping.entity_type,
            tab_mapping.column_mappings,
        )
        if not is_valid:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid mapping for tab '{tab_mapping.tab_name}': {error}"
            )
    
    # Get spreadsheet name for record
    spreadsheet_name: Optional[str] = None
    try:
        connector = GoogleSheetsConnector(org_id, usr_id)
        preview = await connector.get_spreadsheet_preview(spreadsheet_id)
        spreadsheet_name = preview.get("title")
    except Exception:
        pass  # Non-critical
    
    # Create import record
    async with get_session() as session:
        sheet_import = SheetImport(
            organization_id=UUID(org_id),
            user_id=UUID(usr_id),
            spreadsheet_id=spreadsheet_id,
            spreadsheet_name=spreadsheet_name,
            config={
                "tab_mappings": [m.model_dump() for m in request.tab_mappings]
            },
            status="pending",
        )
        session.add(sheet_import)
        await session.commit()
        await session.refresh(sheet_import)
        import_id = str(sheet_import.id)
    
    # Start import in background
    background_tasks.add_task(
        run_import,
        import_id=import_id,
        org_id=org_id,
        user_id=usr_id,
        spreadsheet_id=spreadsheet_id,
        tab_mappings=[m.model_dump() for m in request.tab_mappings],
    )
    
    return ImportResponse(
        import_id=import_id,
        status="pending",
        message="Import started. Check status with GET /sheets/imports/{import_id}",
    )


@router.get("/imports/{import_id}", response_model=ImportStatusResponse)
async def get_import_status(
    import_id: str,
    user_id: Optional[str] = None,
    organization_id: Optional[str] = None,
) -> ImportStatusResponse:
    """Get the status of an import job."""
    org_id, _ = await _get_org_and_user(user_id, organization_id)
    
    try:
        import_uuid = UUID(import_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid import ID")
    
    async with get_session() as session:
        sheet_import = await session.get(SheetImport, import_uuid)
        
        if not sheet_import:
            raise HTTPException(status_code=404, detail="Import not found")
        
        if str(sheet_import.organization_id) != org_id:
            raise HTTPException(status_code=403, detail="Access denied")
        
        return ImportStatusResponse(
            id=str(sheet_import.id),
            status=sheet_import.status,
            spreadsheet_name=sheet_import.spreadsheet_name,
            results=sheet_import.results,
            errors=sheet_import.errors,
            error_message=sheet_import.error_message,
            created_at=sheet_import.created_at.isoformat() + "Z" if sheet_import.created_at else None,
            started_at=sheet_import.started_at.isoformat() + "Z" if sheet_import.started_at else None,
            completed_at=sheet_import.completed_at.isoformat() + "Z" if sheet_import.completed_at else None,
        )


@router.get("/imports", response_model=list[ImportStatusResponse])
async def list_imports(
    user_id: Optional[str] = None,
    organization_id: Optional[str] = None,
    limit: int = 20,
) -> list[ImportStatusResponse]:
    """List recent imports for the organization."""
    org_id, _ = await _get_org_and_user(user_id, organization_id)
    
    async with get_session() as session:
        result = await session.execute(
            select(SheetImport)
            .where(SheetImport.organization_id == UUID(org_id))
            .order_by(SheetImport.created_at.desc())
            .limit(limit)
        )
        imports = result.scalars().all()
        
        return [
            ImportStatusResponse(
                id=str(i.id),
                status=i.status,
                spreadsheet_name=i.spreadsheet_name,
                results=i.results,
                errors=i.errors,
                error_message=i.error_message,
                created_at=i.created_at.isoformat() + "Z" if i.created_at else None,
                started_at=i.started_at.isoformat() + "Z" if i.started_at else None,
                completed_at=i.completed_at.isoformat() + "Z" if i.completed_at else None,
            )
            for i in imports
        ]


# =============================================================================
# Helper Functions
# =============================================================================


async def _get_org_and_user(
    user_id: Optional[str],
    organization_id: Optional[str],
) -> tuple[str, str]:
    """
    Get organization ID and user ID from request params.
    
    Returns: (organization_id, user_id)
    """
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    org_id: str = ""
    
    if organization_id:
        org_id = organization_id
    else:
        # Look up from user
        async with get_session() as session:
            try:
                user_uuid = UUID(user_id)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid user ID")
            
            user = await session.get(User, user_uuid)
            if not user or not user.organization_id:
                raise HTTPException(status_code=404, detail="User not found")
            org_id = str(user.organization_id)
    
    return org_id, user_id


async def run_import(
    import_id: str,
    org_id: str,
    user_id: str,
    spreadsheet_id: str,
    tab_mappings: list[dict[str, Any]],
) -> None:
    """
    Background task to run the actual import.
    
    Updates the SheetImport record with progress and results.
    """
    import_uuid = UUID(import_id)
    
    # Mark as processing
    async with get_session() as session:
        sheet_import = await session.get(SheetImport, import_uuid)
        if sheet_import:
            sheet_import.status = "processing"
            sheet_import.started_at = datetime.utcnow()
            await session.commit()
    
    try:
        connector = GoogleSheetsConnector(org_id, user_id)
        results = await connector.import_data(spreadsheet_id, tab_mappings)
        
        # Mark as completed
        async with get_session() as session:
            sheet_import = await session.get(SheetImport, import_uuid)
            if sheet_import:
                sheet_import.status = "completed"
                sheet_import.completed_at = datetime.utcnow()
                sheet_import.results = {
                    "created": results["created"],
                    "updated": results["updated"],
                    "skipped": results["skipped"],
                    "total_errors": results["total_errors"],
                }
                sheet_import.errors = results["errors"][:100] if results["errors"] else None
                await session.commit()
                
    except Exception as e:
        # Mark as failed
        async with get_session() as session:
            sheet_import = await session.get(SheetImport, import_uuid)
            if sheet_import:
                sheet_import.status = "failed"
                sheet_import.completed_at = datetime.utcnow()
                sheet_import.error_message = str(e)[:500]
                await session.commit()
