"""
Tool definitions and execution for Claude.

Tools:
- run_sql_query: Execute arbitrary SELECT queries (read-only)
- search_activities: Semantic search across emails, meetings, messages
- create_artifact: Save analysis/dashboard
- web_search: Search the web and get summarized results
- crm_write: Create/update records in CRM (with user approval)
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

logger = logging.getLogger(__name__)

# Tables that are allowed to be queried (synced data only - no internal admin tables)
# Note: Row-Level Security (RLS) handles organization filtering at the database level
ALLOWED_TABLES: set[str] = {
    "deals", "accounts", "contacts", "activities", "meetings", "integrations", "users", "organizations",
    "pipelines", "pipeline_stages", "workflows", "workflow_runs"
}


def get_tools() -> list[dict[str, Any]]:
    """Return tool definitions for Claude."""
    return [
        {
            "name": "run_sql_query",
            "description": """Execute a read-only SQL SELECT query against the database.
            
Use this for any data analysis: filtering, joins, aggregations, date comparisons, etc.
The query is automatically scoped to the user's organization for multi-tenant tables.

Available tables:
- meetings: Canonical meeting entities - deduplicated across all sources (title, scheduled_start, participants, summary, action_items, key_topics)
- deals: Sales opportunities (name, amount, stage, close_date, owner_id, account_id, pipeline_id)
- accounts: Companies/customers (name, domain, industry, employee_count, annual_revenue)
- contacts: People at accounts (name, email, title, phone, account_id)
- activities: Raw activity records - query by TYPE not source (type, subject, description, activity_date, meeting_id)
- pipelines: Sales pipelines (name, display_order, is_default)
- pipeline_stages: Stages in pipelines (pipeline_id, name, probability, is_closed_won)
- integrations: Connected data sources (provider, is_active, last_sync_at)
- users: Team members (email, name, role)
- organizations: User's company info (name, logo_url)

IMPORTANT: Query activities by TYPE, not source_system:
- type = 'email' for all emails
- type = 'meeting' for calendar events
- type = 'meeting_transcript' for transcripts
- type = 'slack_message' for messages

Examples:
- SELECT title, scheduled_start, summary, action_items FROM meetings ORDER BY scheduled_start DESC LIMIT 10
- SELECT * FROM meetings WHERE scheduled_start >= CURRENT_DATE AND scheduled_start < CURRENT_DATE + interval '7 days'
- SELECT * FROM activities WHERE type = 'email' ORDER BY activity_date DESC LIMIT 20
- SELECT stage, COUNT(*), SUM(amount) FROM deals GROUP BY stage
- SELECT d.name, a.name as account FROM deals d LEFT JOIN accounts a ON d.account_id = a.id

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
            "description": """Semantic search across emails, meetings, messages, and other activities.

Use this when the user wants to find activities by meaning/concept rather than exact text.
This searches the content of emails, meeting transcripts, messages, etc.

Examples:
- "Find emails about pricing negotiations"
- "Search for meeting discussions about the Q4 roadmap"
- "Look for communications about contract renewal"

For exact text matching (e.g., emails from a specific domain), use run_sql_query instead.
For meeting information (participants, schedule, summaries), query the meetings table directly.""",
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
                        "description": "Filter by activity type: 'email', 'meeting', 'meeting_transcript', 'slack_message', 'call'",
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
- deal: dealname (required), amount, dealstage, closedate, pipeline

IMPORTANT: HubSpot industry field requires specific enum values. Common values:
COMPUTER_SOFTWARE, INFORMATION_TECHNOLOGY_AND_SERVICES, INTERNET, COMPUTER_HARDWARE,
COMPUTER_NETWORKING, MARKETING_AND_ADVERTISING, FINANCIAL_SERVICES, MANAGEMENT_CONSULTING,
BANKING, RETAIL, HEALTH_WELLNESS_AND_FITNESS, HOSPITAL_HEALTH_CARE, MEDICAL_DEVICES,
EDUCATION_MANAGEMENT, E_LEARNING, REAL_ESTATE, CONSTRUCTION, ENTERTAINMENT, MEDIA_PRODUCTION,
TELECOMMUNICATIONS, AUTOMOTIVE, FOOD_BEVERAGES, CONSUMER_GOODS, PHARMACEUTICALS, BIOTECHNOLOGY,
INSURANCE, LEGAL_SERVICES, ACCOUNTING, STAFFING_AND_RECRUITING, VENTURE_CAPITAL_PRIVATE_EQUITY.
Do NOT use freeform values like "Technology" - always use the exact enum values.""",
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
        {
            "name": "create_workflow",
            "description": """Create a workflow automation that runs on a schedule or in response to events.

Use this when users want to automate recurring tasks like:
- "Every morning, send me a summary of stale deals to Slack"
- "When a sync completes, analyze new data and email me insights"
- "Daily at 9am, check for deals without activity in 30 days"

## Trigger Types
- **schedule**: Runs on a cron schedule (e.g., "0 9 * * *" = 9am daily)
- **event**: Runs when an event occurs (e.g., "sync.completed")
- **manual**: Only runs when triggered manually

## Available Actions

Each workflow has a list of steps that execute in sequence:

1. **run_query**: Execute SQL to fetch data
   - params: { "sql": "SELECT ... WHERE organization_id = :org_id ..." }
   - IMPORTANT: Always include organization_id = :org_id in WHERE clause
   
2. **llm**: Process data with AI
   - params: { "prompt": "Summarize this data: {step_0_output}" }
   - Use {step_N_output} to reference output from step N
   
3. **send_slack**: Post to a Slack channel
   - params: { "channel": "#channel-name", "message": "..." }
   
4. **send_system_email**: Send email from Revtops system
   - params: { "to": "email@example.com", "subject": "...", "body": "..." }
   
5. **send_system_sms**: Send SMS (requires Twilio config)
   - params: { "to": "+14155551234", "body": "..." }
   
6. **send_email_from**: Send from user's connected Gmail/Outlook
   - params: { "provider": "gmail", "to": "...", "subject": "...", "body": "..." }
   
7. **sync**: Trigger a data sync
   - params: { "provider": "hubspot" }

## Example Workflow

Name: "Daily Stale Deals Alert"
Trigger: schedule, cron "0 9 * * 1-5" (weekdays at 9am)
Steps:
1. run_query: Find deals without activity in 30 days
2. llm: Summarize and suggest actions
3. send_slack: Post to #sales-alerts

The workflow is saved and will run automatically. Users can view/manage it in the Automations tab.""",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Human-readable name for the workflow",
                    },
                    "description": {
                        "type": "string",
                        "description": "Description of what the workflow does",
                    },
                    "trigger_type": {
                        "type": "string",
                        "enum": ["schedule", "event", "manual"],
                        "description": "What triggers this workflow",
                    },
                    "trigger_config": {
                        "type": "object",
                        "description": "Trigger configuration. For schedule: {cron: '0 9 * * *'}. For event: {event: 'sync.completed'}",
                    },
                    "steps": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "action": {
                                    "type": "string",
                                    "enum": ["run_query", "llm", "send_slack", "send_system_email", "send_system_sms", "send_email_from", "sync"],
                                },
                                "params": {"type": "object"},
                            },
                            "required": ["action", "params"],
                        },
                        "description": "List of steps to execute in order",
                    },
                },
                "required": ["name", "trigger_type", "trigger_config", "steps"],
            },
        },
        {
            "name": "trigger_workflow",
            "description": """Manually trigger a workflow to test it or run it on-demand.

Use this after creating a workflow to test that it works correctly.
Returns the task_id which can be used to check status.

The workflow runs asynchronously - results will appear in the Automations tab.""",
            "input_schema": {
                "type": "object",
                "properties": {
                    "workflow_id": {
                        "type": "string",
                        "description": "UUID of the workflow to trigger",
                    },
                },
                "required": ["workflow_id"],
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

    elif tool_name == "create_workflow":
        result = await _create_workflow(tool_input, organization_id, user_id)
        logger.info("[Tools] create_workflow completed: %s", result)
        return result

    elif tool_name == "trigger_workflow":
        result = await _trigger_workflow(tool_input, organization_id)
        logger.info("[Tools] trigger_workflow completed: %s", result)
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
            # Set the organization context for Row-Level Security
            # This session variable is checked by RLS policies on all tables
            # Use set_config() instead of SET LOCAL for asyncpg compatibility
            await session.execute(
                text("SELECT set_config('app.current_org_id', :org_id, true)"),
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
