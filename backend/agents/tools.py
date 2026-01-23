"""
Tool definitions and execution for Claude.

Tools:
- run_sql_query: Execute arbitrary SELECT queries (read-only)
- search_activities: Semantic search across emails, meetings, messages
- create_artifact: Save analysis/dashboard
- web_search: Search the web and get summarized results
- crm_write: Create/update records in CRM (with user approval)
"""

import logging
import re
from datetime import datetime
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import select, text

from config import settings
from models.artifact import Artifact
from models.crm_operation import CrmOperation
from models.database import get_session
from models.integration import Integration

logger = logging.getLogger(__name__)

# Tables that require organization_id filtering for multi-tenancy
ORG_SCOPED_TABLES: set[str] = {
    "deals", "accounts", "contacts", "activities", "integrations", "artifacts"
}

# Tables that are allowed to be queried
ALLOWED_TABLES: set[str] = {
    "deals", "accounts", "contacts", "activities", "users", "integrations", "organizations"
}


def get_tools() -> list[dict[str, Any]]:
    """Return tool definitions for Claude."""
    return [
        {
            "name": "run_sql_query",
            "description": """Execute a read-only SQL SELECT query against the database.
            
Use this for any data analysis: filtering, joins, aggregations, date comparisons, etc.
The query is automatically scoped to the user's organization for multi-tenant tables.

Examples:
- SELECT * FROM deals WHERE stage = 'closedwon' LIMIT 10
- SELECT stage, COUNT(*), SUM(amount) FROM deals GROUP BY stage
- SELECT d.name, a.name as account FROM deals d LEFT JOIN accounts a ON d.account_id = a.id
- SELECT * FROM deals WHERE close_date BETWEEN '2026-01-01' AND '2026-01-31'
- SELECT * FROM deals WHERE custom_fields->>'pipeline' = 'enterprise'

IMPORTANT: Only SELECT queries are allowed. No INSERT, UPDATE, DELETE, DROP, etc.""",
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The SQL SELECT query to execute",
                    },
                },
                "required": ["query"],
            },
        },
        {
            "name": "search_activities",
            "description": """Semantic search across emails, meetings, slack messages, and other activities.

Use this when the user wants to find activities by meaning/concept rather than exact text.
This searches the content of emails, meeting subjects, slack messages, etc.

Examples:
- "Find emails about pricing negotiations"
- "Search for meetings discussing the Q4 roadmap"
- "Look for communications about contract renewal"

For exact text matching (e.g., emails from a specific domain), use run_sql_query instead.""",
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language search query describing what to find",
                    },
                    "types": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional filter by activity type: 'email', 'meeting', 'call', etc.",
                    },
                    "sources": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional filter by source: 'gmail', 'microsoft_mail', 'google_calendar', 'microsoft_calendar', 'slack'",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results to return (default 10)",
                        "default": 10,
                    },
                },
                "required": ["query"],
            },
        },
        {
            "name": "create_artifact",
            "description": "Save an analysis, report, or dashboard for the user to view later.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["dashboard", "report", "analysis"],
                        "description": "Type of artifact to create",
                    },
                    "title": {
                        "type": "string",
                        "description": "Title of the artifact",
                    },
                    "description": {
                        "type": "string",
                        "description": "Description of what this artifact contains",
                    },
                    "data": {
                        "type": "object",
                        "description": "The analysis data/content",
                    },
                    "is_live": {
                        "type": "boolean",
                        "description": "Whether to refresh data on load (true) or keep static snapshot (false)",
                        "default": False,
                    },
                },
                "required": ["type", "title", "data"],
            },
        },
        {
            "name": "web_search",
            "description": """Search the web for real-time information and get summarized results.

Use this tool when you need external information not available in the user's data:
- Industry benchmarks or best practices (e.g., "average SaaS close rates")
- Company information not in the CRM (e.g., "what does Acme Corp do")
- Market trends or competitor analysis
- Current events or news about companies
- Sales methodologies or frameworks (e.g., "MEDDIC qualification")

Examples:
- "What is the typical close rate for enterprise SaaS deals?"
- "Latest news about TechCorp acquisition"
- "BANT vs MEDDIC sales qualification frameworks"

Do NOT use this for data that's in the user's database - use run_sql_query instead.""",
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query - be specific and include relevant context",
                    },
                },
                "required": ["query"],
            },
        },
        {
            "name": "crm_write",
            "description": """Create or update records in the CRM (HubSpot).

This tool validates the input, checks for duplicates, and shows the user a preview 
with Approve/Cancel buttons. The operation only executes after user approval.

Use this for:
- Creating contacts from prospect lists
- Creating companies from account data
- Creating deals from opportunity information
- Updating existing records

The tool returns a preview with status "pending_approval". The user will see 
Approve/Cancel buttons. Tell them to review and click Approve to proceed.

Property names for each record type:
- contact: email (required), firstname, lastname, company, jobtitle, phone
- company: name (required), domain, industry, numberofemployees
- deal: dealname (required), amount, dealstage, closedate, pipeline""",
            "input_schema": {
                "type": "object",
                "properties": {
                    "target_system": {
                        "type": "string",
                        "enum": ["hubspot"],
                        "description": "The CRM system to write to",
                    },
                    "record_type": {
                        "type": "string",
                        "enum": ["contact", "company", "deal"],
                        "description": "Type of CRM record to create/update",
                    },
                    "operation": {
                        "type": "string",
                        "enum": ["create", "update", "upsert"],
                        "description": "Operation to perform. 'upsert' will update if exists, create if not.",
                    },
                    "records": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "Array of records to write. Each record should have the appropriate properties for the record_type.",
                    },
                },
                "required": ["target_system", "record_type", "operation", "records"],
            },
        },
    ]


async def execute_tool(
    tool_name: str, tool_input: dict[str, Any], organization_id: str | None, user_id: str
) -> dict[str, Any]:
    """Execute a tool and return results."""
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
        result = await _crm_write(tool_input, organization_id, user_id)
        logger.info("[Tools] crm_write completed: %s", result.get("status"))
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
    query_upper = query.upper()
    
    # Match FROM and JOIN clauses
    # This is a simplified parser - handles common cases
    patterns = [
        r'\bFROM\s+([a-zA-Z_][a-zA-Z0-9_]*)',
        r'\bJOIN\s+([a-zA-Z_][a-zA-Z0-9_]*)',
    ]
    
    for pattern in patterns:
        matches = re.findall(pattern, query, re.IGNORECASE)
        tables.update(m.lower() for m in matches)
    
    return tables


def _inject_org_filter(query: str, organization_id: str, tables: set[str]) -> str:
    """
    Inject organization_id filter into the query for multi-tenant tables.
    This is a simplified approach - wraps the query as a CTE and adds filters.
    """
    org_tables = tables & ORG_SCOPED_TABLES
    
    if not org_tables:
        return query
    
    # Build WHERE conditions for org-scoped tables
    # We wrap the original query and add a filter
    # This approach works for simple queries; complex queries may need adjustment
    
    # For single-table queries, we can inject directly
    if len(tables) == 1 and len(org_tables) == 1:
        table = list(org_tables)[0]
        # Check if WHERE already exists
        if re.search(r'\bWHERE\b', query, re.IGNORECASE):
            # Add to existing WHERE
            query = re.sub(
                r'\bWHERE\b',
                f"WHERE {table}.organization_id = '{organization_id}' AND ",
                query,
                count=1,
                flags=re.IGNORECASE
            )
        else:
            # Find position to insert WHERE (before GROUP BY, ORDER BY, LIMIT, or end)
            insert_patterns = [
                (r'\bGROUP\s+BY\b', 'GROUP BY'),
                (r'\bORDER\s+BY\b', 'ORDER BY'),
                (r'\bLIMIT\b', 'LIMIT'),
                (r'\bHAVING\b', 'HAVING'),
            ]
            
            insert_pos = len(query)
            for pattern, _ in insert_patterns:
                match = re.search(pattern, query, re.IGNORECASE)
                if match and match.start() < insert_pos:
                    insert_pos = match.start()
            
            where_clause = f" WHERE {table}.organization_id = '{organization_id}' "
            query = query[:insert_pos] + where_clause + query[insert_pos:]
    else:
        # For multi-table queries, wrap in CTE with filter
        # This is safer but may not work for all query types
        filter_conditions = " AND ".join(
            f"{t}.organization_id = '{organization_id}'" for t in org_tables
        )
        
        if re.search(r'\bWHERE\b', query, re.IGNORECASE):
            query = re.sub(
                r'\bWHERE\b',
                f"WHERE ({filter_conditions}) AND ",
                query,
                count=1,
                flags=re.IGNORECASE
            )
        else:
            # Find the right place to insert
            insert_patterns = [
                (r'\bGROUP\s+BY\b', 'GROUP BY'),
                (r'\bORDER\s+BY\b', 'ORDER BY'),
                (r'\bLIMIT\b', 'LIMIT'),
                (r'\bHAVING\b', 'HAVING'),
            ]
            
            insert_pos = len(query)
            for pattern, _ in insert_patterns:
                match = re.search(pattern, query, re.IGNORECASE)
                if match and match.start() < insert_pos:
                    insert_pos = match.start()
            
            where_clause = f" WHERE {filter_conditions} "
            query = query[:insert_pos] + where_clause + query[insert_pos:]
    
    return query


async def _run_sql_query(
    params: dict[str, Any], organization_id: str, user_id: str
) -> dict[str, Any]:
    """Execute a read-only SQL query with organization scoping."""
    query = params.get("query", "").strip()
    
    if not query:
        return {"error": "No query provided"}
    
    logger.info("[Tools._run_sql_query] Original query: %s", query)
    
    # Validate query is safe
    is_valid, error = _validate_sql_query(query)
    if not is_valid:
        logger.warning("[Tools._run_sql_query] Query validation failed: %s", error)
        return {"error": error}
    
    # Extract tables and validate they're allowed
    tables = _extract_tables_from_query(query)
    logger.debug("[Tools._run_sql_query] Detected tables: %s", tables)
    
    disallowed = tables - ALLOWED_TABLES
    if disallowed:
        return {"error": f"Access to tables not allowed: {disallowed}"}
    
    # Inject organization filter for multi-tenant tables
    filtered_query = _inject_org_filter(query, organization_id, tables)
    logger.info("[Tools._run_sql_query] Filtered query: %s", filtered_query)
    
    # Add LIMIT if not present to prevent huge result sets
    if not re.search(r'\bLIMIT\b', filtered_query, re.IGNORECASE):
        filtered_query = filtered_query.rstrip(';') + " LIMIT 100"
    
    try:
        async with get_session() as session:
            result = await session.execute(text(filtered_query))
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
    source_systems = params.get("sources")
    limit = min(params.get("limit", 10), 50)  # Cap at 50
    
    try:
        from services.embedding_sync import search_activities_by_embedding
        
        results = await search_activities_by_embedding(
            organization_id=organization_id,
            query_text=query,
            limit=limit,
            activity_types=activity_types,
            source_systems=source_systems,
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
    params: dict[str, Any], organization_id: str, user_id: str
) -> dict[str, Any]:
    """
    Create or update CRM records with user approval workflow.
    
    This function validates input, checks for duplicates, creates a pending
    CrmOperation, and returns a preview for user approval.
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
    if operation in ["create", "upsert"] and target_system == "hubspot":
        try:
            from connectors.hubspot import HubSpotConnector
            connector = HubSpotConnector(organization_id)
            
            for record in validated_records:
                existing: dict[str, Any] | None = None
                
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
            logger.warning("[Tools._crm_write] Error checking for duplicates: %s", str(e))
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
            return {"error": f"Operation {operation_id} not found"}
        
        if crm_op.status != "pending":
            return {"error": f"Operation is not pending (status: {crm_op.status})"}
        
        if crm_op.is_expired:
            crm_op.status = "expired"
            await session.commit()
            return {"error": "Operation has expired. Please start again."}
        
        # Mark as executing
        crm_op.status = "executing"
        await session.commit()
    
    try:
        # Execute based on target system
        if crm_op.target_system == "hubspot":
            result = await _execute_hubspot_operation(crm_op, skip_duplicates)
        else:
            result = {"error": f"Unsupported target system: {crm_op.target_system}"}
        
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
        
        # Update operation with error
        async with get_session() as session:
            crm_op = await session.get(CrmOperation, UUID(operation_id))
            if crm_op:
                crm_op.status = "failed"
                crm_op.error_message = str(e)[:500]
                crm_op.executed_at = datetime.utcnow()
                await session.commit()
        
        return {"error": f"Operation failed: {str(e)}"}


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
                batch_result = await connector.create_deals_batch(records_to_process)
                created.extend(batch_result.get("results", []))
                errors.extend(batch_result.get("errors", []))
                
    except Exception as e:
        logger.error("[Tools._execute_hubspot_operation] Error: %s", str(e))
        return {"error": f"HubSpot API error: {str(e)}"}
    
    skipped_count = len(crm_op.validated_records) - len(records_to_process)
    
    return {
        "status": "completed",
        "message": f"Created {len(created)} {crm_op.record_type}(s) in HubSpot",
        "success_count": len(created),
        "failure_count": len(errors),
        "skipped_count": skipped_count,
        "created": created,
        "errors": errors if errors else None,
    }


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
