"""
Tool definitions and execution for Claude.

Tools are organized by category (see registry.py):
- LOCAL_READ: run_sql_query, search_activities
- LOCAL_WRITE: create_artifact, create_workflow, trigger_workflow
- EXTERNAL_READ: web_search, enrich_contacts_with_apollo, enrich_company_with_apollo
- EXTERNAL_WRITE: crm_write, send_email_from, send_slack, trigger_sync

EXTERNAL_WRITE tools require user approval by default (can be overridden in settings).
"""

import json
import logging
import re
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select, text

from config import settings
from models.account import Account
from models.artifact import Artifact
from models.contact import Contact
from models.crm_operation import CrmOperation
from models.database import get_session
from models.deal import Deal
from models.integration import Integration

# Import the unified tool registry
from agents.registry import get_tools_for_claude, get_tool, requires_approval

logger = logging.getLogger(__name__)

# =============================================================================
# Pending Operations Store (temporary until Phase 6 PendingOperation table)
# =============================================================================
# This stores pending operation params in memory so they can be executed on approval
# Key: operation_id, Value: {tool_name, params, organization_id, user_id, created_at}

from datetime import datetime as dt
from typing import TypedDict

class PendingOperationData(TypedDict):
    tool_name: str
    params: dict[str, Any]
    organization_id: str
    user_id: str
    created_at: str

# In-memory store for pending operations (will be replaced by database in Phase 6)
_pending_operations: dict[str, PendingOperationData] = {}

def store_pending_operation(
    operation_id: str,
    tool_name: str,
    params: dict[str, Any],
    organization_id: str,
    user_id: str,
) -> None:
    """Store a pending operation for later execution."""
    _pending_operations[operation_id] = {
        "tool_name": tool_name,
        "params": params,
        "organization_id": organization_id,
        "user_id": user_id,
        "created_at": dt.utcnow().isoformat(),
    }
    logger.info(f"[Tools] Stored pending operation {operation_id} for {tool_name}")

def get_pending_operation(operation_id: str) -> PendingOperationData | None:
    """Retrieve a pending operation by ID."""
    return _pending_operations.get(operation_id)

def remove_pending_operation(operation_id: str) -> None:
    """Remove a pending operation after execution."""
    if operation_id in _pending_operations:
        del _pending_operations[operation_id]
        logger.info(f"[Tools] Removed pending operation {operation_id}")

# Tables that are allowed to be queried (synced data only - no internal admin tables)
# Note: Row-Level Security (RLS) handles organization filtering at the database level
ALLOWED_TABLES: set[str] = {
    "deals", "accounts", "contacts", "activities", "meetings", "integrations", "users", "organizations",
    "pipelines", "pipeline_stages", "workflows", "workflow_runs"
}


def get_tools() -> list[dict[str, Any]]:
    """Return tool definitions for Claude from the unified registry."""
    return get_tools_for_claude()


async def _should_skip_approval(
    tool_name: str, 
    user_id: str, 
    context: dict[str, Any] | None
) -> bool:
    """
    Check if approval should be skipped for this tool execution.
    
    Approval is skipped if:
    1. The tool doesn't require approval by default, OR
    2. The user has enabled auto_approve for this tool, OR
    3. This is a workflow execution with this tool in auto_approve_tools
    
    Args:
        tool_name: Name of the tool
        user_id: User UUID
        context: Execution context (may contain workflow auto_approve_tools)
        
    Returns:
        True if approval should be skipped, False if approval required
    """
    # Check if tool requires approval by default
    if not requires_approval(tool_name):
        return True
    
    # Check workflow-specific auto-approve
    if context and context.get("is_workflow"):
        auto_approve_tools = context.get("auto_approve_tools", [])
        if tool_name in auto_approve_tools:
            logger.info(f"[Tools] Skipping approval for {tool_name} - workflow auto-approved")
            return True
    
    # Check user's global settings
    from api.routes.tool_settings import is_tool_auto_approved
    if await is_tool_auto_approved(UUID(user_id), tool_name):
        logger.info(f"[Tools] Skipping approval for {tool_name} - user auto-approved")
        return True
    
    return False


async def execute_tool(
    tool_name: str, 
    tool_input: dict[str, Any], 
    organization_id: str | None, 
    user_id: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Execute a tool and return results.
    
    Args:
        tool_name: Name of the tool to execute
        tool_input: Input parameters for the tool
        organization_id: Organization UUID (required for most tools)
        user_id: User UUID executing the tool
        context: Optional context containing:
            - is_workflow: bool - Whether this is a workflow execution
            - auto_approve_tools: list[str] - Tools auto-approved for this workflow
            
    Returns:
        Tool execution result or pending_approval status
    """
    logger.info(
        "[Tools] execute_tool called: %s | org_id=%s | user_id=%s | input=%s",
        tool_name,
        organization_id,
        user_id,
        tool_input,
    )
    
    # If no organization, tools that need org data won't work
    if organization_id is None:
        logger.warning("[Tools] No organization_id - returning error")
        return {"error": "No organization associated with user. Please complete onboarding."}
    
    # Check if this tool should bypass approval (for auto-approved workflows)
    skip_approval = await _should_skip_approval(tool_name, user_id, context)
    
    if tool_name == "run_sql_query":
        result = await _run_sql_query(tool_input, organization_id, user_id)
        logger.info("[Tools] run_sql_query returned %d rows", result.get("row_count", 0))
        return result

    elif tool_name == "search_activities":
        result = await _search_activities(tool_input, organization_id, user_id)
        logger.info("[Tools] search_activities returned %d results", len(result.get("results", [])))
        return result

    elif tool_name == "create_artifact":
        result = await _create_artifact(tool_input, organization_id, user_id)
        logger.info("[Tools] create_artifact result: %s", result)
        return result

    elif tool_name == "web_search":
        result = await _web_search(tool_input)
        logger.info("[Tools] web_search completed")
        return result

    elif tool_name == "crm_write":
        result = await _crm_write(tool_input, organization_id, user_id, skip_approval)
        logger.info("[Tools] crm_write completed: %s", result.get("status"))
        return result

    elif tool_name == "create_workflow":
        result = await _create_workflow(tool_input, organization_id, user_id)
        logger.info("[Tools] create_workflow completed: %s", result)
        return result

    elif tool_name == "trigger_workflow":
        result = await _trigger_workflow(tool_input, organization_id)
        logger.info("[Tools] trigger_workflow completed: %s", result)
        return result

    elif tool_name == "enrich_contacts_with_apollo":
        result = await _enrich_contacts_with_apollo(tool_input, organization_id)
        logger.info("[Tools] enrich_contacts_with_apollo completed: %d results", len(result.get("enriched", [])))
        return result

    elif tool_name == "enrich_company_with_apollo":
        result = await _enrich_company_with_apollo(tool_input, organization_id)
        logger.info("[Tools] enrich_company_with_apollo completed")
        return result

    elif tool_name == "send_email_from":
        result = await _send_email_from(tool_input, organization_id, user_id, skip_approval)
        logger.info("[Tools] send_email_from completed: %s", result.get("status"))
        return result

    elif tool_name == "send_slack":
        result = await _send_slack(tool_input, organization_id, user_id, skip_approval)
        logger.info("[Tools] send_slack completed: %s", result.get("status"))
        return result

    elif tool_name == "trigger_sync":
        result = await _trigger_sync(tool_input, organization_id)
        logger.info("[Tools] trigger_sync completed: %s", result.get("status"))
        return result

    else:
        logger.error("[Tools] Unknown tool: %s", tool_name)
        return {"error": f"Unknown tool: {tool_name}"}


def _validate_sql_query(query: str) -> tuple[bool, str | None]:
    """
    Validate that the SQL query is safe to execute.
    Returns (is_valid, error_message).
    """
    query_upper = query.upper().strip()
    
    # Must start with SELECT (or WITH for CTEs)
    if not (query_upper.startswith("SELECT") or query_upper.startswith("WITH")):
        return False, "Only SELECT queries are allowed"
    
    # Block dangerous keywords
    dangerous_keywords: list[str] = [
        "INSERT", "UPDATE", "DELETE", "DROP", "TRUNCATE", "ALTER", 
        "CREATE", "GRANT", "REVOKE", "EXECUTE", "EXEC",
        "INTO OUTFILE", "INTO DUMPFILE", "LOAD_FILE",
    ]
    for keyword in dangerous_keywords:
        # Check for keyword as whole word (not part of column name)
        if re.search(rf'\b{keyword}\b', query_upper):
            return False, f"'{keyword}' statements are not allowed"
    
    return True, None


def _extract_tables_from_query(query: str) -> set[str]:
    """Extract table names from a SQL query (best effort)."""
    tables: set[str] = set()
    
    # Match FROM and JOIN clauses
    patterns = [
        r'\bFROM\s+([a-zA-Z_][a-zA-Z0-9_]*)',
        r'\bJOIN\s+([a-zA-Z_][a-zA-Z0-9_]*)',
    ]
    
    for pattern in patterns:
        matches = re.findall(pattern, query, re.IGNORECASE)
        tables.update(m.lower() for m in matches)
    
    return tables


async def _run_sql_query(
    params: dict[str, Any], organization_id: str, user_id: str
) -> dict[str, Any]:
    """
    Execute a read-only SQL query with Row-Level Security (RLS).
    
    RLS is enforced at the database level via policies that check 
    `app.current_org_id` session variable. This handles all query patterns
    automatically (JOINs, subqueries, CTEs, etc.) without fragile SQL parsing.
    """
    query = params.get("query", "").strip()
    
    if not query:
        return {"error": "No query provided"}
    
    logger.info("[Tools._run_sql_query] Query: %s", query)
    
    # Validate query is safe (SELECT only, no dangerous keywords)
    is_valid, error = _validate_sql_query(query)
    if not is_valid:
        logger.warning("[Tools._run_sql_query] Query validation failed: %s", error)
        return {"error": error}
    
    # Extract tables and validate they're in the allowed list
    tables = _extract_tables_from_query(query)
    logger.debug("[Tools._run_sql_query] Detected tables: %s", tables)
    
    disallowed = tables - ALLOWED_TABLES
    if disallowed:
        return {"error": f"Access to tables not allowed: {disallowed}"}
    
    # Add LIMIT if not present to prevent huge result sets
    final_query = query
    if not re.search(r'\bLIMIT\b', final_query, re.IGNORECASE):
        final_query = final_query.rstrip(';') + " LIMIT 100"
    
    try:
        async with get_session() as session:
            # IMPORTANT: Set org context BEFORE any query for RLS to work.
            # Using false for is_local makes it session-level (persists for connection lifetime).
            # With NullPool, each request gets a fresh connection so this is safe.
            await session.execute(
                text("SELECT set_config('app.current_org_id', :org_id, false)"),
                {"org_id": organization_id}
            )
            
            # Execute the query - RLS automatically filters by organization
            result = await session.execute(text(final_query))
            rows = result.fetchall()
            columns = list(result.keys())
            
            # Convert to list of dicts for JSON serialization
            data: list[dict[str, Any]] = []
            for row in rows:
                row_dict: dict[str, Any] = {}
                for i, col in enumerate(columns):
                    value = row[i]
                    # Handle UUID and other non-JSON-serializable types
                    if hasattr(value, '__str__') and not isinstance(value, (str, int, float, bool, type(None), list, dict)):
                        row_dict[col] = str(value)
                    else:
                        row_dict[col] = value
                data.append(row_dict)
            
            logger.info("[Tools._run_sql_query] Query returned %d rows", len(data))
            
            return {
                "columns": columns,
                "rows": data,
                "row_count": len(data),
            }
    except Exception as e:
        logger.error("[Tools._run_sql_query] Query execution failed: %s", str(e))
        return {"error": f"Query execution failed: {str(e)}"}


async def _search_activities(
    params: dict[str, Any], organization_id: str, user_id: str
) -> dict[str, Any]:
    """Execute semantic search across activities."""
    query = params.get("query", "").strip()
    
    if not query:
        return {"error": "No search query provided"}
    
    activity_types = params.get("types")
    limit = min(params.get("limit", 10), 50)  # Cap at 50
    
    try:
        from services.embedding_sync import search_activities_by_embedding
        
        results = await search_activities_by_embedding(
            organization_id=organization_id,
            query_text=query,
            limit=limit,
            activity_types=activity_types,
        )
        
        if not results:
            return {
                "results": [],
                "message": "No matching activities found. Activities may not have embeddings yet - try syncing data first.",
            }
        
        return {
            "results": results,
            "count": len(results),
            "query": query,
        }
        
    except ValueError as e:
        # OpenAI API key not configured
        logger.warning("[Tools._search_activities] Embedding service not available: %s", e)
        return {
            "error": "Semantic search is not configured. OPENAI_API_KEY may be missing.",
            "suggestion": "Use run_sql_query with ILIKE for text search instead.",
        }
    except Exception as e:
        logger.error("[Tools._search_activities] Search failed: %s", str(e))
        return {"error": f"Search failed: {str(e)}"}


async def _create_artifact(
    data: dict[str, Any], organization_id: str, user_id: str
) -> dict[str, Any]:
    """Save an artifact."""
    async with get_session() as session:
        artifact = Artifact(
            user_id=UUID(user_id),
            organization_id=UUID(organization_id),
            type=data["type"],
            title=data["title"],
            description=data.get("description"),
            snapshot_data=data["data"],
            is_live=data.get("is_live", False),
        )
        session.add(artifact)
        await session.commit()
        await session.refresh(artifact)

        return {
            "success": True,
            "artifact_id": str(artifact.id),
            "url": f"/artifacts/{artifact.id}",
        }


async def _web_search(params: dict[str, Any]) -> dict[str, Any]:
    """Search the web using Perplexity's Sonar API."""
    query = params.get("query", "").strip()
    
    if not query:
        return {"error": "No search query provided"}
    
    if not settings.PERPLEXITY_API_KEY:
        return {
            "error": "Web search is not configured. PERPLEXITY_API_KEY is not set.",
            "suggestion": "Add PERPLEXITY_API_KEY to your environment variables.",
        }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://api.perplexity.ai/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.PERPLEXITY_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "sonar",
                    "messages": [
                        {
                            "role": "system",
                            "content": "You are a helpful research assistant. Provide concise, factual answers with relevant details. Focus on information useful for sales and business contexts.",
                        },
                        {
                            "role": "user",
                            "content": query,
                        },
                    ],
                },
            )
            
            if response.status_code != 200:
                logger.error("[Tools._web_search] API error: %s %s", response.status_code, response.text)
                return {"error": f"Search API error: {response.status_code}"}
            
            data = response.json()
            content: str = data["choices"][0]["message"]["content"]
            
            # Extract citations if available
            citations: list[str] = data.get("citations", [])
            
            result: dict[str, Any] = {
                "answer": content,
                "query": query,
            }
            
            if citations:
                result["sources"] = citations
            
            return result
            
    except httpx.TimeoutException:
        logger.error("[Tools._web_search] Request timed out")
        return {"error": "Search request timed out. Try a simpler query."}
    except Exception as e:
        logger.error("[Tools._web_search] Search failed: %s", str(e))
        return {"error": f"Search failed: {str(e)}"}


async def _crm_write(
    params: dict[str, Any], organization_id: str, user_id: str, skip_approval: bool = False
) -> dict[str, Any]:
    """
    Create or update CRM records with user approval workflow.
    
    This function validates input, checks for duplicates, and either:
    - Creates a pending CrmOperation for user approval (default)
    - Executes immediately if skip_approval is True (auto-approved user/workflow)
    
    Args:
        params: CRM operation parameters
        organization_id: Organization UUID
        user_id: User UUID
        skip_approval: If True, execute immediately without approval
    """
    target_system = params.get("target_system", "").lower()
    record_type = params.get("record_type", "").lower()
    operation = params.get("operation", "create").lower()
    records = params.get("records", [])
    
    # Validate inputs
    if target_system not in ["hubspot"]:
        return {"error": f"Unsupported CRM system: {target_system}. Currently only 'hubspot' is supported."}
    
    if record_type not in ["contact", "company", "deal"]:
        return {"error": f"Invalid record_type: {record_type}. Must be 'contact', 'company', or 'deal'."}
    
    if operation not in ["create", "update", "upsert"]:
        return {"error": f"Invalid operation: {operation}. Must be 'create', 'update', or 'upsert'."}
    
    if not records or not isinstance(records, list):
        return {"error": "No records provided. 'records' must be a non-empty array."}
    
    if len(records) > 100:
        return {"error": f"Too many records ({len(records)}). Maximum is 100 per operation."}
    
    # Check for active HubSpot integration
    async with get_session() as session:
        result = await session.execute(
            select(Integration).where(
                Integration.organization_id == UUID(organization_id),
                Integration.provider == target_system,
                Integration.is_active == True,
            )
        )
        integration = result.scalar_one_or_none()
        
        if not integration:
            return {
                "error": f"No active {target_system} integration found. Please connect {target_system} first.",
            }
    
    # Validate and normalize records
    validated_records: list[dict[str, Any]] = []
    duplicate_warnings: list[dict[str, Any]] = []
    validation_errors: list[str] = []
    
    for i, record in enumerate(records):
        if not isinstance(record, dict):
            validation_errors.append(f"Record {i+1} is not an object")
            continue
        
        # Validate required fields based on record type
        if record_type == "contact":
            if not record.get("email"):
                validation_errors.append(f"Record {i+1}: 'email' is required for contacts")
                continue
            # Normalize email
            record["email"] = record["email"].lower().strip()
            
        elif record_type == "company":
            if not record.get("name"):
                validation_errors.append(f"Record {i+1}: 'name' is required for companies")
                continue
            # Normalize domain if present
            if record.get("domain"):
                domain = record["domain"].lower().strip()
                # Remove protocol if present
                domain = domain.replace("https://", "").replace("http://", "")
                # Remove trailing slash and path
                domain = domain.split("/")[0]
                record["domain"] = domain
                
        elif record_type == "deal":
            if not record.get("dealname"):
                validation_errors.append(f"Record {i+1}: 'dealname' is required for deals")
                continue
        
        validated_records.append(record)
    
    if validation_errors:
        return {
            "error": "Validation failed",
            "validation_errors": validation_errors,
        }
    
    if not validated_records:
        return {"error": "No valid records after validation"}
    
    # Check for duplicates in HubSpot (for create/upsert operations)
    # Only check first 10 records to avoid API rate limits and connection pool exhaustion
    if operation in ["create", "upsert"] and target_system == "hubspot":
        try:
            from connectors.hubspot import HubSpotConnector
            connector = HubSpotConnector(organization_id)
            
            # Limit duplicate checks to first 10 records for performance
            records_to_check = validated_records[:10]
            
            for record in records_to_check:
                existing: dict[str, Any] | None = None
                
                try:
                    if record_type == "contact" and record.get("email"):
                        existing = await connector.find_contact_by_email(record["email"])
                        if existing:
                            duplicate_warnings.append({
                                "record": record,
                                "existing_id": existing["id"],
                                "existing": existing.get("properties", {}),
                                "match_field": "email",
                                "match_value": record["email"],
                            })
                            
                    elif record_type == "company" and record.get("domain"):
                        existing = await connector.find_company_by_domain(record["domain"])
                        if existing:
                            duplicate_warnings.append({
                                "record": record,
                                "existing_id": existing["id"],
                                "existing": existing.get("properties", {}),
                                "match_field": "domain",
                                "match_value": record["domain"],
                            })
                            
                    elif record_type == "deal" and record.get("dealname"):
                        existing = await connector.find_deal_by_name(record["dealname"])
                        if existing:
                            duplicate_warnings.append({
                                "record": record,
                                "existing_id": existing["id"],
                                "existing": existing.get("properties", {}),
                                "match_field": "dealname",
                                "match_value": record["dealname"],
                            })
                except Exception as e:
                    logger.warning("[Tools._crm_write] Error checking duplicate for record: %s", str(e))
                    # Continue with next record
                        
        except Exception as e:
            logger.warning("[Tools._crm_write] Error initializing connector for duplicate check: %s", str(e))
            # Continue without duplicate check - not a blocker
    
    # Create CrmOperation record
    async with get_session() as session:
        crm_operation = CrmOperation(
            organization_id=UUID(organization_id),
            user_id=UUID(user_id),
            target_system=target_system,
            record_type=record_type,
            operation=operation,
            status="pending",
            input_records=records,
            validated_records=validated_records,
            duplicate_warnings=duplicate_warnings if duplicate_warnings else None,
            record_count=len(validated_records),
        )
        session.add(crm_operation)
        await session.commit()
        await session.refresh(crm_operation)
        
        operation_id = str(crm_operation.id)
    
    # Calculate what will happen
    will_create = len(validated_records)
    will_skip = 0
    will_update = 0
    
    if operation == "create" and duplicate_warnings:
        will_skip = len(duplicate_warnings)
        will_create = len(validated_records) - will_skip
    elif operation == "upsert" and duplicate_warnings:
        will_update = len(duplicate_warnings)
        will_create = len(validated_records) - will_update
    
    # If skip_approval is True, execute immediately
    if skip_approval:
        logger.info("[Tools._crm_write] Auto-approved, executing immediately")
        result = await execute_crm_operation(operation_id, skip_duplicates=True)
        return result
    
    # Return preview for user approval
    return {
        "type": "pending_approval",
        "status": "pending_approval",
        "operation_id": operation_id,
        "target_system": target_system,
        "record_type": record_type,
        "operation": operation,
        "preview": {
            "records": validated_records,
            "record_count": len(validated_records),
            "will_create": will_create,
            "will_skip": will_skip,
            "will_update": will_update,
            "duplicate_warnings": duplicate_warnings,
        },
        "message": f"Prepared {len(validated_records)} {record_type}(s) to {operation} in {target_system}. Please review and click Approve to proceed.",
    }


async def execute_crm_operation(operation_id: str, skip_duplicates: bool = True) -> dict[str, Any]:
    """
    Execute a previously validated CRM operation.
    
    This is called when the user approves the operation.
    
    Args:
        operation_id: UUID of the CrmOperation to execute
        skip_duplicates: If True, skip records that already exist (for create operation)
        
    Returns:
        Result of the operation with success/failure details
    """
    async with get_session() as session:
        crm_op = await session.get(CrmOperation, UUID(operation_id))
        
        if not crm_op:
            return {"status": "failed", "message": "Operation not found", "error": f"Operation {operation_id} not found"}
        
        if crm_op.status != "pending":
            return {"status": "failed", "message": "Invalid state", "error": f"Operation is not pending (status: {crm_op.status})"}
        
        if crm_op.is_expired:
            crm_op.status = "expired"
            await session.commit()
            return {"status": "expired", "message": "Operation expired", "error": "Operation has expired. Please start again."}
        
        # Mark as executing
        crm_op.status = "executing"
        await session.commit()
    
    try:
        # Execute based on target system
        if crm_op.target_system == "hubspot":
            result = await _execute_hubspot_operation(crm_op, skip_duplicates)
        else:
            result = {"status": "failed", "message": "Unsupported system", "error": f"Unsupported target system: {crm_op.target_system}"}
        
        # Update operation with result
        async with get_session() as session:
            crm_op = await session.get(CrmOperation, UUID(operation_id))
            if crm_op:
                if "error" in result:
                    crm_op.status = "failed"
                    crm_op.error_message = result["error"]
                else:
                    crm_op.status = "completed"
                    crm_op.success_count = result.get("success_count", 0)
                    crm_op.failure_count = result.get("failure_count", 0)
                    crm_op.result = result
                crm_op.executed_at = datetime.utcnow()
                await session.commit()
        
        return result
        
    except Exception as e:
        logger.error("[Tools.execute_crm_operation] Error: %s", str(e))
        
        # Truncate error message for storage
        error_msg = str(e)[:500]
        
        # Update operation with error
        async with get_session() as session:
            crm_op = await session.get(CrmOperation, UUID(operation_id))
            if crm_op:
                crm_op.status = "failed"
                crm_op.error_message = error_msg
                crm_op.executed_at = datetime.utcnow()
                await session.commit()
        
        return {
            "status": "failed",
            "message": "Operation failed",
            "error": error_msg,
        }


def _validate_deal_required_fields(deals: list[dict[str, Any]]) -> str | None:
    """
    Validate that all deals have required pipeline and dealstage fields.
    
    Returns None if all deals are valid, or an error message describing the issues.
    """
    invalid_deals: list[str] = []
    
    for i, deal in enumerate(deals):
        missing: list[str] = []
        if not deal.get("pipeline"):
            missing.append("pipeline")
        if not deal.get("dealstage"):
            missing.append("dealstage")
        
        if missing:
            deal_name = deal.get("dealname", f"Deal #{i + 1}")
            invalid_deals.append(f"'{deal_name}' is missing: {', '.join(missing)}")
    
    if invalid_deals:
        return "Issues found: " + "; ".join(invalid_deals[:5])  # Limit to first 5 errors
    
    return None


async def _execute_hubspot_operation(
    crm_op: CrmOperation, skip_duplicates: bool
) -> dict[str, Any]:
    """Execute a HubSpot CRM operation."""
    from connectors.hubspot import HubSpotConnector
    
    connector = HubSpotConnector(str(crm_op.organization_id))
    
    records_to_process = crm_op.validated_records
    duplicate_ids: set[str] = set()
    
    # Get duplicate record identifiers to skip if needed
    if skip_duplicates and crm_op.duplicate_warnings:
        for warning in crm_op.duplicate_warnings:
            if crm_op.record_type == "contact":
                duplicate_ids.add(warning["record"].get("email", "").lower())
            elif crm_op.record_type == "company":
                duplicate_ids.add(warning["record"].get("domain", "").lower())
            elif crm_op.record_type == "deal":
                duplicate_ids.add(warning["record"].get("dealname", ""))
    
    # Filter out duplicates if skipping
    if skip_duplicates and duplicate_ids:
        filtered_records: list[dict[str, Any]] = []
        for record in records_to_process:
            identifier = ""
            if crm_op.record_type == "contact":
                identifier = record.get("email", "").lower()
            elif crm_op.record_type == "company":
                identifier = record.get("domain", "").lower()
            elif crm_op.record_type == "deal":
                identifier = record.get("dealname", "")
            
            if identifier not in duplicate_ids:
                filtered_records.append(record)
        records_to_process = filtered_records
    
    if not records_to_process:
        return {
            "status": "completed",
            "message": "No records to create (all were duplicates)",
            "success_count": 0,
            "failure_count": 0,
            "skipped_count": len(crm_op.validated_records),
            "created": [],
        }
    
    # Execute based on record type and operation
    created: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    
    try:
        if crm_op.operation == "create":
            if crm_op.record_type == "contact":
                if len(records_to_process) == 1:
                    result = await connector.create_contact(records_to_process[0])
                    created.append(result)
                else:
                    batch_result = await connector.create_contacts_batch(records_to_process)
                    created.extend(batch_result.get("results", []))
                    errors.extend(batch_result.get("errors", []))
                    
            elif crm_op.record_type == "company":
                if len(records_to_process) == 1:
                    result = await connector.create_company(records_to_process[0])
                    created.append(result)
                else:
                    batch_result = await connector.create_companies_batch(records_to_process)
                    created.extend(batch_result.get("results", []))
                    errors.extend(batch_result.get("errors", []))
                    
            elif crm_op.record_type == "deal":
                # Validate that all deals have required pipeline and dealstage fields
                missing_fields_errors = _validate_deal_required_fields(records_to_process)
                if missing_fields_errors:
                    return {
                        "status": "failed",
                        "message": "Deal creation failed: missing required fields",
                        "error": "Each deal must have both 'pipeline' and 'dealstage' specified. "
                                 + missing_fields_errors,
                    }
                
                if len(records_to_process) == 1:
                    result = await connector.create_deal(records_to_process[0])
                    created.append(result)
                else:
                    batch_result = await connector.create_deals_batch(records_to_process)
                    created.extend(batch_result.get("results", []))
                    errors.extend(batch_result.get("errors", []))
        
        # For upsert, we'd need to handle updates for existing records
        # This is a simplified implementation that just creates non-duplicates
        elif crm_op.operation == "upsert":
            # Same as create for now - updates would be handled separately
            if crm_op.record_type == "contact":
                batch_result = await connector.create_contacts_batch(records_to_process)
                created.extend(batch_result.get("results", []))
                errors.extend(batch_result.get("errors", []))
            elif crm_op.record_type == "company":
                batch_result = await connector.create_companies_batch(records_to_process)
                created.extend(batch_result.get("results", []))
                errors.extend(batch_result.get("errors", []))
            elif crm_op.record_type == "deal":
                # Validate that all deals have required pipeline and dealstage fields
                missing_fields_errors = _validate_deal_required_fields(records_to_process)
                if missing_fields_errors:
                    return {
                        "status": "failed",
                        "message": "Deal creation failed: missing required fields",
                        "error": "Each deal must have both 'pipeline' and 'dealstage' specified. "
                                 + missing_fields_errors,
                    }
                
                batch_result = await connector.create_deals_batch(records_to_process)
                created.extend(batch_result.get("results", []))
                errors.extend(batch_result.get("errors", []))
        
        elif crm_op.operation == "update":
            # Update existing records - each record must have an 'id' field
            for record in records_to_process:
                record_id = record.get("id")
                if not record_id:
                    errors.append({"record": record, "error": "Missing record ID for update"})
                    continue
                
                # Extract properties to update (everything except 'id')
                properties = {k: v for k, v in record.items() if k != "id"}
                
                try:
                    if crm_op.record_type == "contact":
                        result = await connector.update_contact(record_id, properties)
                        created.append(result)
                    elif crm_op.record_type == "company":
                        result = await connector.update_company(record_id, properties)
                        created.append(result)
                    elif crm_op.record_type == "deal":
                        result = await connector.update_deal(record_id, properties)
                        created.append(result)
                except Exception as update_err:
                    errors.append({"record": record, "error": str(update_err)})
                
    except Exception as e:
        logger.error("[Tools._execute_hubspot_operation] Error: %s", str(e))
        return {"status": "failed", "message": "HubSpot API error", "error": str(e)}
    
    skipped_count = len(crm_op.validated_records) - len(records_to_process)
    
    # Incremental sync: upsert created records to local database immediately
    # This avoids needing a full re-sync for user to see their new records
    synced_count = 0
    if created:
        try:
            synced_count = await _sync_created_records_to_db(
                crm_op.record_type, created, crm_op.organization_id
            )
            logger.info(
                "[Tools._execute_hubspot_operation] Synced %d new %s(s) to local DB",
                synced_count, crm_op.record_type
            )
        except Exception as sync_err:
            # Don't fail the operation if sync fails - records are in HubSpot
            logger.warning(
                "[Tools._execute_hubspot_operation] Failed to sync to local DB: %s",
                str(sync_err)
            )
    
    # Use appropriate verb based on operation type
    operation_verb = "Updated" if crm_op.operation == "update" else "Created"
    
    return {
        "status": "completed",
        "message": f"{operation_verb} {len(created)} {crm_op.record_type}(s) in HubSpot",
        "success_count": len(created),
        "failure_count": len(errors),
        "skipped_count": skipped_count,
        "synced_to_local": synced_count,
        "created": created,
        "errors": errors if errors else None,
    }


async def _sync_created_records_to_db(
    record_type: str,
    created_records: list[dict[str, Any]],
    organization_id: UUID,
) -> int:
    """
    Sync newly created CRM records to local database (incremental sync).
    
    This allows users to immediately see records they just created without
    waiting for a full sync cycle.
    
    Args:
        record_type: Type of record (contact, company, deal)
        created_records: List of records returned from HubSpot API
        organization_id: Organization UUID
        
    Returns:
        Number of records synced
    """
    synced = 0
    
    async with get_session() as session:
        for hs_record in created_records:
            hs_id = hs_record.get("id", "")
            properties = hs_record.get("properties", {})
            
            if not hs_id:
                continue
            
            try:
                if record_type == "contact":
                    # Build contact from HubSpot response
                    first_name = properties.get("firstname") or ""
                    last_name = properties.get("lastname") or ""
                    full_name = f"{first_name} {last_name}".strip()
                    if not full_name:
                        full_name = properties.get("email") or f"Contact {hs_id}"
                    
                    contact = Contact(
                        id=uuid4(),
                        organization_id=organization_id,
                        source_system="hubspot",
                        source_id=hs_id,
                        name=full_name,
                        email=properties.get("email"),
                        title=properties.get("jobtitle"),
                        phone=properties.get("phone"),
                    )
                    await session.merge(contact)
                    synced += 1
                    
                elif record_type == "company":
                    # Build account from HubSpot response
                    name = properties.get("name")
                    if not name:
                        name = properties.get("domain") or f"Company {hs_id}"
                    
                    account = Account(
                        id=uuid4(),
                        organization_id=organization_id,
                        source_system="hubspot",
                        source_id=hs_id,
                        name=name,
                        domain=properties.get("domain"),
                        industry=properties.get("industry"),
                    )
                    await session.merge(account)
                    synced += 1
                    
                elif record_type == "deal":
                    # Build deal from HubSpot response
                    from decimal import Decimal
                    
                    amount = None
                    if properties.get("amount"):
                        try:
                            amount = Decimal(str(properties["amount"]))
                        except (ValueError, TypeError):
                            pass
                    
                    deal = Deal(
                        id=uuid4(),
                        organization_id=organization_id,
                        source_system="hubspot",
                        source_id=hs_id,
                        name=properties.get("dealname") or "Untitled Deal",
                        amount=amount,
                        stage=properties.get("dealstage"),
                    )
                    await session.merge(deal)
                    synced += 1
                    
            except Exception as e:
                logger.warning(
                    "[Tools._sync_created_records_to_db] Failed to sync %s %s: %s",
                    record_type, hs_id, str(e)
                )
                continue
        
        await session.commit()
    
    return synced


async def cancel_crm_operation(operation_id: str) -> dict[str, Any]:
    """
    Cancel a pending CRM operation.
    
    Args:
        operation_id: UUID of the CrmOperation to cancel
        
    Returns:
        Confirmation of cancellation
    """
    async with get_session() as session:
        crm_op = await session.get(CrmOperation, UUID(operation_id))
        
        if not crm_op:
            return {"error": f"Operation {operation_id} not found"}
        
        if crm_op.status != "pending":
            return {"error": f"Operation is not pending (status: {crm_op.status})"}
        
        crm_op.status = "canceled"
        await session.commit()
        
        return {
            "status": "canceled",
            "message": "Operation canceled by user",
            "operation_id": operation_id,
        }


async def update_tool_call_result(operation_id: str, new_result: dict[str, Any]) -> bool:
    """
    Update the stored tool call result in chat_messages for a CRM operation.
    
    This ensures that when a conversation is reloaded, the tool call shows
    the final state (completed/failed/canceled) instead of pending_approval.
    
    Args:
        operation_id: UUID of the CRM operation
        new_result: The new result to store (includes status, message, etc.)
        
    Returns:
        True if updated successfully, False otherwise
    """
    from sqlalchemy import select
    from models.chat_message import ChatMessage
    
    try:
        async with get_session() as session:
            # Find messages that contain this operation_id in their tool_calls
            # We need to search for messages with tool_calls containing the operation_id
            result = await session.execute(
                select(ChatMessage).where(
                    ChatMessage.tool_calls.isnot(None)
                )
            )
            messages = result.scalars().all()
            
            updated_count = 0
            for msg in messages:
                if not msg.tool_calls:
                    continue
                    
                # Check if any tool call has this operation_id
                modified = False
                updated_tool_calls: list[dict[str, Any]] = []
                
                for tc in msg.tool_calls:
                    tc_result = tc.get("result", {})
                    if isinstance(tc_result, dict) and tc_result.get("operation_id") == operation_id:
                        # Update this tool call's result
                        tc["result"] = new_result
                        modified = True
                    updated_tool_calls.append(tc)
                
                if modified:
                    # Assign a new list to ensure SQLAlchemy detects the change
                    msg.tool_calls = updated_tool_calls
                    # Flag the attribute as modified for JSONB columns
                    from sqlalchemy.orm.attributes import flag_modified
                    flag_modified(msg, "tool_calls")
                    updated_count += 1
            
            if updated_count > 0:
                await session.commit()
                logger.info(
                    "[Tools.update_tool_call_result] Updated %d message(s) for operation %s",
                    updated_count,
                    operation_id
                )
                return True
            else:
                logger.warning(
                    "[Tools.update_tool_call_result] No messages found for operation %s",
                    operation_id
                )
                return False
                
    except Exception as e:
        logger.error(
            "[Tools.update_tool_call_result] Failed to update for operation %s: %s",
            operation_id,
            str(e)
        )
        return False


async def get_crm_operation_status(operation_id: str) -> dict[str, Any]:
    """
    Get the current status of a CRM operation.
    
    Used to check if a pending_approval operation has already been processed.
    
    Args:
        operation_id: UUID of the CRM operation
        
    Returns:
        Operation status and details
    """
    try:
        async with get_session() as session:
            crm_op = await session.get(CrmOperation, UUID(operation_id))
            
            if not crm_op:
                return {"error": f"Operation {operation_id} not found"}
            
            return crm_op.to_result_dict()
            
    except Exception as e:
        logger.error(
            "[Tools.get_crm_operation_status] Failed for operation %s: %s",
            operation_id,
            str(e)
        )
        return {"error": str(e)}


async def _create_workflow(
    params: dict[str, Any], organization_id: str, user_id: str
) -> dict[str, Any]:
    """
    Create a new workflow automation.
    
    Args:
        params: Workflow definition (name, trigger_type, trigger_config, steps)
        organization_id: Organization UUID
        user_id: User UUID (creator)
        
    Returns:
        Created workflow details
    """
    from models.workflow import Workflow
    
    name = params.get("name", "").strip()
    description = params.get("description", "")
    trigger_type = params.get("trigger_type", "manual")
    trigger_config = params.get("trigger_config", {})
    steps = params.get("steps", [])
    
    # Validate inputs
    if not name:
        return {"error": "Workflow name is required"}
    
    if trigger_type not in ("schedule", "event", "manual"):
        return {"error": f"Invalid trigger_type: {trigger_type}. Must be 'schedule', 'event', or 'manual'."}
    
    if trigger_type == "schedule" and not trigger_config.get("cron"):
        return {"error": "Schedule triggers require a cron expression in trigger_config.cron"}
    
    if trigger_type == "event" and not trigger_config.get("event"):
        return {"error": "Event triggers require an event type in trigger_config.event"}
    
    if not steps or not isinstance(steps, list):
        return {"error": "Workflow must have at least one step"}
    
    # Validate cron expression if provided
    if trigger_config.get("cron"):
        try:
            from croniter import croniter
            croniter(trigger_config["cron"])
        except Exception as e:
            return {"error": f"Invalid cron expression: {e}"}
    
    # Validate steps
    valid_actions = {"run_query", "llm", "send_slack", "send_system_email", "send_system_sms", "send_email_from", "sync", "query"}
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            return {"error": f"Step {i+1} is not a valid object"}
        
        action = step.get("action")
        if not action:
            return {"error": f"Step {i+1} is missing 'action'"}
        
        if action not in valid_actions:
            return {"error": f"Step {i+1} has invalid action '{action}'. Valid actions: {', '.join(valid_actions)}"}
    
    try:
        async with get_session() as session:
            workflow = Workflow(
                organization_id=UUID(organization_id),
                created_by_user_id=UUID(user_id),
                name=name,
                description=description or None,
                trigger_type=trigger_type,
                trigger_config=trigger_config,
                steps=steps,
                is_enabled=True,
            )
            session.add(workflow)
            await session.commit()
            await session.refresh(workflow)
            
            # Build trigger description
            trigger_desc = ""
            if trigger_type == "schedule":
                trigger_desc = f"Scheduled with cron: {trigger_config.get('cron')}"
            elif trigger_type == "event":
                trigger_desc = f"Triggered by event: {trigger_config.get('event')}"
            else:
                trigger_desc = "Manual trigger only"
            
            return {
                "success": True,
                "workflow_id": str(workflow.id),
                "name": name,
                "trigger": trigger_desc,
                "steps_count": len(steps),
                "message": f"Workflow '{name}' created successfully. You can view and manage it in the Automations tab.",
            }
            
    except Exception as e:
        logger.error("[Tools._create_workflow] Failed: %s", str(e))
        return {"error": f"Failed to create workflow: {str(e)}"}


async def _trigger_workflow(
    params: dict[str, Any], organization_id: str
) -> dict[str, Any]:
    """
    Manually trigger a workflow execution.
    
    Args:
        params: Contains workflow_id
        organization_id: Organization UUID
        
    Returns:
        Task ID and status
    """
    from models.workflow import Workflow
    
    workflow_id = params.get("workflow_id", "").strip()
    
    if not workflow_id:
        return {"error": "workflow_id is required"}
    
    try:
        # Verify workflow exists and belongs to org
        async with get_session() as session:
            result = await session.execute(
                select(Workflow).where(
                    Workflow.id == UUID(workflow_id),
                    Workflow.organization_id == UUID(organization_id),
                )
            )
            workflow = result.scalar_one_or_none()
            
            if not workflow:
                return {"error": f"Workflow {workflow_id} not found"}
            
            if not workflow.is_enabled:
                return {"error": "Workflow is disabled. Enable it first in the Automations tab."}
        
        # Queue execution via Celery
        from workers.tasks.workflows import execute_workflow
        task = execute_workflow.delay(workflow_id, "manual")
        
        return {
            "success": True,
            "task_id": task.id,
            "workflow_id": workflow_id,
            "workflow_name": workflow.name,
            "message": f"Workflow '{workflow.name}' triggered. Check the Automations tab for results.",
        }
        
    except Exception as e:
        logger.error("[Tools._trigger_workflow] Failed: %s", str(e))
        return {"error": f"Failed to trigger workflow: {str(e)}"}


async def _enrich_contacts_with_apollo(
    params: dict[str, Any], organization_id: str
) -> dict[str, Any]:
    """
    Enrich contacts using Apollo.io's database.
    
    Args:
        params: Contains contacts list, reveal_personal_emails, reveal_phone_numbers, limit
        organization_id: Organization UUID
        
    Returns:
        Enriched contact data
    """
    from connectors.apollo import ApolloConnector
    
    contacts = params.get("contacts", [])
    reveal_personal_emails = params.get("reveal_personal_emails", False)
    reveal_phone_numbers = params.get("reveal_phone_numbers", False)
    limit = min(params.get("limit", 50), 500)  # Cap at 500
    
    if not contacts:
        return {"error": "No contacts provided. 'contacts' must be a non-empty array."}
    
    if not isinstance(contacts, list):
        return {"error": "'contacts' must be an array of contact objects."}
    
    # Limit contacts
    contacts = contacts[:limit]
    
    # Check for active Apollo integration
    async with get_session() as session:
        result = await session.execute(
            select(Integration).where(
                Integration.organization_id == UUID(organization_id),
                Integration.provider == "apollo",
                Integration.is_active == True,
            )
        )
        integration = result.scalar_one_or_none()
        
        if not integration:
            return {
                "error": "No active Apollo.io integration found. Please connect Apollo first in Data Sources.",
                "suggestion": "Go to Data Sources and connect your Apollo.io API key.",
            }
    
    try:
        connector = ApolloConnector(organization_id)
        
        # Enrich contacts in bulk
        enriched_results = await connector.bulk_enrich_people(
            people=contacts,
            reveal_personal_emails=reveal_personal_emails,
            reveal_phone_number=reveal_phone_numbers,
        )
        
        # Pair original contacts with enrichment results
        enriched_contacts: list[dict[str, Any]] = []
        match_count = 0
        no_match_count = 0
        
        for i, enrichment in enumerate(enriched_results):
            original = contacts[i] if i < len(contacts) else {}
            
            if enrichment and enrichment.get("name"):
                match_count += 1
                enriched_contacts.append({
                    "original": original,
                    "enriched": enrichment,
                    "matched": True,
                })
            else:
                no_match_count += 1
                enriched_contacts.append({
                    "original": original,
                    "enriched": None,
                    "matched": False,
                })
        
        return {
            "success": True,
            "total": len(contacts),
            "matched": match_count,
            "not_matched": no_match_count,
            "enriched": enriched_contacts,
            "message": f"Enriched {match_count} of {len(contacts)} contacts. "
                       f"{'Use crm_write to update these contacts in your CRM.' if match_count > 0 else 'No matches found - try providing more identifying info (email, domain).'}",
        }
        
    except Exception as e:
        logger.error("[Tools._enrich_contacts_with_apollo] Failed: %s", str(e))
        return {"error": f"Apollo enrichment failed: {str(e)}"}


async def _enrich_company_with_apollo(
    params: dict[str, Any], organization_id: str
) -> dict[str, Any]:
    """
    Enrich a company using Apollo.io's database.
    
    Args:
        params: Contains domain
        organization_id: Organization UUID
        
    Returns:
        Enriched company data
    """
    from connectors.apollo import ApolloConnector
    
    domain = params.get("domain", "").strip().lower()
    
    if not domain:
        return {"error": "Company domain is required."}
    
    # Clean up domain
    domain = domain.replace("https://", "").replace("http://", "")
    domain = domain.split("/")[0]  # Remove path
    
    # Check for active Apollo integration
    async with get_session() as session:
        result = await session.execute(
            select(Integration).where(
                Integration.organization_id == UUID(organization_id),
                Integration.provider == "apollo",
                Integration.is_active == True,
            )
        )
        integration = result.scalar_one_or_none()
        
        if not integration:
            return {
                "error": "No active Apollo.io integration found. Please connect Apollo first in Data Sources.",
                "suggestion": "Go to Data Sources and connect your Apollo.io API key.",
            }
    
    try:
        connector = ApolloConnector(organization_id)
        
        enriched = await connector.enrich_organization(domain)
        
        if enriched:
            return {
                "success": True,
                "domain": domain,
                "company": enriched,
                "message": f"Found company data for {domain}: {enriched.get('name', 'Unknown')}",
            }
        else:
            return {
                "success": False,
                "domain": domain,
                "company": None,
                "message": f"No company data found for domain '{domain}' in Apollo's database.",
            }
        
    except Exception as e:
        logger.error("[Tools._enrich_company_with_apollo] Failed: %s", str(e))
        return {"error": f"Apollo company enrichment failed: {str(e)}"}


async def _send_email_from(
    params: dict[str, Any], organization_id: str, user_id: str, skip_approval: bool = False
) -> dict[str, Any]:
    """
    Send an email from the user's connected Gmail or Outlook account.
    
    This requires approval by default. If skip_approval is True (user auto-approved
    or workflow auto-approved), executes immediately.
    
    Args:
        params: Contains to, subject, body, cc, bcc
        organization_id: Organization UUID
        user_id: User UUID (whose email account to use)
        skip_approval: If True, send immediately without approval
        
    Returns:
        Pending approval preview, or send result if skip_approval
    """
    to = params.get("to", "").strip()
    subject = params.get("subject", "").strip()
    body = params.get("body", "").strip()
    cc = params.get("cc", [])
    bcc = params.get("bcc", [])
    
    if not to:
        return {"error": "Recipient email address (to) is required."}
    
    if not subject:
        return {"error": "Email subject is required."}
    
    if not body:
        return {"error": "Email body is required."}
    
    # Check for active email integration (Gmail or Microsoft)
    async with get_session() as session:
        result = await session.execute(
            select(Integration).where(
                Integration.organization_id == UUID(organization_id),
                Integration.user_id == UUID(user_id),
                Integration.provider.in_(["gmail", "microsoft_mail"]),
                Integration.is_active == True,
            )
        )
        integration = result.scalar_one_or_none()
        
        if not integration:
            return {
                "error": "No connected email account found. Please connect Gmail or Outlook in Data Sources.",
                "suggestion": "Go to Data Sources and connect your Gmail or Outlook account.",
            }
    
    # If skip_approval, execute immediately
    if skip_approval:
        logger.info("[Tools._send_email_from] Auto-approved, sending immediately")
        return await execute_send_email_from(params, organization_id, user_id)
    
    # Create pending operation for approval
    operation_id = str(uuid4())
    
    # Store the full params for later execution (temp solution until Phase 6)
    store_pending_operation(
        operation_id=operation_id,
        tool_name="send_email_from",
        params=params,
        organization_id=organization_id,
        user_id=user_id,
    )
    
    return {
        "type": "pending_approval",
        "status": "pending_approval",
        "operation_id": operation_id,
        "tool_name": "send_email_from",
        "preview": {
            "provider": integration.provider,
            "to": to,
            "subject": subject,
            "body": body[:500] + ("..." if len(body) > 500 else ""),
            "cc": cc,
            "bcc": bcc,
        },
        "message": f"Ready to send email to {to}. Please review and click Approve to send.",
    }


async def execute_send_email_from(
    params: dict[str, Any], organization_id: str, user_id: str
) -> dict[str, Any]:
    """
    Actually execute the email send (called after user approval).
    
    Args:
        params: Contains to, subject, body, cc, bcc
        organization_id: Organization UUID
        user_id: User UUID (whose email account to use)
        
    Returns:
        Success/failure result
    """
    from connectors.gmail import GmailConnector
    from connectors.microsoft_mail import MicrosoftMailConnector
    
    to = params.get("to", "").strip()
    subject = params.get("subject", "").strip()
    body = params.get("body", "").strip()
    cc = params.get("cc", [])
    bcc = params.get("bcc", [])
    
    # Get the user's email integration
    async with get_session() as session:
        result = await session.execute(
            select(Integration).where(
                Integration.organization_id == UUID(organization_id),
                Integration.user_id == UUID(user_id),
                Integration.provider.in_(["gmail", "microsoft_mail"]),
                Integration.is_active == True,
            )
        )
        integration = result.scalar_one_or_none()
        
        if not integration:
            return {
                "status": "failed",
                "error": "No connected email account found.",
            }
    
    try:
        # Create appropriate connector with user_id for per-user integrations
        if integration.provider == "gmail":
            connector = GmailConnector(
                organization_id=organization_id,
                user_id=user_id,
            )
        else:
            connector = MicrosoftMailConnector(
                organization_id=organization_id,
                user_id=user_id,
            )
        
        # Send the email
        result = await connector.send_email(
            to=to,
            subject=subject,
            body=body,
            cc=cc if cc else None,
            bcc=bcc if bcc else None,
        )
        
        if result.get("success"):
            logger.info(f"[Tools] Email sent via {integration.provider} to {to}")
            return {
                "status": "completed",
                "message": f"Email sent successfully to {to}",
                "provider": integration.provider,
                "to": to,
                "subject": subject,
            }
        else:
            return {
                "status": "failed",
                "error": result.get("error", "Failed to send email"),
            }
            
    except Exception as e:
        logger.error(f"[Tools._send_email_from] Failed: {str(e)}")
        return {
            "status": "failed",
            "error": str(e),
        }


async def _send_slack(
    params: dict[str, Any], organization_id: str, user_id: str, skip_approval: bool = False
) -> dict[str, Any]:
    """
    Post a message to a Slack channel.
    
    This requires approval by default. If skip_approval is True, posts immediately.
    
    Args:
        params: Contains channel, message, thread_ts
        organization_id: Organization UUID
        user_id: User UUID
        skip_approval: If True, post immediately without approval
        
    Returns:
        Pending approval preview, or post result if skip_approval
    """
    channel = params.get("channel", "").strip()
    message = params.get("message", "").strip()
    thread_ts = params.get("thread_ts")
    
    if not channel:
        return {"error": "Slack channel is required."}
    
    if not message:
        return {"error": "Message text is required."}
    
    # Check for active Slack integration
    async with get_session() as session:
        result = await session.execute(
            select(Integration).where(
                Integration.organization_id == UUID(organization_id),
                Integration.provider == "slack",
                Integration.is_active == True,
            )
        )
        integration = result.scalar_one_or_none()
        
        if not integration:
            return {
                "error": "No connected Slack workspace found. Please connect Slack in Data Sources.",
                "suggestion": "Go to Data Sources and connect your Slack workspace.",
            }
    
    # If skip_approval, execute immediately
    if skip_approval:
        logger.info("[Tools._send_slack] Auto-approved, posting immediately")
        return await execute_send_slack(params, organization_id)
    
    # Create pending operation for approval
    operation_id = str(uuid4())
    
    # Store the full params for later execution (temp solution until Phase 6)
    store_pending_operation(
        operation_id=operation_id,
        tool_name="send_slack",
        params=params,
        organization_id=organization_id,
        user_id=user_id,
    )
    
    return {
        "type": "pending_approval",
        "status": "pending_approval",
        "operation_id": operation_id,
        "tool_name": "send_slack",
        "preview": {
            "channel": channel,
            "message": message[:500] + ("..." if len(message) > 500 else ""),
            "thread_ts": thread_ts,
        },
        "message": f"Ready to post to {channel}. Please review and click Approve to send.",
    }


async def execute_send_slack(
    params: dict[str, Any], organization_id: str
) -> dict[str, Any]:
    """
    Actually execute the Slack post (called after user approval).
    
    Args:
        params: Contains channel, message, thread_ts
        organization_id: Organization UUID
        
    Returns:
        Success/failure result
    """
    from connectors.slack import SlackConnector
    
    channel = params.get("channel", "").strip()
    message = params.get("message", "").strip()
    thread_ts = params.get("thread_ts")
    
    # Get the Slack integration
    async with get_session() as session:
        result = await session.execute(
            select(Integration).where(
                Integration.organization_id == UUID(organization_id),
                Integration.provider == "slack",
                Integration.is_active == True,
            )
        )
        integration = result.scalar_one_or_none()
        
        if not integration:
            return {
                "status": "failed",
                "error": "No connected Slack workspace found.",
            }
    
    try:
        connector = SlackConnector(
            organization_id=organization_id,
            nango_connection_id=integration.nango_connection_id,
        )
        
        result = await connector.post_message(
            channel=channel,
            text=message,
            thread_ts=thread_ts,
        )
        
        logger.info(f"[Tools] Posted to Slack channel {channel}: {result.get('ts')}")
        return {
            "status": "completed",
            "message": f"Message posted to {channel}",
            "channel": channel,
            "ts": result.get("ts"),
        }
        
    except Exception as e:
        logger.error(f"[Tools._send_slack] Failed: {str(e)}")
        return {
            "status": "failed",
            "error": str(e),
        }


async def _trigger_sync(
    params: dict[str, Any], organization_id: str
) -> dict[str, Any]:
    """
    Trigger a data sync for a specific provider.
    
    This does NOT require approval as it's just refreshing data.
    
    Args:
        params: Contains provider
        organization_id: Organization UUID
        
    Returns:
        Sync status
    """
    provider = params.get("provider", "").strip().lower()
    
    if not provider:
        return {"error": "Provider is required (e.g., 'hubspot', 'gmail', 'salesforce')."}
    
    # Check for active integration
    async with get_session() as session:
        result = await session.execute(
            select(Integration).where(
                Integration.organization_id == UUID(organization_id),
                Integration.provider == provider,
                Integration.is_active == True,
            )
        )
        integration = result.scalar_one_or_none()
        
        if not integration:
            return {
                "error": f"No active {provider} integration found.",
                "suggestion": f"Go to Data Sources and connect {provider}.",
            }
    
    try:
        # Queue sync via Celery
        from workers.tasks.sync import sync_integration
        task = sync_integration.delay(organization_id, provider)
        
        return {
            "status": "queued",
            "message": f"Sync for {provider} has been queued. It may take a few minutes to complete.",
            "task_id": task.id,
            "provider": provider,
        }
        
    except Exception as e:
        logger.error(f"[Tools._trigger_sync] Failed: {str(e)}")
        return {"error": f"Failed to trigger sync: {str(e)}"}
