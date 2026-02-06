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
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select, text

from config import settings
from models.account import Account
from models.artifact import Artifact
from models.contact import Contact
from models.pending_operation import PendingOperation, CrmOperation  # CrmOperation is alias
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


# =============================================================================
# Tool Progress Helper (DRY pattern for progress updates)
# =============================================================================

class ToolProgressUpdater:
    """
    Centralized helper for sending tool progress updates to the frontend.
    
    Usage:
        progress = ToolProgressUpdater(context, organization_id)
        await progress.update({"message": "Processing...", "completed": 5, "total": 10})
    """
    
    def __init__(
        self,
        context: dict[str, Any] | None,
        organization_id: str,
    ) -> None:
        self.conversation_id: str | None = context.get("conversation_id") if context else None
        self.tool_id: str | None = context.get("tool_id") if context else None
        self.organization_id = organization_id
        
    @property
    def can_update(self) -> bool:
        """Returns True if progress updates can be sent (have required context)."""
        return bool(self.conversation_id and self.tool_id)
    
    async def update(self, result: dict[str, Any], status: str = "running") -> bool:
        """
        Send a progress update to the frontend.
        
        Args:
            result: Progress data dict (e.g., message, completed, total, etc.)
            status: "running" for in-progress, "complete" when done
            
        Returns:
            True if update was sent, False if missing context
        """
        if not self.can_update:
            return False
        
        # Import here to avoid circular import
        from agents.orchestrator import update_tool_result
        
        return await update_tool_result(
            self.conversation_id,  # type: ignore[arg-type]
            self.tool_id,  # type: ignore[arg-type]
            result,
            status,
            self.organization_id,
        )


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
    user_id: str | None, 
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
        user_id: User UUID (may be None for Slack DM conversations)
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
    
    # Check user's global settings (only if we have a user)
    if user_id:
        from api.routes.tool_settings import is_tool_auto_approved
        if await is_tool_auto_approved(UUID(user_id), tool_name):
            logger.info(f"[Tools] Skipping approval for {tool_name} - user auto-approved")
            return True
    
    return False


async def execute_tool(
    tool_name: str, 
    tool_input: dict[str, Any], 
    organization_id: str | None, 
    user_id: str | None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Execute a tool and return results.
    
    Args:
        tool_name: Name of the tool to execute
        tool_input: Input parameters for the tool
        organization_id: Organization UUID (required for most tools)
        user_id: User UUID executing the tool (None for Slack DM conversations)
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

    elif tool_name == "run_sql_write":
        result = await _run_sql_write(tool_input, organization_id, user_id, context)
        logger.info("[Tools] run_sql_write completed: %s", result)
        return result

    elif tool_name == "search_activities":
        result = await _search_activities(tool_input, organization_id, user_id)
        logger.info("[Tools] search_activities returned %d results", len(result.get("results", [])))
        return result

    elif tool_name == "web_search":
        result = await _web_search(tool_input)
        logger.info("[Tools] web_search completed")
        return result

    elif tool_name == "trigger_workflow":
        result = await _trigger_workflow(tool_input, organization_id)
        logger.info("[Tools] trigger_workflow completed: %s", result)
        return result

    elif tool_name == "run_workflow":
        result = await _run_workflow(tool_input, organization_id, user_id, context)
        logger.info("[Tools] run_workflow completed: %s", result.get("status"))
        return result

    elif tool_name == "loop_over":
        result = await _loop_over(tool_input, organization_id, user_id, context)
        logger.info("[Tools] loop_over completed: %d/%d successful", result.get("succeeded", 0), result.get("total", 0))
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

    elif tool_name == "create_artifact":
        result = await _create_artifact(tool_input, organization_id, user_id, context)
        logger.info("[Tools] create_artifact completed: %s", result.get("artifact_id"))
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


def _serialize_value(value: Any) -> Any:
    """
    Serialize a value for JSON output to the agent.
    
    Ensures consistent formatting:
    - Datetimes: ISO 8601 format with 'Z' suffix (UTC)
    - Dates: ISO 8601 date format (YYYY-MM-DD)
    - UUIDs: String representation
    - Decimals: Float representation
    - Other types: String fallback
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        # If timezone-aware, convert to UTC; if naive, assume UTC
        if value.tzinfo is not None:
            utc_dt = value.astimezone(timezone.utc)
        else:
            utc_dt = value.replace(tzinfo=timezone.utc)
        # Return ISO format with Z suffix (drop +00:00, use Z for clarity)
        return utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (str, int, float, bool, list, dict)):
        return value
    # Fallback for other types
    return str(value)


async def _run_sql_query(
    params: dict[str, Any], organization_id: str, user_id: str | None
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
        async with get_session(organization_id=organization_id) as session:
            # Execute the query - RLS automatically filters by organization
            # (organization_id context is already set by get_session)
            result = await session.execute(text(final_query))
            rows = result.fetchall()
            columns = list(result.keys())
            
            # Convert to list of dicts with consistent serialization
            # All datetimes are formatted as ISO 8601 with Z suffix (UTC)
            data: list[dict[str, Any]] = []
            for row in rows:
                row_dict: dict[str, Any] = {
                    col: _serialize_value(row[i])
                    for i, col in enumerate(columns)
                }
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


# Tables that can be written to via run_sql_write
WRITABLE_TABLES: set[str] = {
    "workflows",
    "artifacts", 
    "contacts",
    "deals",
    "accounts",
}

# CRM tables that go through pending operations (review before commit)
CRM_TABLES: set[str] = {
    "contacts",
    "deals", 
    "accounts",
}

# Tables that are completely off-limits for writes
PROTECTED_TABLES: set[str] = {
    "users",
    "organizations", 
    "integrations",
    "activities",
    "user_tool_settings",
    "pending_operations",
    "change_sessions",
    "record_snapshots",
    "conversations",
    "chat_messages",
}


def _validate_sql_write(query: str) -> tuple[bool, str | None, str | None]:
    """
    Validate that a write query is safe to execute.
    Returns (is_valid, error_message, operation_type).
    """
    query_upper = query.upper().strip()
    
    # Determine operation type
    operation: str | None = None
    if query_upper.startswith("INSERT"):
        operation = "INSERT"
    elif query_upper.startswith("UPDATE"):
        operation = "UPDATE"
    elif query_upper.startswith("DELETE"):
        operation = "DELETE"
    else:
        return False, "Only INSERT, UPDATE, or DELETE queries are allowed", None
    
    # Block dangerous statement types (check if query STARTS with these)
    # We don't check for keywords anywhere in the query because they could
    # appear in string literals (e.g., "Create a summary..." in prompt text)
    dangerous_start_keywords: list[str] = [
        "DROP", "TRUNCATE", "ALTER", "CREATE", "GRANT", "REVOKE",
    ]
    for keyword in dangerous_start_keywords:
        if query_upper.startswith(keyword):
            return False, f"'{keyword}' statements are not allowed", None
    
    # Block dangerous functions/commands that could appear anywhere
    # These are SQL injection vectors, not natural language words
    dangerous_patterns: list[str] = [
        r'\bEXECUTE\s*\(',  # EXECUTE() function
        r'\bEXEC\s+',       # EXEC statement  
        r'\bINTO\s+OUTFILE\b',
        r'\bINTO\s+DUMPFILE\b',
        r'\bLOAD_FILE\s*\(',
    ]
    for pattern in dangerous_patterns:
        if re.search(pattern, query_upper):
            return False, "Query contains disallowed SQL functions", None
    
    # UPDATE and DELETE must have WHERE clause
    if operation in ("UPDATE", "DELETE"):
        if not re.search(r'\bWHERE\b', query_upper):
            return False, f"{operation} queries must include a WHERE clause for safety", None
    
    return True, None, operation


def _extract_table_from_write(query: str) -> str | None:
    """Extract the target table name from a write query."""
    query_upper = query.upper().strip()
    
    # INSERT INTO table_name
    insert_match = re.match(r'INSERT\s+INTO\s+([a-zA-Z_][a-zA-Z0-9_]*)', query, re.IGNORECASE)
    if insert_match:
        return insert_match.group(1).lower()
    
    # UPDATE table_name
    update_match = re.match(r'UPDATE\s+([a-zA-Z_][a-zA-Z0-9_]*)', query, re.IGNORECASE)
    if update_match:
        return update_match.group(1).lower()
    
    # DELETE FROM table_name
    delete_match = re.match(r'DELETE\s+FROM\s+([a-zA-Z_][a-zA-Z0-9_]*)', query, re.IGNORECASE)
    if delete_match:
        return delete_match.group(1).lower()


def _find_matching_paren(s: str, start: int) -> int:
    """Find the index of the closing paren that matches the opening paren at start."""
    depth = 0
    in_string = False
    string_char: str | None = None
    i = start
    
    while i < len(s):
        char = s[i]
        
        # Handle string literals
        if char in ("'", '"') and not in_string:
            in_string = True
            string_char = char
        elif char == string_char and in_string:
            # Check for escaped quote (doubled)
            if i + 1 < len(s) and s[i + 1] == string_char:
                i += 1  # Skip escaped quote
            else:
                in_string = False
                string_char = None
        elif not in_string:
            if char == '(':
                depth += 1
            elif char == ')':
                depth -= 1
                if depth == 0:
                    return i
        i += 1
    
    return -1  # No matching paren found


def _parse_insert_for_injection(query: str) -> tuple[str, str, str] | None:
    """
    Parse INSERT INTO table (cols) VALUES (vals) handling nested parens/quotes.
    Returns (table_name, columns_str, values_str) or None if parsing fails.
    """
    # Match: INSERT INTO table_name
    table_match = re.match(r'INSERT\s+INTO\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*', query, re.IGNORECASE)
    if not table_match:
        return None
    
    table_name = table_match.group(1)
    rest = query[table_match.end():]
    
    # Find columns: (col1, col2, ...)
    if not rest.startswith('('):
        return None
    
    cols_end = _find_matching_paren(rest, 0)
    if cols_end == -1:
        return None
    
    columns = rest[1:cols_end]  # Content between parens
    rest = rest[cols_end + 1:].strip()
    
    # Match VALUES keyword
    values_match = re.match(r'VALUES\s*', rest, re.IGNORECASE)
    if not values_match:
        return None
    
    rest = rest[values_match.end():]
    
    # Find values: (val1, val2, ...)
    if not rest.startswith('('):
        return None
    
    vals_end = _find_matching_paren(rest, 0)
    if vals_end == -1:
        return None
    
    values = rest[1:vals_end]  # Content between parens
    
    return (table_name, columns.strip(), values.strip())
    
    return None


def _parse_insert_values(query: str) -> dict[str, Any] | None:
    """
    Parse an INSERT query to extract column names and values.
    Returns a dict of {column: value} or None if parsing fails.
    """
    # Match: INSERT INTO table (col1, col2, ...) VALUES (val1, val2, ...)
    match = re.match(
        r"INSERT\s+INTO\s+\w+\s*\(([^)]+)\)\s*VALUES\s*\(([^)]+)\)",
        query,
        re.IGNORECASE
    )
    if not match:
        return None
    
    columns_str, values_str = match.groups()
    columns = [c.strip() for c in columns_str.split(",")]
    
    # Simple value parsing - handles strings, numbers, nulls
    # This is basic; production would use proper SQL parsing
    values: list[Any] = []
    current_value = ""
    in_string = False
    string_char = None
    
    for char in values_str + ",":
        if char in ("'", '"') and not in_string:
            in_string = True
            string_char = char
        elif char == string_char and in_string:
            in_string = False
            string_char = None
        elif char == "," and not in_string:
            val = current_value.strip()
            # Convert to appropriate type
            if val.upper() == "NULL":
                values.append(None)
            elif val.startswith("'") and val.endswith("'"):
                values.append(val[1:-1])
            elif val.startswith('"') and val.endswith('"'):
                values.append(val[1:-1])
            else:
                try:
                    if "." in val:
                        values.append(float(val))
                    else:
                        values.append(int(val))
                except ValueError:
                    values.append(val)
            current_value = ""
            continue
        current_value += char
    
    if len(columns) != len(values):
        return None
    
    return dict(zip(columns, values))


def _parse_update_values(query: str) -> tuple[dict[str, Any], str] | None:
    """
    Parse an UPDATE query to extract SET values and WHERE clause.
    Returns (updates_dict, where_clause) or None if parsing fails.
    """
    # Match: UPDATE table SET col1 = val1, col2 = val2 WHERE ...
    match = re.match(
        r"UPDATE\s+\w+\s+SET\s+(.+?)\s+WHERE\s+(.+)",
        query,
        re.IGNORECASE | re.DOTALL
    )
    if not match:
        return None
    
    set_clause, where_clause = match.groups()
    
    # Parse SET clause - simple approach for col = 'value' pairs
    updates: dict[str, Any] = {}
    # Split on comma but not inside quotes
    parts: list[str] = []
    current = ""
    in_string = False
    for char in set_clause:
        if char in ("'", '"') and not in_string:
            in_string = True
        elif char in ("'", '"') and in_string:
            in_string = False
        elif char == "," and not in_string:
            parts.append(current.strip())
            current = ""
            continue
        current += char
    if current.strip():
        parts.append(current.strip())
    
    for part in parts:
        if "=" not in part:
            continue
        col, val = part.split("=", 1)
        col = col.strip()
        val = val.strip()
        
        if val.upper() == "NULL":
            updates[col] = None
        elif val.startswith("'") and val.endswith("'"):
            updates[col] = val[1:-1]
        elif val.startswith('"') and val.endswith('"'):
            updates[col] = val[1:-1]
        else:
            try:
                if "." in val:
                    updates[col] = float(val)
                else:
                    updates[col] = int(val)
            except ValueError:
                updates[col] = val
    
    return updates, where_clause.strip()


async def _run_sql_write(
    params: dict[str, Any],
    organization_id: str,
    user_id: str | None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Execute a write SQL query (INSERT, UPDATE, DELETE) with safety rails.
    
    Routing:
    - CRM tables (contacts, deals, accounts) → Creates PendingOperation for review
    - Other tables (workflows, artifacts) → Direct execution
    
    Safety features:
    - Only whitelisted tables can be written to
    - UPDATE/DELETE require WHERE clauses
    - organization_id is auto-injected for RLS
    """
    query = params.get("query", "").strip()
    
    if not query:
        return {"error": "No query provided"}
    
    logger.info("[Tools._run_sql_write] Query: %s", query)
    
    # Validate query structure
    is_valid, error, operation = _validate_sql_write(query)
    if not is_valid:
        logger.warning("[Tools._run_sql_write] Validation failed: %s", error)
        return {"error": error}
    
    # Extract and validate target table
    table = _extract_table_from_write(query)
    if not table:
        return {"error": "Could not determine target table from query"}
    
    logger.debug("[Tools._run_sql_write] Target table: %s, operation: %s", table, operation)
    
    if table in PROTECTED_TABLES:
        return {"error": f"Table '{table}' is protected and cannot be modified via SQL."}
    
    if table not in WRITABLE_TABLES:
        return {"error": f"Table '{table}' is not in the writable list. Allowed tables: {', '.join(sorted(WRITABLE_TABLES))}"}
    
    # ==========================================================================
    # CRM Tables: Route through PendingOperation for review/commit workflow
    # ==========================================================================
    if table in CRM_TABLES:
        conversation_id: str | None = (context or {}).get("conversation_id")
        return await _handle_crm_write_from_sql(
            query=query,
            table=table,
            operation=operation,
            organization_id=organization_id,
            user_id=user_id,
            conversation_id=conversation_id,
        )
    
    # ==========================================================================
    # Non-CRM Tables: Direct execution
    # ==========================================================================
    try:
        async with get_session(organization_id=organization_id) as session:
            
            # For INSERT, inject required columns
            final_query = query
            if operation == "INSERT":
                # Parse INSERT INTO table (cols) VALUES (vals)
                # Need to handle nested parens, quotes, and functions properly
                parsed = _parse_insert_for_injection(query)
                if parsed is None:
                    return {"error": "INSERT query format not recognized. Use: INSERT INTO table (columns) VALUES (values)"}
                
                table_part, columns, values = parsed
                columns_lower = columns.lower()
                
                # Build lists of extra columns and values to inject
                extra_cols: list[str] = []
                extra_vals: list[str] = []
                
                # Always inject organization_id and created_by_user_id
                if "organization_id" not in columns_lower:
                    extra_cols.append("organization_id")
                    extra_vals.append(f"'{organization_id}'")
                if "created_by_user_id" not in columns_lower:
                    extra_cols.append("created_by_user_id")
                    extra_vals.append(f"'{user_id}'")
                
                # Workflow-specific defaults
                if table == "workflows":
                    if "id" not in columns_lower:
                        extra_cols.append("id")
                        extra_vals.append("gen_random_uuid()")
                    if "steps" not in columns_lower:
                        extra_cols.append("steps")
                        extra_vals.append("'[]'::jsonb")
                    if "auto_approve_tools" not in columns_lower:
                        extra_cols.append("auto_approve_tools")
                        extra_vals.append("'[]'::jsonb")
                    if "is_enabled" not in columns_lower:
                        extra_cols.append("is_enabled")
                        extra_vals.append("true")
                
                # Artifacts-specific defaults
                if table == "artifacts":
                    if "id" not in columns_lower:
                        extra_cols.append("id")
                        extra_vals.append("gen_random_uuid()")
                
                # Reconstruct the query
                if extra_cols:
                    new_cols = f"{columns}, {', '.join(extra_cols)}"
                    new_vals = f"{values}, {', '.join(extra_vals)}"
                    final_query = f"INSERT INTO {table_part} ({new_cols}) VALUES ({new_vals})"
                else:
                    final_query = query
            
            # Execute the query
            result = await session.execute(text(final_query))
            await session.commit()
            
            rows_affected = result.rowcount
            
            logger.info("[Tools._run_sql_write] %s completed, %d rows affected", operation, rows_affected)
            
            return {
                "success": True,
                "operation": operation,
                "table": table,
                "rows_affected": rows_affected,
                "message": f"{operation} completed successfully. {rows_affected} row(s) affected.",
            }
            
    except Exception as e:
        logger.error("[Tools._run_sql_write] Query execution failed: %s", str(e))
        return {"error": f"Query execution failed: {str(e)}"}


async def _handle_crm_write_from_sql(
    query: str,
    table: str,
    operation: str | None,
    organization_id: str,
    user_id: str,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    """
    Handle CRM table writes by creating PendingOperations for review.
    Parses SQL and converts to structured pending operation.
    """
    # Map table name to record type
    table_to_record_type: dict[str, str] = {
        "contacts": "contact",
        "deals": "deal",
        "accounts": "company",  # accounts table = company record type
    }
    record_type = table_to_record_type.get(table, table)
    
    # Map SQL operation to CRM operation type
    op_mapping: dict[str, str] = {
        "INSERT": "create",
        "UPDATE": "update",
        "DELETE": "delete",
    }
    crm_operation = op_mapping.get(operation or "", "create")
    
    # Parse the SQL to extract data
    records: list[dict[str, Any]] = []
    
    if operation == "INSERT":
        parsed = _parse_insert_values(query)
        if not parsed:
            return {"error": "Could not parse INSERT query. Use format: INSERT INTO table (col1, col2) VALUES (val1, val2)"}
        records = [parsed]
        
    elif operation == "UPDATE":
        parsed = _parse_update_values(query)
        if not parsed:
            return {"error": "Could not parse UPDATE query. Use format: UPDATE table SET col1 = val1 WHERE id = '...'"}
        updates, where_clause = parsed
        # Try to extract ID from WHERE clause
        id_match = re.search(r"id\s*=\s*'([^']+)'", where_clause, re.IGNORECASE)
        if id_match:
            updates["id"] = id_match.group(1)
        records = [updates]
        
    elif operation == "DELETE":
        # For DELETE, we just need the ID from WHERE clause
        id_match = re.search(r"id\s*=\s*'([^']+)'", query, re.IGNORECASE)
        if not id_match:
            return {"error": "DELETE requires WHERE id = '...' clause"}
        records = [{"id": id_match.group(1)}]
    
    # Create PendingOperation (audit trail); then execute into change session immediately (no approval card)
    try:
        async with get_session(organization_id=organization_id) as session:
            pending_op = PendingOperation(
                organization_id=UUID(organization_id),
                user_id=UUID(user_id),
                conversation_id=UUID(conversation_id) if conversation_id else None,
                tool_name="run_sql_write",
                tool_params={"query": query, "table": table},
                target_system="hubspot",  # Default to HubSpot
                record_type=record_type,
                operation=crm_operation,
                input_records=records,
                validated_records=records,  # No validation for SQL-based writes
                status="pending",
            )
            session.add(pending_op)
            await session.commit()
            await session.refresh(pending_op)
            logger.info(
                "[Tools._handle_crm_write_from_sql] Created pending operation %s for %s %s",
                pending_op.id,
                crm_operation,
                record_type,
            )
    except Exception as e:
        logger.error("[Tools._handle_crm_write_from_sql] Failed: %s", str(e))
        return {"error": f"Failed to create pending operation: {str(e)}"}

    result = await execute_crm_operation(
        str(pending_op.id),
        skip_duplicates=True,
        organization_id=organization_id,
    )
    if "error" in result:
        return result
    return {
        "success": True,
        "operation": crm_operation,
        "table": table,
        "records_count": len(records),
        "success_count": result.get("success_count", 0),
        "message": f"Added {result.get('success_count', 0)} {record_type}(s) to pending changes. Commit or discard in the bar below when ready.",
    }


async def _search_activities(
    params: dict[str, Any], organization_id: str, user_id: str | None
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


async def _web_search(params: dict[str, Any]) -> dict[str, Any]:
    """Search the web using Perplexity's Sonar API."""
    query = params.get("query", "").strip()
    
    if not query:
        return {"error": "No search query provided"}
    
    if not settings.PERPLEXITY_API_KEY:
        return {
            "error": "We do not currently run external web interactions; coming soon!",
            "suggestion": "Add PERPLEXITY_API_KEY to your environment variables to enable web search.",
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
    params: dict[str, Any], organization_id: str, user_id: str | None, skip_approval: bool = False
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
    async with get_session(organization_id=organization_id) as session:
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
    async with get_session(organization_id=organization_id) as session:
        crm_operation = CrmOperation(
            organization_id=UUID(organization_id),
            user_id=UUID(user_id) if user_id else None,
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
    
    # Local-first: Execute immediately, creates records locally with sync_status='pending'
    # User can then use the bottom panel to "Commit All" to HubSpot or "Undo All" to discard
    logger.info("[Tools._crm_write] Executing local-first CRM operation")
    result = await execute_crm_operation(operation_id, skip_duplicates=True, organization_id=organization_id)
    return result


async def execute_crm_operation(
    operation_id: str, 
    skip_duplicates: bool = True,
    organization_id: str | None = None,
) -> dict[str, Any]:
    """
    Execute a previously validated CRM operation (LOCAL-FIRST).
    
    This creates records locally with sync_status='pending'. The user must then
    explicitly "Commit" to push changes to the external CRM, or "Undo" to discard.
    
    Args:
        operation_id: UUID of the CrmOperation to execute
        skip_duplicates: If True, skip records that already exist (for create operation)
        organization_id: Organization ID for RLS context (optional, loaded from operation if not provided)
        
    Returns:
        Result of the operation with success/failure details
    """
    from services.change_session import (
        get_or_start_change_session,
        get_or_start_orphan_change_session,
        start_change_session,
    )
    from models.conversation import Conversation

    async with get_session(organization_id=organization_id) as session:
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

        # Capture needed values before session closes
        org_id = str(crm_op.organization_id)
        user_id = str(crm_op.user_id) if crm_op.user_id else None
        target_system = crm_op.target_system
        record_type = crm_op.record_type
        operation = crm_op.operation
        validated_records = crm_op.validated_records
        duplicate_warnings = crm_op.duplicate_warnings
        op_conversation_id: str | None = str(crm_op.conversation_id) if crm_op.conversation_id else None

    # Resolve scope: root of conversation tree so parent + all child workflows share one change session
    scope_conversation_id: str | None = None
    if op_conversation_id and user_id:
        async with get_session(organization_id=org_id) as sess:
            conv = await sess.get(Conversation, UUID(op_conversation_id))
            if conv:
                scope_conversation_id = str(conv.root_conversation_id or conv.id)

    # Start or reuse a change session (one per run/chat, or one per org+user when no conversation)
    change_session_id = None
    if user_id:
        try:
            if scope_conversation_id:
                change_session = await get_or_start_change_session(
                    organization_id=org_id,
                    user_id=user_id,
                    scope_conversation_id=scope_conversation_id,
                    description=f"CRM {operation} {record_type}(s) - pending sync to {target_system}",
                )
            else:
                change_session = await get_or_start_orphan_change_session(
                    organization_id=org_id,
                    user_id=user_id,
                    description=f"CRM {operation} {record_type}(s) - pending sync to {target_system}",
                )
            change_session_id = str(change_session.id)
        except Exception as e:
            logger.warning("[Tools.execute_crm_operation] Failed to start change session: %s", e)
    
    try:
        # Create records locally only (don't push to external CRM yet)
        result = await _create_local_pending_records(
            organization_id=UUID(org_id),
            user_id=user_id,
            record_type=record_type,
            operation=operation,
            validated_records=validated_records,
            duplicate_warnings=duplicate_warnings,
            skip_duplicates=skip_duplicates,
            change_session_id=change_session_id,
            target_system=target_system,
        )
        
        # Update operation with result
        async with get_session(organization_id=org_id) as session:
            crm_op = await session.get(CrmOperation, UUID(operation_id))
            if crm_op:
                if "error" in result:
                    crm_op.status = "failed"
                    crm_op.error_message = result["error"]
                else:
                    # Mark as "pending_sync" - waiting for user to commit or undo
                    crm_op.status = "pending_sync"
                    crm_op.success_count = result.get("success_count", 0)
                    crm_op.failure_count = result.get("failure_count", 0)
                    crm_op.result = result
                crm_op.executed_at = datetime.utcnow()
                await session.commit()
        
        # Note: Change session stays 'pending' until user commits or discards
        
        return result
        
    except Exception as e:
        logger.error("[Tools.execute_crm_operation] Error: %s", str(e))
        
        error_msg = str(e)[:500]
        
        async with get_session(organization_id=org_id) as session:
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


async def _create_local_pending_records(
    organization_id: UUID,
    user_id: str | None,
    record_type: str,
    operation: str,
    validated_records: list[dict[str, Any]],
    duplicate_warnings: list[dict[str, Any]] | None,
    skip_duplicates: bool,
    change_session_id: str | None,
    target_system: str,
) -> dict[str, Any]:
    """
    Store proposed creates only (no local DB rows until user commits).
    When user commits, we create locally and push to CRM; when they discard, we drop proposals.
    """
    from services.change_session import add_proposed_create

    records_to_process = validated_records
    duplicate_ids: set[str] = set()

    if skip_duplicates and duplicate_warnings:
        for warning in duplicate_warnings:
            if record_type == "contact":
                duplicate_ids.add(warning["record"].get("email", "").lower())
            elif record_type == "company":
                duplicate_ids.add(warning["record"].get("domain", "").lower())
            elif record_type == "deal":
                duplicate_ids.add(warning["record"].get("dealname", ""))

    if skip_duplicates and duplicate_ids:
        filtered_records: list[dict[str, Any]] = []
        for record in records_to_process:
            identifier = ""
            if record_type == "contact":
                identifier = record.get("email", "").lower()
            elif record_type == "company":
                identifier = record.get("domain", "").lower()
            elif record_type == "deal":
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
            "skipped_count": len(validated_records),
            "created_local": [],
            "change_session_id": change_session_id,
        }

    table_map: dict[str, str] = {"contact": "contacts", "company": "accounts", "deal": "deals"}
    table_name = table_map.get(record_type)
    if not table_name or not change_session_id:
        return {
            "status": "failed",
            "message": "Missing change session or table",
            "success_count": 0,
            "failure_count": len(records_to_process),
            "errors": [{"error": "change_session_id or table required"}],
        }

    created_count = 0
    errors: list[dict[str, Any]] = []
    async with get_session(organization_id=str(organization_id)) as session:
        for record in records_to_process:
            try:
                record_id = uuid4()
                await add_proposed_create(
                    change_session_id=change_session_id,
                    table_name=table_name,
                    record_id=str(record_id),
                    input_payload=record,
                    db_session=session,
                )
                created_count += 1
            except Exception as e:
                logger.warning(
                    "[Tools._create_local_pending_records] Failed to add proposal %s: %s",
                    record_type, str(e),
                )
                errors.append({"record": record, "error": str(e)})
        await session.commit()

    skipped_count = len(validated_records) - len(records_to_process)
    return {
        "status": "pending_sync",
        "message": f"Stored {created_count} {record_type}(s) for review. Commit to create in HubSpot and locally, or Undo to discard.",
        "success_count": created_count,
        "failure_count": len(errors),
        "skipped_count": skipped_count,
        "created_local": [],
        "change_session_id": change_session_id,
        "errors": errors if errors else None,
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
    crm_op: CrmOperation,
    skip_duplicates: bool,
    change_session_id: str | None = None,
    user_id: str | None = None,
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
                crm_op.record_type,
                created,
                crm_op.organization_id,
                change_session_id=change_session_id,
                user_id=user_id,
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
    change_session_id: str | None = None,
    user_id: str | None = None,
) -> int:
    """
    Sync newly created CRM records to local database (incremental sync).
    
    This allows users to immediately see records they just created without
    waiting for a full sync cycle.
    
    Args:
        record_type: Type of record (contact, company, deal)
        created_records: List of records returned from HubSpot API
        organization_id: Organization UUID
        change_session_id: Optional change session for tracking/rollback
        user_id: Optional user ID for updated_by tracking
        
    Returns:
        Number of records synced
    """
    from datetime import datetime as dt, timezone as tz
    from services.change_session import capture_snapshot, update_snapshot_after_data
    
    synced = 0
    now = dt.now(tz.utc)
    user_uuid = UUID(user_id) if user_id else None
    
    # Map record_type to table name
    table_map = {"contact": "contacts", "company": "accounts", "deal": "deals"}
    table_name = table_map.get(record_type)
    
    async with get_session(organization_id=str(organization_id)) as session:
        for hs_record in created_records:
            hs_id = hs_record.get("id", "")
            properties = hs_record.get("properties", {})
            
            if not hs_id:
                continue
            
            try:
                record_id = uuid4()
                snapshot_id: str | None = None
                
                # Capture snapshot before create (if tracking changes)
                if change_session_id and table_name:
                    snapshot = await capture_snapshot(
                        change_session_id=change_session_id,
                        table_name=table_name,
                        record_id=str(record_id),
                        operation="create",
                        db_session=session,
                    )
                    snapshot_id = str(snapshot.id)
                
                if record_type == "contact":
                    # Build contact from HubSpot response
                    first_name = properties.get("firstname") or ""
                    last_name = properties.get("lastname") or ""
                    full_name = f"{first_name} {last_name}".strip()
                    if not full_name:
                        full_name = properties.get("email") or f"Contact {hs_id}"
                    
                    contact = Contact(
                        id=record_id,
                        organization_id=organization_id,
                        source_system="hubspot",
                        source_id=hs_id,
                        name=full_name,
                        email=properties.get("email"),
                        title=properties.get("jobtitle"),
                        phone=properties.get("phone"),
                        updated_at=now,
                        updated_by=user_uuid,
                    )
                    await session.merge(contact)
                    
                    # Update snapshot with after_data
                    if snapshot_id:
                        await update_snapshot_after_data(
                            snapshot_id, contact.to_dict(), db_session=session
                        )
                    synced += 1
                    
                elif record_type == "company":
                    # Build account from HubSpot response
                    name = properties.get("name")
                    if not name:
                        name = properties.get("domain") or f"Company {hs_id}"
                    
                    account = Account(
                        id=record_id,
                        organization_id=organization_id,
                        source_system="hubspot",
                        source_id=hs_id,
                        name=name,
                        domain=properties.get("domain"),
                        industry=properties.get("industry"),
                        updated_at=now,
                        updated_by=user_uuid,
                    )
                    await session.merge(account)
                    
                    # Update snapshot with after_data
                    if snapshot_id:
                        await update_snapshot_after_data(
                            snapshot_id, account.to_dict(), db_session=session
                        )
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
                        id=record_id,
                        organization_id=organization_id,
                        source_system="hubspot",
                        source_id=hs_id,
                        name=properties.get("dealname") or "Untitled Deal",
                        amount=amount,
                        stage=properties.get("dealstage"),
                        updated_at=now,
                        updated_by=user_uuid,
                    )
                    await session.merge(deal)
                    
                    # Update snapshot with after_data
                    if snapshot_id:
                        await update_snapshot_after_data(
                            snapshot_id, deal.to_dict(), db_session=session
                        )
                    synced += 1
                    
            except Exception as e:
                logger.warning(
                    "[Tools._sync_created_records_to_db] Failed to sync %s %s: %s",
                    record_type, hs_id, str(e)
                )
                continue
        
        await session.commit()
    
    return synced


async def cancel_crm_operation(operation_id: str, organization_id: str | None = None) -> dict[str, Any]:
    """
    Cancel a pending CRM operation.
    
    Args:
        operation_id: UUID of the CrmOperation to cancel
        organization_id: Organization ID for RLS context (optional)
        
    Returns:
        Confirmation of cancellation
    """
    async with get_session(organization_id=organization_id) as session:
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


# Fields that exist in our local models but NOT in HubSpot properties
_INTERNAL_FIELDS: frozenset[str] = frozenset({
    "id", "organization_id", "source_system", "source_id", "sync_status",
    "synced_at", "updated_at", "updated_by", "custom_fields", "account_id",
    "owner_id", "pipeline_id", "created_date", "last_modified_date",
    "visible_to_user_ids", "employee_count", "annual_revenue", "probability",
    "close_date", "_input",
})


def _to_hubspot_properties(table_name: str, raw: dict[str, Any]) -> dict[str, Any]:
    """Map local DB column names to HubSpot property names and strip internal fields."""
    # Start by stripping internal-only keys and None values
    cleaned: dict[str, Any] = {
        k: v for k, v in raw.items()
        if k not in _INTERNAL_FIELDS and v is not None
    }

    if table_name == "contacts":
        # local "title" -> HubSpot "jobtitle"
        if "title" in cleaned:
            cleaned.setdefault("jobtitle", cleaned.pop("title"))
        # local "name" -> split into firstname / lastname (if they aren't already set)
        if "name" in cleaned and "firstname" not in cleaned:
            parts: list[str] = str(cleaned.pop("name")).split(None, 1)
            cleaned["firstname"] = parts[0] if parts else ""
            cleaned["lastname"] = parts[1] if len(parts) > 1 else ""
        elif "name" in cleaned:
            cleaned.pop("name")  # firstname/lastname already provided
    elif table_name == "deals":
        # local "name" -> HubSpot "dealname"
        if "name" in cleaned and "dealname" not in cleaned:
            cleaned["dealname"] = cleaned.pop("name")
        elif "name" in cleaned:
            cleaned.pop("name")
        # local "stage" -> HubSpot "dealstage"
        if "stage" in cleaned and "dealstage" not in cleaned:
            cleaned["dealstage"] = cleaned.pop("stage")
        elif "stage" in cleaned:
            cleaned.pop("stage")
    # accounts: "name", "domain", "industry" are the same in HubSpot — nothing to rename

    return cleaned


async def commit_change_session(
    change_session_id: str, 
    user_id: str,
    organization_id: str | None = None,
) -> dict[str, Any]:
    """
    Commit a pending change session - push proposed records to HubSpot then
    create local rows with the returned external IDs.
    """
    from models.change_session import ChangeSession
    from models.record_snapshot import RecordSnapshot
    from services.change_session import approve_change_session
    from connectors.hubspot import HubSpotConnector

    _log = logger  # alias for brevity
    _log.info(
        "[commit] Starting commit for session=%s user=%s org=%s",
        change_session_id, user_id, organization_id,
    )

    # ── 1. Load change session & snapshots ──────────────────────────────────
    async with get_session(organization_id=organization_id) as session:
        change_session = await session.get(ChangeSession, UUID(change_session_id))

        if not change_session:
            _log.warning("[commit] Session %s not found", change_session_id)
            return {"status": "failed", "error": f"Change session {change_session_id} not found"}

        if change_session.status != "pending":
            _log.warning("[commit] Session %s is %s, not pending", change_session_id, change_session.status)
            return {"status": "failed", "error": f"Change session is not pending (status: {change_session.status})"}

        org_id: str = str(change_session.organization_id)

        result = await session.execute(
            select(RecordSnapshot).where(RecordSnapshot.change_session_id == change_session.id)
        )
        snapshots = result.scalars().all()

    _log.info("[commit] Session %s: loaded %d snapshot(s), org=%s", change_session_id, len(snapshots), org_id)

    if not snapshots:
        _log.info("[commit] No snapshots – marking session approved with 0 synced")
        await approve_change_session(change_session_id, user_id)
        return {"status": "completed", "message": "No pending changes to commit", "synced_count": 0}

    # ── 2. Build per-table sync lists ───────────────────────────────────────
    contacts_to_sync: list[tuple[UUID, dict[str, Any]]] = []
    accounts_to_sync: list[tuple[UUID, dict[str, Any]]] = []
    deals_to_sync: list[tuple[UUID, dict[str, Any]]] = []

    for snapshot in snapshots:
        if snapshot.operation != "create" or not snapshot.after_data:
            _log.debug("[commit] Skipping snapshot %s (op=%s, has_data=%s)",
                       snapshot.id, snapshot.operation, bool(snapshot.after_data))
            continue

        after_data: dict[str, Any] = snapshot.after_data
        raw_input: dict[str, Any] | None = after_data.get("_input") if isinstance(after_data, dict) else None
        source_data: dict[str, Any] = raw_input or after_data

        _log.info(
            "[commit] Snapshot %s: table=%s record=%s raw_keys=%s",
            snapshot.id, snapshot.table_name, snapshot.record_id, list(source_data.keys()),
        )

        # Normalise to HubSpot property names and strip internal-only fields
        input_data: dict[str, Any] = _to_hubspot_properties(snapshot.table_name, source_data)

        _log.info(
            "[commit] Snapshot %s: mapped HS properties=%s",
            snapshot.id, {k: (v if k != "phone" else "***") for k, v in input_data.items()},
        )

        record_id = snapshot.record_id
        if snapshot.table_name == "contacts":
            contacts_to_sync.append((record_id, input_data))
        elif snapshot.table_name == "accounts":
            accounts_to_sync.append((record_id, input_data))
        elif snapshot.table_name == "deals":
            deals_to_sync.append((record_id, input_data))

    total_records: int = len(contacts_to_sync) + len(accounts_to_sync) + len(deals_to_sync)
    _log.info(
        "[commit] Records to push: %d contacts, %d accounts, %d deals (%d total)",
        len(contacts_to_sync), len(accounts_to_sync), len(deals_to_sync), total_records,
    )

    # ── 3. Push to HubSpot & create local rows ─────────────────────────────
    connector = HubSpotConnector(org_id)
    synced_count: int = 0
    errors: list[dict[str, Any]] = []
    org_uuid: UUID = UUID(org_id)
    user_uuid: UUID | None = UUID(user_id) if user_id else None
    now: datetime = datetime.utcnow()

    async with get_session(organization_id=org_id) as session:
        # ── Contacts ────────────────────────────────────────────────────────
        for local_id, input_data in contacts_to_sync:
            try:
                _log.info("[commit] Pushing contact %s to HubSpot: %s", local_id, input_data)
                hs_result: dict[str, Any] = await connector.create_contact(input_data)
                hs_id: str | None = hs_result.get("id")
                _log.info("[commit] HubSpot returned id=%s for contact %s", hs_id, local_id)

                if hs_id:
                    first_name: str = input_data.get("firstname") or ""
                    last_name: str = input_data.get("lastname") or ""
                    full_name: str = f"{first_name} {last_name}".strip() or input_data.get("email") or f"Contact {local_id}"
                    contact = Contact(
                        id=local_id,
                        organization_id=org_uuid,
                        source_system="hubspot",
                        source_id=str(hs_id),
                        name=full_name,
                        email=input_data.get("email"),
                        title=input_data.get("jobtitle"),
                        phone=input_data.get("phone"),
                        sync_status="synced",
                        updated_at=now,
                        updated_by=user_uuid,
                    )
                    session.add(contact)
                    synced_count += 1
                    _log.info("[commit] Created local contact %s (hs=%s, name=%s)", local_id, hs_id, full_name)
                else:
                    _log.warning("[commit] HubSpot did not return an id for contact %s – response: %s", local_id, hs_result)
            except Exception as e:
                _log.error("[commit] FAILED contact %s: %s", local_id, e, exc_info=True)
                errors.append({"table": "contacts", "record_id": str(local_id), "error": str(e)})

        # ── Accounts (companies) ────────────────────────────────────────────
        for local_id, input_data in accounts_to_sync:
            try:
                _log.info("[commit] Pushing company %s to HubSpot: %s", local_id, input_data)
                hs_result = await connector.create_company(input_data)
                hs_id = hs_result.get("id")
                _log.info("[commit] HubSpot returned id=%s for company %s", hs_id, local_id)

                if hs_id:
                    name: str = input_data.get("name") or input_data.get("domain") or f"Company {local_id}"
                    account = Account(
                        id=local_id,
                        organization_id=org_uuid,
                        source_system="hubspot",
                        source_id=str(hs_id),
                        name=name,
                        domain=input_data.get("domain"),
                        industry=input_data.get("industry"),
                        sync_status="synced",
                        updated_at=now,
                        updated_by=user_uuid,
                    )
                    session.add(account)
                    synced_count += 1
                    _log.info("[commit] Created local account %s (hs=%s, name=%s)", local_id, hs_id, name)
                else:
                    _log.warning("[commit] HubSpot did not return an id for company %s – response: %s", local_id, hs_result)
            except Exception as e:
                _log.error("[commit] FAILED company %s: %s", local_id, e, exc_info=True)
                errors.append({"table": "accounts", "record_id": str(local_id), "error": str(e)})

        # ── Deals ───────────────────────────────────────────────────────────
        for local_id, input_data in deals_to_sync:
            try:
                _log.info("[commit] Pushing deal %s to HubSpot: %s", local_id, input_data)
                hs_result = await connector.create_deal(input_data)
                hs_id = hs_result.get("id")
                _log.info("[commit] HubSpot returned id=%s for deal %s", hs_id, local_id)

                if hs_id:
                    amount: Decimal | None = None
                    if input_data.get("amount") is not None:
                        try:
                            amount = Decimal(str(input_data["amount"]))
                        except (ValueError, TypeError):
                            pass
                    deal = Deal(
                        id=local_id,
                        organization_id=org_uuid,
                        source_system="hubspot",
                        source_id=str(hs_id),
                        name=input_data.get("dealname") or "Untitled Deal",
                        amount=amount,
                        stage=input_data.get("dealstage"),
                        sync_status="synced",
                        updated_at=now,
                        updated_by=user_uuid,
                    )
                    session.add(deal)
                    synced_count += 1
                    _log.info("[commit] Created local deal %s (hs=%s, name=%s)", local_id, hs_id, deal.name)
                else:
                    _log.warning("[commit] HubSpot did not return an id for deal %s – response: %s", local_id, hs_result)
            except Exception as e:
                _log.error("[commit] FAILED deal %s: %s", local_id, e, exc_info=True)
                errors.append({"table": "deals", "record_id": str(local_id), "error": str(e)})

        await session.commit()
        _log.info("[commit] DB commit complete – %d rows written", synced_count)

    # ── 4. Mark session approved ────────────────────────────────────────────
    await approve_change_session(change_session_id, user_id)
    _log.info(
        "[commit] Session %s approved. synced=%d errors=%d total=%d",
        change_session_id, synced_count, len(errors), total_records,
    )

    return {
        "status": "completed" if not errors else "partial",
        "message": f"Synced {synced_count}/{total_records} record(s) to HubSpot",
        "synced_count": synced_count,
        "error_count": len(errors),
        "errors": errors if errors else None,
    }


async def discard_change_session(
    change_session_id: str,
    user_id: str,
    organization_id: str | None = None,
) -> dict[str, Any]:
    """
    Discard a pending change session. Proposals are dropped; no local rows to delete for creates.
    """
    from services.change_session import discard_change_session as service_discard

    result: dict[str, Any] = await service_discard(
        change_session_id, user_id, organization_id=organization_id
    )
    if result.get("status") == "error":
        return {"status": "failed", "error": result.get("error", "Unknown error")}
    return {
        "status": "discarded",
        "message": f"Discarded {result.get('total_snapshots', 0)} proposed change(s)",
        "deleted_count": result.get("rollback_count", 0),
    }


async def update_tool_call_result(
    operation_id: str, 
    new_result: dict[str, Any],
    organization_id: str | None = None,
) -> bool:
    """
    Update the stored tool call result in chat_messages for a CRM operation.
    
    This ensures that when a conversation is reloaded, the tool call shows
    the final state (completed/failed/canceled) instead of pending_approval.
    
    Args:
        operation_id: UUID of the CRM operation
        new_result: The new result to store (includes status, message, etc.)
        organization_id: Organization ID for RLS context (optional)
        
    Returns:
        True if updated successfully, False otherwise
    """
    from sqlalchemy import select
    from models.chat_message import ChatMessage
    
    try:
        async with get_session(organization_id=organization_id) as session:
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


async def get_crm_operation_status(operation_id: str, organization_id: str | None = None) -> dict[str, Any]:
    """
    Get the current status of a CRM operation.
    
    Used to check if a pending_approval operation has already been processed.
    
    Args:
        operation_id: UUID of the CRM operation
        organization_id: Organization ID for RLS context (optional)
        
    Returns:
        Operation status and details
    """
    try:
        async with get_session(organization_id=organization_id) as session:
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
        async with get_session(organization_id=organization_id) as session:
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
        task = execute_workflow.delay(workflow_id, "manual", None, None, organization_id)
        
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
    async with get_session(organization_id=organization_id) as session:
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
    async with get_session(organization_id=organization_id) as session:
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
    params: dict[str, Any], organization_id: str, user_id: str | None, skip_approval: bool = False
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
    # send_email_from requires a user account to send from
    if not user_id:
        return {
            "error": "Cannot send email from user account: no user identified.",
            "suggestion": "This conversation doesn't have an associated user. Email sending requires a logged-in user with a connected email account.",
        }
    
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
    async with get_session(organization_id=organization_id) as session:
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
    async with get_session(organization_id=organization_id) as session:
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
    params: dict[str, Any], organization_id: str, user_id: str | None, skip_approval: bool = False
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
    
    # Note: SlackConnector.post_message auto-converts markdown to mrkdwn
    
    # Check for active Slack integration
    async with get_session(organization_id=organization_id) as session:
        # Debug: Log what integrations exist for this org
        all_integrations = await session.execute(
            select(Integration).where(
                Integration.organization_id == UUID(organization_id),
            )
        )
        all_int_list = all_integrations.scalars().all()
        logger.info(f"[send_slack] Found {len(all_int_list)} integrations for org {organization_id}")
        for i in all_int_list:
            logger.info(f"[send_slack]   - provider={i.provider}, is_active={i.is_active}")
        
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
    async with get_session(organization_id=organization_id) as session:
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
        # SlackConnector inherits from BaseConnector which fetches credentials internally
        connector = SlackConnector(
            organization_id=organization_id,
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
    async with get_session(organization_id=organization_id) as session:
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


# =============================================================================
# Workflow Composition Tools
# =============================================================================

# Maximum depth for nested workflow calls to prevent infinite recursion
MAX_WORKFLOW_CALL_DEPTH: int = 5

# Maximum items that can be processed in loop_over
MAX_LOOP_ITEMS: int = 500

# Maximum concurrent workflow executions in loop_over
MAX_CONCURRENT_WORKFLOWS: int = 10


async def _run_workflow(
    params: dict[str, Any],
    organization_id: str,
    user_id: str | None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Execute another workflow and optionally wait for completion.
    
    This enables workflow composition - parent workflows can delegate
    to specialist child workflows.
    
    Args:
        params: Contains workflow_id, input_data, wait_for_completion
        organization_id: Organization UUID
        user_id: User UUID
        context: Workflow context containing call_stack for recursion detection
        
    Returns:
        Workflow execution result or task info
    """
    from models.workflow import Workflow, WorkflowRun
    from workers.tasks.workflows import _execute_workflow
    
    workflow_id = params.get("workflow_id", "").strip()
    input_data: dict[str, Any] = params.get("input_data", {}) or {}
    wait_for_completion: bool = params.get("wait_for_completion", True)
    
    if not workflow_id:
        return {"error": "workflow_id is required"}
    
    # === Recursion Detection ===
    call_stack: list[str] = []
    if context:
        call_stack = list(context.get("call_stack", []))
    
    # Check depth limit
    if len(call_stack) >= MAX_WORKFLOW_CALL_DEPTH:
        return {
            "error": f"Maximum workflow call depth ({MAX_WORKFLOW_CALL_DEPTH}) exceeded. "
                     f"Call stack: {' -> '.join(call_stack)}",
            "status": "rejected",
        }
    
    # Check for circular call
    if workflow_id in call_stack:
        return {
            "error": f"Circular workflow call detected. "
                     f"Workflow {workflow_id} is already in call stack: {' -> '.join(call_stack)}",
            "status": "rejected",
        }
    
    try:
        # Verify workflow exists and belongs to org
        async with get_session(organization_id=organization_id) as session:
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
                return {"error": f"Workflow '{workflow.name}' is disabled."}
            
            workflow_name: str = workflow.name
        
        if not wait_for_completion:
            # Fire and forget - queue via Celery
            from workers.tasks.workflows import execute_workflow
            task = execute_workflow.delay(
                workflow_id, 
                "run_workflow", 
                input_data, 
                None, 
                organization_id
            )
            
            return {
                "status": "queued",
                "workflow_id": workflow_id,
                "workflow_name": workflow_name,
                "task_id": task.id,
                "message": f"Workflow '{workflow_name}' queued for execution.",
            }
        
        # === Synchronous execution ===
        # Build child context with updated call stack
        child_call_stack: list[str] = call_stack + [workflow_id]
        
        # Build trigger_data with parent context (child workflow will set root_conversation_id from this)
        trigger_data: dict[str, Any] = {
            **input_data,
            "_parent_context": {
                "call_stack": child_call_stack,
                "parent_workflow_id": context.get("workflow_id") if context else None,
                "parent_conversation_id": context.get("conversation_id") if context else None,
            },
        }
        
        # Execute workflow directly (not via Celery) for synchronous result
        execution_result = await _execute_workflow(
            workflow_id=workflow_id,
            triggered_by="run_workflow",
            trigger_data=trigger_data,
            conversation_id=None,
            organization_id=organization_id,
        )
        
        return {
            "status": execution_result.get("status", "unknown"),
            "workflow_id": workflow_id,
            "workflow_name": workflow_name,
            "run_id": execution_result.get("run_id"),
            "conversation_id": execution_result.get("conversation_id"),
            "output": execution_result.get("output"),
            "structured_output": execution_result.get("structured_output"),
            "error": execution_result.get("error"),
        }
        
    except Exception as e:
        logger.error("[Tools._run_workflow] Failed: %s", str(e))
        return {"error": f"Failed to run workflow: {str(e)}", "status": "failed"}


async def _loop_over(
    params: dict[str, Any],
    organization_id: str,
    user_id: str | None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Execute a workflow for each item in a list.
    
    This is the "map" operation for workflows - process a batch of items
    by running a workflow for each one.
    
    Args:
        params: Contains items, workflow_id, max_concurrent, max_items, continue_on_error
        organization_id: Organization UUID
        user_id: User UUID
        context: Workflow context for recursion detection
        
    Returns:
        Summary of results and failures
    """
    import asyncio
    from models.workflow import Workflow
    
    items: list[dict[str, Any]] = params.get("items", [])
    workflow_id: str = params.get("workflow_id", "").strip()
    max_concurrent: int = min(params.get("max_concurrent", 3), MAX_CONCURRENT_WORKFLOWS)
    max_items: int = min(params.get("max_items", 100), MAX_LOOP_ITEMS)
    continue_on_error: bool = params.get("continue_on_error", True)
    
    if not workflow_id:
        return {"error": "workflow_id is required"}
    
    if not items:
        return {"error": "items array is required and cannot be empty"}
    
    if not isinstance(items, list):
        return {"error": "items must be an array"}
    
    # Apply item limit
    original_count: int = len(items)
    items = items[:max_items]
    truncated: bool = original_count > max_items
    
    # Verify workflow exists
    async with get_session(organization_id=organization_id) as session:
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
            return {"error": f"Workflow '{workflow.name}' is disabled."}
        
        workflow_name: str = workflow.name
    
    # === Execute workflow for each item ===
    results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    semaphore = asyncio.Semaphore(max_concurrent)
    completed_count = 0
    
    # Centralized progress updater
    progress = ToolProgressUpdater(context, organization_id)
    conversation_id = progress.conversation_id  # Keep for parent context linking
    
    logger.info(
        "[Tools._loop_over] Progress context: can_update=%s",
        progress.can_update,
    )
    
    async def send_progress() -> None:
        """Send loop progress update."""
        await progress.update({
            "completed": completed_count,
            "total": len(items),
            "workflow_name": workflow_name,
            "succeeded": len(results),
            "failed": len(failures),
        })
        logger.info("[Tools._loop_over] Updating progress: %d/%d", completed_count, len(items))
    
    async def process_item(index: int, item: dict[str, Any]) -> dict[str, Any]:
        """Process a single item through the workflow."""
        nonlocal completed_count
        
        async with semaphore:
            try:
                # Run workflow with item directly as input_data (not wrapped)
                # The item IS the input, plus we add _loop_context for metadata
                input_data: dict[str, Any] = {
                    **item,  # Spread the item properties at root level
                    "_loop_context": {
                        "index": index,
                        "total": len(items),
                    },
                }
                
                # Also pass parent_conversation_id so child can link back
                if conversation_id:
                    input_data["_parent_context"] = input_data.get("_parent_context", {})
                    input_data["_parent_context"]["parent_conversation_id"] = conversation_id
                
                result = await _run_workflow(
                    params={
                        "workflow_id": workflow_id,
                        "input_data": input_data,
                        "wait_for_completion": True,
                    },
                    organization_id=organization_id,
                    user_id=user_id,
                    context=context,
                )
                
                return {
                    "index": index,
                    "item": item,
                    "result": result,
                    "success": result.get("status") == "completed",
                }
                
            except Exception as e:
                logger.error(f"[Tools._loop_over] Item {index} failed: {str(e)}")
                return {
                    "index": index,
                    "item": item,
                    "result": {"error": str(e)},
                    "success": False,
                }
            finally:
                # Update progress after each item (success or failure)
                completed_count += 1
                await send_progress()
    
    # Create tasks for all items
    tasks: list[asyncio.Task[dict[str, Any]]] = [
        asyncio.create_task(process_item(i, item))
        for i, item in enumerate(items)
    ]
    
    # Process with or without early termination
    if continue_on_error:
        # Wait for all tasks
        item_results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for item_result in item_results:
            if isinstance(item_result, Exception):
                failures.append({"error": str(item_result)})
            elif item_result.get("success"):
                results.append(item_result)
            else:
                failures.append(item_result)
    else:
        # Stop on first error
        for task in asyncio.as_completed(tasks):
            item_result = await task
            
            if not item_result.get("success"):
                failures.append(item_result)
                # Cancel remaining tasks
                for t in tasks:
                    if not t.done():
                        t.cancel()
                break
            
            results.append(item_result)
    
    succeeded: int = len(results)
    failed: int = len(failures)
    
    return {
        "status": "completed" if failed == 0 else ("partial" if succeeded > 0 else "failed"),
        "workflow_id": workflow_id,
        "workflow_name": workflow_name,
        "total": len(items),
        "succeeded": succeeded,
        "failed": failed,
        "truncated": truncated,
        "original_count": original_count if truncated else None,
        "results": results,
        "failures": failures[:10],  # Limit failure details to first 10
        "message": (
            f"Processed {len(items)} items: {succeeded} succeeded, {failed} failed."
            + (f" (Truncated from {original_count} items)" if truncated else "")
        ),
    }


# =============================================================================
# Artifact Creation Tool
# =============================================================================

# Mapping of content_type to MIME type
CONTENT_TYPE_TO_MIME: dict[str, str] = {
    "text": "text/plain",
    "markdown": "text/markdown",
    "pdf": "application/pdf",
    "chart": "application/json",
}


async def _create_artifact(
    params: dict[str, Any],
    organization_id: str,
    user_id: str | None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Create a downloadable artifact (file) for the user.
    
    Args:
        params: Tool input with title, filename, content_type, content
        organization_id: Organization UUID
        user_id: User UUID
        context: Optional context with conversation_id, message_id
        
    Returns:
        Artifact metadata for frontend display
    """
    title: str = params.get("title", "Untitled")
    filename: str = params.get("filename", "artifact.txt")
    content_type: str = params.get("content_type", "text")
    content: str = params.get("content", "")
    
    # Centralized progress updater
    progress = ToolProgressUpdater(context, organization_id)
    content_len = len(content)
    
    # Send initial progress
    await progress.update({
        "message": f"Validating {content_type} content...",
        "title": title,
        "content_type": content_type,
        "chars_processed": 0,
        "total_chars": content_len,
    })
    
    # Validate content_type
    valid_types: set[str] = {"text", "markdown", "pdf", "chart"}
    if content_type not in valid_types:
        return {
            "error": f"Invalid content_type '{content_type}'. Must be one of: {', '.join(valid_types)}"
        }
    
    # Validate content is not empty
    if not content.strip():
        return {"error": "Content cannot be empty"}
    
    # For charts, validate JSON
    if content_type == "chart":
        try:
            chart_spec = json.loads(content)
            # Ensure it has the basic Plotly structure
            if not isinstance(chart_spec, dict):
                return {"error": "Chart content must be a JSON object"}
            if "data" not in chart_spec:
                return {"error": "Chart content must have a 'data' field with Plotly traces"}
        except json.JSONDecodeError as e:
            return {"error": f"Invalid JSON for chart: {str(e)}"}
    
    # Get MIME type
    mime_type: str = CONTENT_TYPE_TO_MIME.get(content_type, "application/octet-stream")
    
    # For PDF, we'll store markdown content and generate PDF on download
    # This avoids storing large base64 content in the database
    stored_content: str = content
    if content_type == "pdf":
        # Store markdown source - PDF will be generated on demand
        mime_type = "text/markdown"  # Source is markdown
    
    # Get context for linking (already extracted above, but get message_id)
    message_id: str | None = context.get("message_id") if context else None
    
    # Update progress before database save
    await progress.update({
        "message": f"Saving {content_type} artifact...",
        "title": title,
        "content_type": content_type,
        "chars_processed": content_len,
        "total_chars": content_len,
    })
    
    # Create artifact in database
    artifact_id: str = str(uuid4())
    
    async with get_session(organization_id=organization_id) as session:
        artifact = Artifact(
            id=artifact_id,
            user_id=user_id,
            organization_id=organization_id,
            type="file",  # Use 'file' type to distinguish from dashboards/reports
            title=title,
            content=stored_content,
            content_type=content_type,
            mime_type=mime_type,
            filename=filename,
            conversation_id=conversation_id,
            message_id=message_id,
        )
        session.add(artifact)
        await session.commit()
        
        logger.info(
            "[Tools._create_artifact] Created artifact: id=%s, type=%s, title=%s",
            artifact_id,
            content_type,
            title,
        )
    
    # Return metadata for frontend (content excluded to keep response small)
    # Use camelCase for frontend compatibility
    return {
        "status": "success",
        "artifact_id": artifact_id,
        "artifact": {
            "id": artifact_id,
            "title": title,
            "filename": filename,
            "contentType": content_type,  # camelCase for frontend
            "mimeType": CONTENT_TYPE_TO_MIME.get(content_type, "application/octet-stream"),  # camelCase
        },
        "message": f"Created {content_type} artifact: {title}",
    }
