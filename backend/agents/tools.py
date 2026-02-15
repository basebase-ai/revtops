"""
Tool definitions and execution for Claude.

Tools are organized by category (see registry.py):
- LOCAL_READ: run_sql_query, search_activities
- LOCAL_WRITE: create_artifact, run_sql_write, run_workflow
- EXTERNAL_READ: web_search, fetch_url, enrich_with_apollo, search_system_of_record
- EXTERNAL_WRITE: write_to_system_of_record, send_email_from, send_slack, trigger_sync

All writes to external systems (CRM, issue trackers, code repos) go through
write_to_system_of_record, which dispatches to the correct handler by target_system.
"""

import json
import logging
import re
from collections.abc import Awaitable, Callable
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
from models.tracker_team import TrackerTeam

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
    "org_members",
    "pipelines", "pipeline_stages", "goals", "workflows", "workflow_runs", "user_mappings_for_identity",
    "github_repositories", "github_commits", "github_pull_requests",
    "shared_files",
    "tracker_teams", "tracker_projects", "tracker_issues",
    "bulk_operations", "bulk_operation_results",
}


def get_tools(context: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Return tool definitions for Claude from the unified registry."""
    in_workflow = bool((context or {}).get("is_workflow"))
    return get_tools_for_claude(in_workflow=in_workflow)


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
    
    normalized_tool_name = tool_name
    if tool_name == "create_github_issue":
        logger.warning("[Tools] create_github_issue is deprecated, remapping to github_issues_access")
        normalized_tool_name = "github_issues_access"
    elif tool_name == "write_to_system_of_record":
        logger.info("[Tools] Routing write_to_system_of_record to unified system-of-record dispatcher")
        normalized_tool_name = "write_to_system_of_record"

    # Check if this tool should bypass approval (for auto-approved workflows)
    skip_approval = await _should_skip_approval(tool_name, user_id, context)

    if normalized_tool_name == "manage_memory" and context and context.get("is_workflow"):
        action: str = tool_input.get("action", "save")
        if action in ("save", "update"):
            logger.info("[Tools] Blocking manage_memory(%s) during workflow execution", action)
            return {"error": "manage_memory save/update is not available in workflows. Use keep_notes for workflow-scoped notes."}

    conversation_id: str | None = (context or {}).get("conversation_id")
    tool_handlers: dict[str, Callable[[], Awaitable[dict[str, Any]]]] = {
        "run_sql_query": lambda: _run_sql_query(tool_input, organization_id, user_id),
        "run_sql_write": lambda: _run_sql_write(tool_input, organization_id, user_id, context),
        "search_activities": lambda: _search_activities(tool_input, organization_id, user_id),
        "web_search": lambda: _web_search(tool_input),
        "run_workflow": lambda: _run_workflow(tool_input, organization_id, user_id, context),
        "loop_over": lambda: _loop_over(tool_input, organization_id, user_id, context),
        "fetch_url": lambda: _fetch_url(tool_input),
        "enrich_with_apollo": lambda: _enrich_with_apollo(tool_input, organization_id),
        "crm_write": lambda: _crm_write(
            tool_input,
            organization_id,
            user_id,
            skip_approval,
            conversation_id=conversation_id,
        ),
        "write_to_system_of_record": lambda: _write_to_system_of_record(
            tool_input,
            organization_id,
            user_id,
            skip_approval,
            conversation_id=conversation_id,
        ),
        "send_email_from": lambda: _send_email_from(tool_input, organization_id, user_id, skip_approval),
        "send_slack": lambda: _send_slack(tool_input, organization_id, user_id, skip_approval),
        "send_sms": lambda: _send_sms(tool_input, organization_id, user_id),
        "github_issues_access": lambda: _github_issues_access(tool_input, organization_id, user_id, skip_approval),
        "trigger_sync": lambda: _trigger_sync(tool_input, organization_id),
        "search_cloud_files": lambda: _search_cloud_files(tool_input, organization_id, user_id),
        "read_cloud_file": lambda: _read_cloud_file(tool_input, organization_id, user_id),
        "create_artifact": lambda: _create_artifact(tool_input, organization_id, user_id, context),
        "keep_notes": lambda: _keep_notes(tool_input, organization_id, user_id, context, skip_approval),
        "manage_memory": lambda: _manage_memory(tool_input, organization_id, user_id, skip_approval),
        "create_linear_issue": lambda: _create_linear_issue(tool_input, organization_id, user_id, skip_approval),
        "update_linear_issue": lambda: _update_linear_issue(tool_input, organization_id, user_id, skip_approval),
        "search_linear_issues": lambda: _search_linear_issues(tool_input, organization_id),
        "bulk_tool_run": lambda: _bulk_tool_run(tool_input, organization_id, user_id, context),
        "monitor_operation": lambda: _monitor_operation(tool_input, organization_id, context),
    }

    handler = tool_handlers.get(normalized_tool_name)
    if handler is None:
        logger.error("[Tools] Unknown tool: %s", tool_name)
        return {"error": f"Unknown tool: {tool_name}"}

    result = await handler()
    _log_tool_execution_result(tool_name, normalized_tool_name, result)
    return result


def _log_tool_execution_result(
    requested_tool_name: str,
    executed_tool_name: str,
    result: dict[str, Any],
) -> None:
    """Centralize tool execution logging so execute_tool remains easy to scan."""
    if executed_tool_name == "run_sql_query":
        logger.info("[Tools] run_sql_query returned %d rows", result.get("row_count", 0))
    elif executed_tool_name == "search_activities":
        logger.info("[Tools] search_activities returned %d results", len(result.get("results", [])))
    elif executed_tool_name == "loop_over":
        logger.info(
            "[Tools] loop_over completed: %d/%d successful",
            result.get("succeeded", 0),
            result.get("total", 0),
        )
    elif executed_tool_name == "fetch_url":
        logger.info("[Tools] fetch_url completed: %s", result.get("url"))
    elif executed_tool_name == "enrich_with_apollo":
        logger.info("[Tools] enrich_with_apollo completed: %s", result.get("status", "done"))
    elif executed_tool_name in {"crm_write", "write_to_system_of_record"}:
        logger.info("[Tools] write_to_system_of_record completed: %s", result.get("status", result.get("error", "unknown")))
    elif executed_tool_name == "send_email_from":
        logger.info("[Tools] send_email_from completed: %s", result.get("status"))
    elif executed_tool_name == "send_slack":
        logger.info("[Tools] send_slack completed: %s", result.get("status"))
    elif executed_tool_name == "trigger_sync":
        logger.info("[Tools] trigger_sync completed: %s", result.get("status"))
    elif executed_tool_name == "search_cloud_files":
        logger.info("[Tools] search_cloud_files returned %d results", len(result.get("files", [])))
    elif executed_tool_name == "read_cloud_file":
        logger.info("[Tools] read_cloud_file completed: %s", result.get("file_name", "unknown"))
    elif executed_tool_name == "create_artifact":
        logger.info("[Tools] create_artifact completed: %s", result.get("artifact_id"))
    elif executed_tool_name == "keep_notes":
        logger.info("[Tools] keep_notes completed: %s", result.get("note_id", result.get("error", result.get("status"))))
    elif executed_tool_name == "manage_memory":
        logger.info("[Tools] manage_memory completed: %s", result.get("memory_id", result.get("status", result.get("error"))))

    else:
        logger.info("[Tools] %s completed: %s", requested_tool_name, result.get("status", result))


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
    "org_members",
    "users",
}

# Per-table column restrictions: only these columns may appear in SET clauses.
# Tables not listed here have no column restrictions.
WRITABLE_COLUMNS: dict[str, set[str]] = {
    "users": {"phone_number"},
}

# CRM tables that go through pending operations (review before commit)
CRM_TABLES: set[str] = {
    "contacts",
    "deals", 
    "accounts",
}

# Tables that are completely off-limits for writes
PROTECTED_TABLES: set[str] = {
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


def _split_sql_csv(values: str) -> list[str]:
    """Split a SQL comma-separated list while respecting quotes and nested parentheses."""
    parts: list[str] = []
    current: list[str] = []
    in_string = False
    string_char: str | None = None
    depth = 0

    i = 0
    while i < len(values):
        char = values[i]

        if char in ("'", '"') and not in_string:
            in_string = True
            string_char = char
            current.append(char)
        elif char == string_char and in_string:
            # Handle escaped quotes in SQL strings (e.g., 'it''s')
            if i + 1 < len(values) and values[i + 1] == string_char:
                current.append(char)
                current.append(values[i + 1])
                i += 1
            else:
                in_string = False
                string_char = None
                current.append(char)
        elif not in_string and char == '(':
            depth += 1
            current.append(char)
        elif not in_string and char == ')':
            depth = max(0, depth - 1)
            current.append(char)
        elif not in_string and depth == 0 and char == ',':
            part = ''.join(current).strip()
            if part:
                parts.append(part)
            current = []
        else:
            current.append(char)

        i += 1

    tail = ''.join(current).strip()
    if tail:
        parts.append(tail)

    return parts


def _parse_sql_bool(raw_value: str) -> bool | None:
    """Parse a SQL boolean literal; returns None if not a boolean literal."""
    normalized = raw_value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    return None


def _parse_sql_string_literal(raw_value: str) -> str | None:
    """Parse a quoted SQL string literal; returns None for non-literals."""
    value = raw_value.strip()
    if len(value) >= 2 and value[0] == "'" and value[-1] == "'":
        return value[1:-1].replace("''", "'")
    return None


def _workflow_insert_would_auto_run(query: str) -> bool:
    """Return True when a workflow INSERT results in an enabled non-manual workflow."""
    parsed = _parse_insert_for_injection(query)
    if parsed is None:
        logger.warning("[Tools._workflow_insert_would_auto_run] Could not parse workflow INSERT query")
        return False

    _, columns, values = parsed
    col_names = [c.strip().lower() for c in _split_sql_csv(columns)]
    raw_values = _split_sql_csv(values)
    if len(col_names) != len(raw_values):
        logger.warning(
            "[Tools._workflow_insert_would_auto_run] Workflow INSERT parse mismatch (columns=%d values=%d)",
            len(col_names),
            len(raw_values),
        )
        return False

    value_map = {name: raw_values[idx] for idx, name in enumerate(col_names)}

    trigger_type_value = _parse_sql_string_literal(value_map.get("trigger_type", ""))
    trigger_type = (trigger_type_value or "").strip().lower()

    is_enabled_raw = value_map.get("is_enabled")
    is_enabled = _parse_sql_bool(is_enabled_raw) if is_enabled_raw is not None else True

    return bool(trigger_type and trigger_type != "manual" and is_enabled is True)


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

    # Prevent autonomous workflow fan-out: a workflow run cannot create other
    # workflows that are automatically runnable (enabled + non-manual trigger).
    if context and context.get("is_workflow") and table == "workflows":
        if operation == "INSERT":
            limit_error = _workflow_child_creation_limit_error(context)
            if limit_error:
                return limit_error

        if operation == "INSERT" and _workflow_insert_would_auto_run(query):
            logger.warning(
                "[Tools._run_sql_write] Blocked workflow-created auto-run workflow INSERT"
            )
            return {
                "error": (
                    "Workflow executions cannot create enabled schedule/event workflows. "
                    "Create the workflow as manual or disabled first."
                )
            }

        if operation == "UPDATE":
            lower_query = query.lower()
            enables_workflow = re.search(r"\bis_enabled\s*=\s*true\b", lower_query) is not None
            sets_auto_trigger = re.search(r"\btrigger_type\s*=\s*'\s*(schedule|event)\s*'", lower_query) is not None
            if enables_workflow or sets_auto_trigger:
                logger.warning(
                    "[Tools._run_sql_write] Blocked workflow UPDATE that could enable auto-run child workflow"
                )
                return {
                    "error": (
                        "Workflow executions cannot enable or configure schedule/event triggers "
                        "on workflows. Leave child workflows manual/disabled."
                    )
                }
    
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

            if (
                context
                and context.get("is_workflow")
                and table == "workflows"
                and operation == "INSERT"
            ):
                updated_count = int(context.get("created_workflow_count", 0)) + 1
                context["created_workflow_count"] = updated_count
                logger.info(
                    "[Tools._run_sql_write] Workflow-created child count incremented: workflow_id=%s count=%d",
                    context.get("workflow_id"),
                    updated_count,
                )
            
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


async def _fetch_url(params: dict[str, Any]) -> dict[str, Any]:
    """Fetch a web page, using direct httpx for simple requests or ScrapingBee for proxy/JS rendering."""
    url: str = params.get("url", "").strip()
    extract_text: bool = params.get("extract_text", True)
    render_js: bool = params.get("render_js", False)
    premium_proxy: bool = params.get("premium_proxy", False)
    wait_ms: int | None = params.get("wait_ms")

    if not url:
        return {"error": "No URL provided"}

    if not url.startswith(("http://", "https://")):
        return {"error": "URL must start with http:// or https://"}

    use_scrapingbee: bool = render_js or premium_proxy

    if use_scrapingbee and not settings.SCRAPINGBEE_API_KEY:
        return {
            "error": "ScrapingBee is required for render_js/premium_proxy but SCRAPINGBEE_API_KEY is not set.",
            "suggestion": "Add SCRAPINGBEE_API_KEY to your environment variables, or disable render_js/premium_proxy.",
        }

    try:
        if use_scrapingbee:
            body = await _fetch_url_via_scrapingbee(url, extract_text, render_js, premium_proxy, wait_ms)
        else:
            body = await _fetch_url_direct(url)
    except httpx.TimeoutException:
        logger.error("[Tools._fetch_url] Request timed out for %s", url)
        return {"error": "Request timed out. The page may be slow to respond.", "url": url}
    except Exception as e:
        logger.error("[Tools._fetch_url] Fetch failed: %s", str(e))
        return {"error": f"Fetch failed: {str(e)}", "url": url}

    # If ScrapingBee with extract_text, response is JSON with our extract_rules
    if use_scrapingbee and extract_text:
        try:
            extracted: dict[str, Any] = json.loads(body)
            text_content: str = extracted.get("text", body)
            return _truncate_result(url, text_content, mode="extracted_text", max_chars=50_000)
        except (json.JSONDecodeError, KeyError):
            pass  # Fall through to raw handling

    if extract_text and not use_scrapingbee:
        # Simple tag stripping for direct fetches
        text_content = _strip_html(body)
        return _truncate_result(url, text_content, mode="extracted_text", max_chars=50_000)

    return _truncate_result(url, body, mode="html", max_chars=100_000)


async def _fetch_url_direct(url: str) -> str:
    """Fetch a URL directly with httpx (no proxy, no cost)."""
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        response = await client.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; Revtops/1.0)",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        if response.status_code >= 400:
            raise Exception(f"HTTP {response.status_code} from {url}")
        return response.text


async def _fetch_url_via_scrapingbee(
    url: str,
    extract_text: bool,
    render_js: bool,
    premium_proxy: bool,
    wait_ms: int | None,
) -> str:
    """Fetch a URL via ScrapingBee proxy (costs credits)."""
    sb_params: dict[str, str] = {
        "api_key": settings.SCRAPINGBEE_API_KEY,  # type: ignore[arg-type]
        "url": url,
    }

    if extract_text:
        sb_params["extract_rules"] = json.dumps({"text": {"selector": "body", "type": "text"}})

    if render_js:
        sb_params["render_js"] = "true"
        if wait_ms is not None:
            clamped_wait: int = max(0, min(wait_ms, 35000))
            sb_params["wait"] = str(clamped_wait)

    if premium_proxy:
        sb_params["premium_proxy"] = "true"

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.get(
            "https://app.scrapingbee.com/api/v1/",
            params=sb_params,
        )

        if response.status_code != 200:
            detail: str = response.text[:500] if response.text else "no response body"
            logger.error(
                "[Tools._fetch_url] ScrapingBee error: %s %s",
                response.status_code,
                detail,
            )
            raise Exception(
                f"ScrapingBee returned status {response.status_code}: {detail}"
            )

        return response.text


def _strip_html(html: str) -> str:
    """Naive but fast HTML tag stripping for plain-text extraction."""
    # Remove script/style blocks entirely
    text: str = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    # Strip remaining tags
    text = re.sub(r"<[^>]+>", " ", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _truncate_result(url: str, content: str, *, mode: str, max_chars: int) -> dict[str, Any]:
    """Build a fetch_url result dict, truncating content if needed."""
    truncated: bool = len(content) > max_chars
    if truncated:
        content = content[:max_chars]
    result: dict[str, Any] = {
        "url": url,
        "content": content,
        "mode": mode,
    }
    if truncated:
        result["truncated"] = True
        result["note"] = f"Content truncated to {max_chars} characters"
    return result


# =============================================================================
# Generic System-of-Record Write Dispatcher
# =============================================================================
# All external writes route through write_to_system_of_record, which dispatches
# to the correct handler based on target_system. Adding a new integration is just
# adding a new entry in _WRITE_HANDLERS.

# Handler type: (records, record_type, operation, org_id, user_id, skip_approval, conversation_id) -> result
from typing import Callable, Awaitable as _Awaitable

_WriteHandler = Callable[
    [list[dict[str, Any]], str, str, str, str | None, bool, str | None],
    _Awaitable[dict[str, Any]],
]

# CRM systems whose bulk writes go through the ChangeSession / pending-changes flow
_CRM_SYSTEMS: frozenset[str] = frozenset({"hubspot", "salesforce"})

# Writes touching this many records or fewer execute immediately.
# Above this threshold, CRM writes go through the Pending Changes UI for review.
_DIRECT_WRITE_THRESHOLD: int = 5

# Systems that support the "issue" record type via tracker connectors
_TRACKER_SYSTEMS: frozenset[str] = frozenset({"linear", "jira", "asana"})

# Systems that support the "issue" record type via code-repo connectors
_CODE_REPO_SYSTEMS: frozenset[str] = frozenset({"github", "gitlab"})


async def _write_to_system_of_record(
    params: dict[str, Any],
    organization_id: str,
    user_id: str | None,
    skip_approval: bool = False,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    """
    Universal write dispatcher for all external systems of record.

    Routes to the correct handler based on target_system.
    The record count determines the execution mode:
      - ≤ _DIRECT_WRITE_THRESHOLD  →  execute immediately
      - >  _DIRECT_WRITE_THRESHOLD →  pending changes UI for review
    """
    target_system: str = params.get("target_system", "").lower().strip()
    record_type: str = params.get("record_type", "").lower().strip()
    operation: str = params.get("operation", "create").lower().strip()
    records: list[dict[str, Any]] | Any = params.get("records", [])

    if not target_system:
        return {"error": "target_system is required (e.g. 'hubspot', 'linear', 'github')."}
    if not record_type:
        return {"error": "record_type is required (e.g. 'contact', 'issue', 'deal')."}
    if operation not in ("create", "update"):
        return {"error": f"Invalid operation '{operation}'. Must be 'create' or 'update'."}
    if not records or not isinstance(records, list):
        return {"error": "records must be a non-empty array of objects."}

    all_systems: frozenset[str] = _CRM_SYSTEMS | _TRACKER_SYSTEMS | _CODE_REPO_SYSTEMS
    if target_system not in all_systems:
        return {"error": f"Unsupported target_system '{target_system}'. Supported: {', '.join(sorted(all_systems))}"}

    # Validate record_type for non-CRM systems
    if target_system in (_TRACKER_SYSTEMS | _CODE_REPO_SYSTEMS) and record_type != "issue":
        return {"error": f"Unsupported record_type '{record_type}' for {target_system}. Only 'issue' is supported."}

    require_review: bool = bool(params.get("require_review", False))
    direct_execute: bool = (
        len(records) <= _DIRECT_WRITE_THRESHOLD
        and not require_review
    )

    if direct_execute:
        # ── Small write: execute immediately against external API ──
        return await _execute_direct_write(
            target_system, record_type, operation, records,
            organization_id, user_id, skip_approval, conversation_id,
        )
    else:
        # ── Large write or require_review: go through Pending Changes UI ──
        if require_review and target_system not in _CRM_SYSTEMS:
            return {"error": f"require_review is only supported for CRM systems, not '{target_system}'."}
        return await _execute_pending_write(
            target_system, record_type, operation, records,
            organization_id, user_id, skip_approval, conversation_id,
        )


async def _execute_direct_write(
    target_system: str,
    record_type: str,
    operation: str,
    records: list[dict[str, Any]],
    organization_id: str,
    user_id: str | None,
    skip_approval: bool = False,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    """Small write (≤ threshold): execute immediately against the external API."""

    if target_system in _CRM_SYSTEMS:
        crm_params: dict[str, Any] = {
            "target_system": target_system,
            "record_type": record_type,
            "operation": operation,
            "records": records,
        }
        return await _crm_write(
            crm_params, organization_id, user_id, skip_approval,
            conversation_id=conversation_id, direct_execute=True,
        )

    if target_system in _TRACKER_SYSTEMS:
        return await _handle_tracker_write(target_system, operation, records, organization_id, user_id)

    if target_system in _CODE_REPO_SYSTEMS:
        return await _handle_code_repo_write(target_system, operation, records, organization_id)

    return {"error": f"Direct write not supported for '{target_system}'."}


async def _execute_pending_write(
    target_system: str,
    record_type: str,
    operation: str,
    records: list[dict[str, Any]],
    organization_id: str,
    user_id: str | None,
    skip_approval: bool = False,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    """Large write (> threshold): route through Pending Changes UI for review."""

    if target_system in _CRM_SYSTEMS:
        crm_params: dict[str, Any] = {
            "target_system": target_system,
            "record_type": record_type,
            "operation": operation,
            "records": records,
        }
        return await _crm_write(
            crm_params, organization_id, user_id, skip_approval,
            conversation_id=conversation_id, direct_execute=False,
        )

    # Non-CRM bulk writes: pending changes not yet supported, execute immediately
    # TODO: extend Pending Changes UI to support tracker / code-repo bulk writes
    logger.warning(
        "[Tools] Bulk write (%d records) to %s — pending changes not yet supported, executing directly",
        len(records), target_system,
    )
    return await _execute_direct_write(
        target_system, record_type, operation, records,
        organization_id, user_id, skip_approval, conversation_id,
    )


async def _handle_tracker_write(
    target_system: str,
    operation: str,
    records: list[dict[str, Any]],
    organization_id: str,
    user_id: str | None,
) -> dict[str, Any]:
    """Handle writes to issue tracker systems (Linear, Jira, Asana)."""
    # Check for active integration
    async with get_session(organization_id=organization_id) as session:
        result = await session.execute(
            select(Integration).where(
                Integration.organization_id == UUID(organization_id),
                Integration.provider == target_system,
                Integration.is_active == True,
            )
        )
        integration: Integration | None = result.scalar_one_or_none()
        if not integration:
            return {
                "error": f"No active {target_system} integration found. Please connect {target_system} in Data Sources.",
            }

    if target_system == "linear":
        return await _handle_linear_write(operation, records, organization_id)
    # Future: elif target_system == "jira": ...
    # Future: elif target_system == "asana": ...

    return {"error": f"Issue tracker '{target_system}' is not yet implemented."}


async def _handle_linear_write(
    operation: str,
    records: list[dict[str, Any]],
    organization_id: str,
) -> dict[str, Any]:
    """Handle create/update for Linear issues. Each record is processed individually."""
    from connectors.linear import LinearConnector

    connector: LinearConnector = LinearConnector(organization_id=organization_id)
    results: list[dict[str, Any]] = []
    errors: list[str] = []

    for i, record in enumerate(records):
        try:
            if operation == "create":
                issue_result: dict[str, Any] = await _execute_linear_create(connector, record)
            else:
                issue_result = await _execute_linear_update(connector, record, organization_id)
            results.append(issue_result)
        except Exception as exc:
            error_msg: str = f"Record {i + 1}: {exc}"
            logger.error("[Tools._handle_linear_write] %s", error_msg)
            errors.append(error_msg)

    if errors and not results:
        return {"status": "failed", "errors": errors}

    summary_parts: list[str] = []
    for r in results:
        identifier: str = r.get("identifier", "?")
        title: str = r.get("title", "")
        summary_parts.append(f"{identifier}: {title}")

    return {
        "status": "completed",
        "message": f"{'Created' if operation == 'create' else 'Updated'} {len(results)} Linear issue(s)",
        "issues": results,
        "errors": errors if errors else None,
    }


async def _execute_linear_create(
    connector: "LinearConnector",  # type: ignore[name-defined]
    record: dict[str, Any],
) -> dict[str, Any]:
    """Create a single Linear issue from a record dict."""
    team_key: str = record.get("team_key", "").strip()
    title: str = record.get("title", "").strip()
    if not team_key:
        raise ValueError("team_key is required (e.g. 'ENG')")
    if not title:
        raise ValueError("title is required")

    team: dict[str, Any] | None = await connector.resolve_team_by_key(team_key)
    if not team:
        raise ValueError(f"Team with key '{team_key}' not found in Linear")

    assignee_id: str | None = None
    assignee_name: str | None = record.get("assignee_name")
    if assignee_name:
        user: dict[str, Any] | None = await connector.resolve_assignee_by_name(assignee_name)
        if user:
            assignee_id = user["id"]
        else:
            logger.warning("Could not resolve Linear user '%s'", assignee_name)

    project_id: str | None = None
    project_name: str | None = record.get("project_name")
    if project_name:
        project: dict[str, Any] | None = await connector.resolve_project_by_name(project_name)
        if project:
            project_id = project["id"]
        else:
            logger.warning("Could not resolve Linear project '%s'", project_name)

    issue: dict[str, Any] = await connector.create_issue(
        team_id=team["id"],
        title=title,
        description=record.get("description"),
        priority=record.get("priority"),
        assignee_id=assignee_id,
        project_id=project_id,
    )
    return issue


async def _execute_linear_update(
    connector: "LinearConnector",  # type: ignore[name-defined]
    record: dict[str, Any],
    organization_id: str,
) -> dict[str, Any]:
    """Update a single Linear issue from a record dict."""
    from models.tracker_issue import TrackerIssue

    issue_identifier: str = record.get("issue_identifier", "").strip()
    if not issue_identifier:
        raise ValueError("issue_identifier is required (e.g. 'ENG-123')")

    update_fields: list[str] = ["title", "description", "state_name", "priority", "assignee_name"]
    has_update: bool = any(record.get(f) is not None for f in update_fields)
    if not has_update:
        raise ValueError("At least one field to update must be provided (title, description, state_name, priority, assignee_name)")

    # Look up the Linear issue ID from synced data
    org_uuid: UUID = UUID(organization_id)
    linear_issue_id: str | None = None
    linear_team_id: str | None = None

    async with get_session(organization_id=organization_id) as session:
        row_result = await session.execute(
            select(TrackerIssue.source_id, TrackerTeam.source_id)
            .join(TrackerTeam, TrackerIssue.team_id == TrackerTeam.id)
            .where(
                TrackerIssue.organization_id == org_uuid,
                TrackerIssue.source_system == "linear",
                TrackerIssue.identifier == issue_identifier,
            )
        )
        row = row_result.first()
        if row:
            linear_issue_id = row[0]
            linear_team_id = row[1]

    if not linear_issue_id or not linear_team_id:
        raise ValueError(f"Issue '{issue_identifier}' not found in synced data. Try running a sync first.")

    # Resolve state name → state ID
    state_id: str | None = None
    state_name: str | None = record.get("state_name")
    if state_name:
        state: dict[str, Any] | None = await connector.resolve_state_by_name(linear_team_id, state_name)
        if state:
            state_id = state["id"]
        else:
            raise ValueError(f"Workflow state '{state_name}' not found for this team")

    # Resolve assignee name → user ID
    assignee_id: str | None = None
    assignee_name: str | None = record.get("assignee_name")
    if assignee_name:
        user: dict[str, Any] | None = await connector.resolve_assignee_by_name(assignee_name)
        if user:
            assignee_id = user["id"]
        else:
            logger.warning("Could not resolve Linear user '%s'", assignee_name)

    issue: dict[str, Any] = await connector.update_issue(
        issue_id=linear_issue_id,
        title=record.get("title"),
        description=record.get("description"),
        state_id=state_id,
        priority=record.get("priority"),
        assignee_id=assignee_id,
    )
    return issue


async def _handle_code_repo_write(
    target_system: str,
    operation: str,
    records: list[dict[str, Any]],
    organization_id: str,
) -> dict[str, Any]:
    """Handle writes to code repository systems (GitHub, GitLab)."""
    if operation != "create":
        return {"error": f"Only 'create' operation is supported for {target_system} issues."}

    # Check for active integration
    async with get_session(organization_id=organization_id) as session:
        result = await session.execute(
            select(Integration).where(
                Integration.organization_id == UUID(organization_id),
                Integration.provider == target_system,
                Integration.is_active == True,
            )
        )
        integration: Integration | None = result.scalar_one_or_none()
        if not integration:
            return {
                "error": f"No active {target_system} integration found. Please connect {target_system} in Data Sources.",
            }

    if target_system == "github":
        return await _handle_github_write(records, organization_id)
    # Future: elif target_system == "gitlab": ...

    return {"error": f"Code repository '{target_system}' is not yet implemented."}


async def _handle_github_write(
    records: list[dict[str, Any]],
    organization_id: str,
) -> dict[str, Any]:
    """Handle creating GitHub issues. Each record is processed individually."""
    from connectors.github import GitHubConnector

    connector: GitHubConnector = GitHubConnector(organization_id=organization_id)
    results: list[dict[str, Any]] = []
    errors: list[str] = []

    for i, record in enumerate(records):
        repo_full_name: str = record.get("repo_full_name", "").strip()
        title: str = record.get("title", "").strip()

        if not repo_full_name or "/" not in repo_full_name:
            errors.append(f"Record {i + 1}: repo_full_name is required in 'owner/repo' format")
            continue
        if not title:
            errors.append(f"Record {i + 1}: title is required")
            continue

        try:
            issue: dict[str, Any] = await connector.create_issue(
                repo_full_name=repo_full_name,
                title=title,
                body=record.get("body"),
                labels=record.get("labels"),
                assignees=record.get("assignees"),
            )
            results.append(issue)
        except Exception as exc:
            error_msg: str = f"Record {i + 1}: {exc}"
            logger.error("[Tools._handle_github_write] %s", error_msg)
            errors.append(error_msg)

    if errors and not results:
        return {"status": "failed", "errors": errors}

    return {
        "status": "completed",
        "message": f"Created {len(results)} GitHub issue(s)",
        "issues": results,
        "errors": errors if errors else None,
    }


async def _crm_write(
    params: dict[str, Any],
    organization_id: str,
    user_id: str | None,
    skip_approval: bool = False,
    conversation_id: str | None = None,
    direct_execute: bool = False,
) -> dict[str, Any]:
    """
    Create or update CRM records.
    
    Args:
        params: CRM operation parameters
        organization_id: Organization UUID
        user_id: User UUID
        skip_approval: If True, execute immediately without approval
        conversation_id: Conversation UUID for grouping into a ChangeSession
        direct_execute: If True, write directly to the external CRM API
            (skip ChangeSession / Pending Changes). Used for small writes.
    """
    target_system = params.get("target_system", "").lower()
    record_type = params.get("record_type", "").lower()
    operation = params.get("operation", "create").lower()
    records = params.get("records", [])
    
    # Validate inputs
    if target_system not in ["hubspot"]:
        return {"error": f"Unsupported CRM system: {target_system}. Currently only 'hubspot' is supported."}
    
    _ENGAGEMENT_TYPES: frozenset[str] = frozenset({"call", "email", "meeting", "note"})
    _CRM_RECORD_TYPES: frozenset[str] = frozenset({"contact", "company", "deal"})
    _ALL_RECORD_TYPES: frozenset[str] = _CRM_RECORD_TYPES | _ENGAGEMENT_TYPES

    if record_type not in _ALL_RECORD_TYPES:
        return {"error": f"Invalid record_type: {record_type}. Must be one of: {', '.join(sorted(_ALL_RECORD_TYPES))}."}
    
    is_engagement: bool = record_type in _ENGAGEMENT_TYPES

    if operation not in ["create", "update", "upsert"]:
        return {"error": f"Invalid operation: {operation}. Must be 'create', 'update', or 'upsert'."}
    
    if is_engagement and operation != "create":
        return {"error": f"Engagements (call/email/meeting/note) only support 'create' operation, got '{operation}'."}
    
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
        
        # For updates, the only required field is 'id' — skip create-specific checks
        if operation == "update":
            if not record.get("id"):
                validation_errors.append(f"Record {i+1}: 'id' is required for updates")
                continue
            # Still normalise email/domain if provided
            if record_type == "contact" and record.get("email"):
                record["email"] = record["email"].lower().strip()
            if record_type == "company" and record.get("domain"):
                domain: str = record["domain"].lower().strip()
                domain = domain.replace("https://", "").replace("http://", "")
                domain = domain.split("/")[0]
                record["domain"] = domain
            validated_records.append(record)
            continue

        # Validate required fields based on record type (create / upsert)
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

        elif is_engagement:
            # All engagement types require hs_timestamp
            if not record.get("hs_timestamp"):
                validation_errors.append(f"Record {i+1}: 'hs_timestamp' is required for {record_type}s")
                continue
            # Validate associations format if present
            raw_assocs: list[dict[str, Any]] | Any = record.get("associations")
            if raw_assocs is not None:
                if not isinstance(raw_assocs, list):
                    validation_errors.append(f"Record {i+1}: 'associations' must be an array")
                    continue
                for j, assoc in enumerate(raw_assocs):
                    if not isinstance(assoc, dict):
                        validation_errors.append(f"Record {i+1}, association {j+1}: must be an object")
                        continue
                    if not assoc.get("to_object_type") or not assoc.get("to_object_id"):
                        validation_errors.append(
                            f"Record {i+1}, association {j+1}: 'to_object_type' and 'to_object_id' are required"
                        )
                        continue
        
        validated_records.append(record)
    
    if validation_errors:
        return {
            "error": "Validation failed",
            "validation_errors": validation_errors,
        }
    
    if not validated_records:
        return {"error": "No valid records after validation"}
    
    # Check for duplicates in HubSpot (for create/upsert operations on CRM record types)
    # Engagements don't have duplicate detection
    # Only check first 10 records to avoid API rate limits and connection pool exhaustion
    if operation in ["create", "upsert"] and target_system == "hubspot" and not is_engagement:
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
            conversation_id=UUID(conversation_id) if conversation_id else None,
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
    
    if direct_execute:
        # ── Small write: push directly to external CRM, no Pending Changes ──
        logger.info("[Tools._crm_write] Direct-executing CRM operation (small write)")
        async with get_session(organization_id=organization_id) as session:
            crm_op = await session.get(CrmOperation, UUID(operation_id))
            if not crm_op:
                return {"status": "failed", "error": "CrmOperation not found after creation"}
            crm_op.status = "executing"
            await session.commit()

            direct_result: dict[str, Any] = await _execute_hubspot_operation(
                crm_op, skip_duplicates=True, user_id=user_id,
            )

            # Reload to update status
            await session.refresh(crm_op)
            if "error" in direct_result:
                crm_op.status = "failed"
                crm_op.error_message = direct_result.get("error")
            else:
                crm_op.status = "completed"
                crm_op.success_count = direct_result.get("success_count", 0)
                crm_op.failure_count = direct_result.get("failure_count", 0)
                crm_op.result = direct_result
            crm_op.executed_at = datetime.utcnow()
            await session.commit()

        return direct_result
    else:
        # ── Large write: local-first with Pending Changes review ──
        logger.info("[Tools._crm_write] Executing local-first CRM operation (pending changes)")
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
                change_session_id = str(change_session.id)
                logger.info(
                    "[Tools.execute_crm_operation] Using change session %s for scope %s",
                    change_session_id,
                    scope_conversation_id,
                )
            else:
                # No conversation scope - create orphan session
                # This happens when conversation_id is None or conversation lookup fails
                logger.warning(
                    "[Tools.execute_crm_operation] No conversation scope (op_conversation_id=%s, scope=%s), "
                    "creating orphan change session",
                    op_conversation_id,
                    scope_conversation_id,
                )
                change_session = await get_or_start_orphan_change_session(
                    organization_id=org_id,
                    user_id=user_id,
                    description=f"CRM {operation} {record_type}(s) - pending sync to {target_system}",
                )
                change_session_id = str(change_session.id)
        except Exception as e:
            logger.error(
                "[Tools.execute_crm_operation] Failed to start change session: %s (op_conversation_id=%s, scope=%s)",
                e,
                op_conversation_id,
                scope_conversation_id,
            )
    
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
    Store proposed creates or updates as snapshots (no side-effects until commit).
    """
    from services.change_session import add_proposed_create, add_proposed_update

    table_map: dict[str, str] = {
        "contact": "contacts",
        "company": "accounts",
        "deal": "deals",
        "call": "activities",
        "email": "activities",
        "meeting": "activities",
        "note": "activities",
    }
    table_name: str | None = table_map.get(record_type)
    if not table_name or not change_session_id:
        return {
            "status": "failed",
            "message": "Missing change session or table",
            "success_count": 0,
            "failure_count": len(validated_records),
            "errors": [{"error": "change_session_id or table required"}],
        }

    # ── Handle UPDATEs ──────────────────────────────────────────────────────
    if operation == "update":
        success_count: int = 0
        errors: list[dict[str, Any]] = []
        async with get_session(organization_id=str(organization_id)) as session:
            for record in validated_records:
                try:
                    record_id: str | None = record.get("id")
                    if not record_id:
                        errors.append({"record": record, "error": "No id field for update"})
                        continue
                    # Fields to update = everything except the id
                    update_fields: dict[str, Any] = {k: v for k, v in record.items() if k != "id"}
                    await add_proposed_update(
                        change_session_id=change_session_id,
                        table_name=table_name,
                        record_id=record_id,
                        update_fields=update_fields,
                        db_session=session,
                    )
                    success_count += 1
                except Exception as e:
                    logger.warning("[_create_local_pending_records] Failed update proposal: %s", e)
                    errors.append({"record": record, "error": str(e)})
            await session.commit()
        return {
            "status": "pending_sync",
            "message": f"Stored {success_count} {record_type} update(s) for review.",
            "success_count": success_count,
            "failure_count": len(errors),
            "skipped_count": 0,
            "created_local": [],
            "change_session_id": change_session_id,
            "errors": errors if errors else None,
        }

    # ── Handle CREATEs ──────────────────────────────────────────────────────
    records_to_process: list[dict[str, Any]] = validated_records
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
            identifier: str = ""
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

    # For engagements, inject _engagement_type metadata so the commit flow
    # knows which HubSpot engagement object type (call/email/meeting/note) to use.
    _engagement_record_types: frozenset[str] = frozenset({"call", "email", "meeting", "note"})
    is_engagement_create: bool = record_type in _engagement_record_types

    created_count: int = 0
    create_errors: list[dict[str, Any]] = []
    async with get_session(organization_id=str(organization_id)) as session:
        for record in records_to_process:
            try:
                new_record_id: UUID = uuid4()
                payload: dict[str, Any] = dict(record)
                if is_engagement_create:
                    payload["_engagement_type"] = record_type
                await add_proposed_create(
                    change_session_id=change_session_id,
                    table_name=table_name,
                    record_id=str(new_record_id),
                    input_payload=payload,
                    db_session=session,
                )
                created_count += 1
            except Exception as e:
                logger.warning("[_create_local_pending_records] Failed create proposal: %s", e)
                create_errors.append({"record": record, "error": str(e)})
        await session.commit()

    skipped_count: int = len(validated_records) - len(records_to_process)
    return {
        "status": "pending_sync",
        "message": f"Stored {created_count} {record_type}(s) for review. Commit to create in HubSpot and locally, or Undo to discard.",
        "success_count": created_count,
        "failure_count": len(create_errors),
        "skipped_count": skipped_count,
        "created_local": [],
        "change_session_id": change_session_id,
        "errors": create_errors if create_errors else None,
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
    
    # ── Default hubspot_owner_id to the requesting user when creating ──
    if crm_op.operation == "create" and user_id:
        needs_owner: bool = any(
            not record.get("hubspot_owner_id") for record in records_to_process
        )
        if needs_owner:
            try:
                from uuid import UUID as _UUID
                hs_owner_id: str | None = await connector.map_user_to_hs_owner(_UUID(user_id))
                if hs_owner_id:
                    for record in records_to_process:
                        if not record.get("hubspot_owner_id"):
                            record["hubspot_owner_id"] = hs_owner_id
                    logger.info(
                        "[Tools._execute_hubspot_operation] Defaulted hubspot_owner_id=%s for %d record(s)",
                        hs_owner_id, len(records_to_process),
                    )
            except Exception as owner_exc:
                logger.warning(
                    "[Tools._execute_hubspot_operation] Could not resolve owner for user %s: %s",
                    user_id, owner_exc,
                )

    # Determine if this is an engagement type
    _engagement_types: frozenset[str] = frozenset({"call", "email", "meeting", "note"})
    is_engagement: bool = crm_op.record_type in _engagement_types

    # Execute based on record type and operation
    created: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    
    try:
        if crm_op.operation == "create":
            if is_engagement:
                # ── Engagement creation (calls, emails, meetings, notes) ──
                engagement_type: str = crm_op.record_type
                for record in records_to_process:
                    try:
                        # Separate properties from associations
                        raw_assocs: list[dict[str, Any]] = record.pop("associations", []) or []
                        hs_associations: list[dict[str, Any]] = (
                            connector.build_engagement_associations(engagement_type, raw_assocs)
                            if raw_assocs else []
                        )
                        eng_result: dict[str, Any] = await connector.create_engagement(
                            engagement_type=engagement_type,
                            properties=record,
                            associations=hs_associations if hs_associations else None,
                        )
                        created.append(eng_result)
                    except Exception as eng_err:
                        errors.append({"record": record, "error": str(eng_err)})

            elif crm_op.record_type == "contact":
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
    table_map: dict[str, str] = {
        "contact": "contacts", "company": "accounts", "deal": "deals",
        "call": "activities", "email": "activities", "meeting": "activities", "note": "activities",
    }
    table_name: str | None = table_map.get(record_type)
    
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

                elif record_type in ("call", "email", "meeting", "note"):
                    # Build Activity from HubSpot engagement response
                    from models.activity import Activity as ActivityModel

                    # Derive subject from type-specific title properties
                    subject: str | None = (
                        properties.get("hs_call_title")
                        or properties.get("hs_email_subject")
                        or properties.get("hs_meeting_title")
                        or None
                    )
                    description: str | None = (
                        properties.get("hs_call_body")
                        or properties.get("hs_email_text")
                        or properties.get("hs_meeting_body")
                        or properties.get("hs_note_body")
                        or None
                    )
                    # Parse activity date from hs_timestamp (store naive UTC for DB column)
                    activity_date: datetime | None = None
                    ts_raw = properties.get("hs_timestamp")
                    if ts_raw:
                        try:
                            from dateutil.parser import parse as parse_dt
                            parsed = parse_dt(str(ts_raw))
                            if getattr(parsed, "tzinfo", None) is not None:
                                activity_date = parsed.astimezone(timezone.utc).replace(tzinfo=None)
                            else:
                                activity_date = parsed
                        except Exception:
                            pass

                    activity = ActivityModel(
                        id=record_id,
                        organization_id=organization_id,
                        source_system="hubspot",
                        source_id=hs_id,
                        type=record_type,
                        subject=subject,
                        description=description,
                        activity_date=activity_date,
                        custom_fields=properties,
                        updated_at=now,
                        updated_by=user_uuid,
                    )
                    await session.merge(activity)

                    if snapshot_id:
                        await update_snapshot_after_data(
                            snapshot_id, activity.to_dict(), db_session=session
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
    elif table_name == "accounts":
        # HubSpot "industry" is a strict enum (e.g. ELECTRICAL_ELECTRONIC_MANUFACTURING).
        # Always drop it to avoid validation errors — store locally only.
        cleaned.pop("industry", None)
    elif table_name == "activities":
        # Engagements: strip the "associations" key (handled separately) and
        # the "_engagement_type" metadata key — everything else is passed through
        # as HubSpot engagement properties (hs_timestamp, hs_call_body, etc.)
        cleaned.pop("associations", None)
        cleaned.pop("_engagement_type", None)

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
        await approve_change_session(change_session_id, user_id, organization_id=org_id)
        return {"status": "completed", "message": "No pending changes to commit", "synced_count": 0}

    # ── 2. Build per-table sync lists (creates and updates) ────────────────
    # Creates: (local_id, hs_properties)
    contacts_to_create: list[tuple[UUID, dict[str, Any]]] = []
    accounts_to_create: list[tuple[UUID, dict[str, Any]]] = []
    deals_to_create: list[tuple[UUID, dict[str, Any]]] = []
    # Engagements: (local_id, engagement_type, hs_properties, raw_associations)
    engagements_to_create: list[tuple[UUID, str, dict[str, Any], list[dict[str, Any]]]] = []
    # Updates: (local_id, hs_properties, raw_local_fields)
    contacts_to_update: list[tuple[UUID, dict[str, Any], dict[str, Any]]] = []
    accounts_to_update: list[tuple[UUID, dict[str, Any], dict[str, Any]]] = []
    deals_to_update: list[tuple[UUID, dict[str, Any], dict[str, Any]]] = []

    for snapshot in snapshots:
        if snapshot.operation not in ("create", "update") or not snapshot.after_data:
            _log.debug("[commit] Skipping snapshot %s (op=%s, has_data=%s)",
                       snapshot.id, snapshot.operation, bool(snapshot.after_data))
            continue

        after_data: dict[str, Any] = snapshot.after_data
        raw_input: dict[str, Any] | None = after_data.get("_input") if isinstance(after_data, dict) else None
        source_data: dict[str, Any] = raw_input or after_data

        _log.info(
            "[commit] Snapshot %s: op=%s table=%s record=%s raw_keys=%s",
            snapshot.id, snapshot.operation, snapshot.table_name,
            snapshot.record_id, list(source_data.keys()),
        )

        # Normalise to HubSpot property names and strip internal-only fields
        hs_props: dict[str, Any] = _to_hubspot_properties(snapshot.table_name, source_data)

        _log.info(
            "[commit] Snapshot %s: mapped HS properties=%s",
            snapshot.id, {k: (v if k != "phone" else "***") for k, v in hs_props.items()},
        )

        record_id: UUID = snapshot.record_id

        if snapshot.operation == "create":
            if snapshot.table_name == "contacts":
                contacts_to_create.append((record_id, hs_props))
            elif snapshot.table_name == "accounts":
                accounts_to_create.append((record_id, hs_props))
            elif snapshot.table_name == "deals":
                deals_to_create.append((record_id, hs_props))
            elif snapshot.table_name == "activities":
                # Engagement: extract the _engagement_type and raw associations from source_data
                eng_type: str = source_data.get("_engagement_type", "note")
                raw_assocs: list[dict[str, Any]] = source_data.get("associations", []) or []
                engagements_to_create.append((record_id, eng_type, hs_props, raw_assocs))
        elif snapshot.operation == "update":
            if snapshot.table_name == "contacts":
                contacts_to_update.append((record_id, hs_props, source_data))
            elif snapshot.table_name == "accounts":
                accounts_to_update.append((record_id, hs_props, source_data))
            elif snapshot.table_name == "deals":
                deals_to_update.append((record_id, hs_props, source_data))

    total_creates: int = len(contacts_to_create) + len(accounts_to_create) + len(deals_to_create) + len(engagements_to_create)
    total_updates: int = len(contacts_to_update) + len(accounts_to_update) + len(deals_to_update)
    total_records: int = total_creates + total_updates
    _log.info(
        "[commit] Creates: %d contacts, %d accounts, %d deals, %d engagements. "
        "Updates: %d contacts, %d accounts, %d deals. Total: %d",
        len(contacts_to_create), len(accounts_to_create), len(deals_to_create),
        len(engagements_to_create),
        len(contacts_to_update), len(accounts_to_update), len(deals_to_update),
        total_records,
    )

    # ── 3. Push to HubSpot & write local rows ──────────────────────────────
    connector = HubSpotConnector(org_id)
    synced_count: int = 0
    errors: list[dict[str, Any]] = []
    org_uuid: UUID = UUID(org_id)
    user_uuid: UUID | None = UUID(user_id) if user_id else None
    now: datetime = datetime.utcnow()

    # ── Helper: map local column names to model attrs for updates ────────
    _CONTACT_FIELD_MAP: dict[str, str] = {
        "title": "title", "jobtitle": "title",
        "name": "name", "email": "email", "phone": "phone",
    }
    _ACCOUNT_FIELD_MAP: dict[str, str] = {
        "name": "name", "domain": "domain", "industry": "industry",
    }
    _DEAL_FIELD_MAP: dict[str, str] = {
        "name": "name", "dealname": "name",
        "stage": "stage", "dealstage": "stage",
        "amount": "amount",
    }

    async with get_session(organization_id=org_id) as session:
        # ══════════════════════════════════════════════════════════════════
        # 3a. CREATES
        # ══════════════════════════════════════════════════════════════════

        # ── Create contacts ──────────────────────────────────────────────
        for local_id, hs_props in contacts_to_create:
            try:
                _log.info("[commit:create] Pushing contact %s to HubSpot: %s", local_id, hs_props)
                hs_result: dict[str, Any] = await connector.create_contact(hs_props)
                hs_id: str | None = hs_result.get("id")
                _log.info("[commit:create] HubSpot returned id=%s for contact %s", hs_id, local_id)

                if hs_id:
                    first_name: str = hs_props.get("firstname") or ""
                    last_name: str = hs_props.get("lastname") or ""
                    full_name: str = f"{first_name} {last_name}".strip() or hs_props.get("email") or f"Contact {local_id}"
                    contact = Contact(
                        id=local_id, organization_id=org_uuid, source_system="hubspot",
                        source_id=str(hs_id), name=full_name, email=hs_props.get("email"),
                        title=hs_props.get("jobtitle"), phone=hs_props.get("phone"),
                        sync_status="synced", updated_at=now, updated_by=user_uuid,
                    )
                    session.add(contact)
                    synced_count += 1
                    _log.info("[commit:create] Local contact created %s (hs=%s)", local_id, hs_id)
                else:
                    _log.warning("[commit:create] No HS id for contact %s: %s", local_id, hs_result)
            except Exception as e:
                _log.error("[commit:create] FAILED contact %s: %s", local_id, e, exc_info=True)
                errors.append({"table": "contacts", "record_id": str(local_id), "error": str(e)})

        # ── Create accounts ──────────────────────────────────────────────
        for local_id, hs_props in accounts_to_create:
            try:
                _log.info(
                    "[commit:create] Pushing company %s to HubSpot: name=%r domain=%r industry=%r all_keys=%s full=%s",
                    local_id, hs_props.get("name"), hs_props.get("domain"),
                    hs_props.get("industry"), list(hs_props.keys()), hs_props,
                )
                hs_result = await connector.create_company(hs_props)
                hs_id = hs_result.get("id")
                _log.info("[commit:create] HubSpot returned id=%s for company %s", hs_id, local_id)

                if hs_id:
                    acct_name: str = hs_props.get("name") or hs_props.get("domain") or f"Company {local_id}"
                    account = Account(
                        id=local_id, organization_id=org_uuid, source_system="hubspot",
                        source_id=str(hs_id), name=acct_name, domain=hs_props.get("domain"),
                        industry=hs_props.get("industry"), sync_status="synced",
                        updated_at=now, updated_by=user_uuid,
                    )
                    session.add(account)
                    synced_count += 1
                    _log.info("[commit:create] Local account created %s (hs=%s)", local_id, hs_id)
                else:
                    _log.warning("[commit:create] No HS id for company %s: %s", local_id, hs_result)
            except Exception as e:
                _log.error("[commit:create] FAILED company %s: %s", local_id, e, exc_info=True)
                errors.append({"table": "accounts", "record_id": str(local_id), "error": str(e)})

        # ── Create deals ─────────────────────────────────────────────────
        for local_id, hs_props in deals_to_create:
            try:
                _log.info("[commit:create] Pushing deal %s to HubSpot: %s", local_id, hs_props)
                hs_result = await connector.create_deal(hs_props)
                hs_id = hs_result.get("id")
                _log.info("[commit:create] HubSpot returned id=%s for deal %s", hs_id, local_id)

                if hs_id:
                    amount: Decimal | None = None
                    if hs_props.get("amount") is not None:
                        try:
                            amount = Decimal(str(hs_props["amount"]))
                        except (ValueError, TypeError):
                            pass
                    deal = Deal(
                        id=local_id, organization_id=org_uuid, source_system="hubspot",
                        source_id=str(hs_id), name=hs_props.get("dealname") or "Untitled Deal",
                        amount=amount, stage=hs_props.get("dealstage"),
                        sync_status="synced", updated_at=now, updated_by=user_uuid,
                    )
                    session.add(deal)
                    synced_count += 1
                    _log.info("[commit:create] Local deal created %s (hs=%s)", local_id, hs_id)
                else:
                    _log.warning("[commit:create] No HS id for deal %s: %s", local_id, hs_result)
            except Exception as e:
                _log.error("[commit:create] FAILED deal %s: %s", local_id, e, exc_info=True)
                errors.append({"table": "deals", "record_id": str(local_id), "error": str(e)})

        # ── Create engagements (calls, emails, meetings, notes) ─────────
        def _is_uuid(s: str) -> bool:
            if not s or len(s) != 36:
                return False
            try:
                UUID(s)
                return True
            except (ValueError, TypeError):
                return False

        async def _resolve_association_ids(
            sess: Any,
            raw: list[dict[str, Any]],
        ) -> list[dict[str, Any]]:
            """Resolve internal UUIDs in to_object_id to HubSpot source_id."""
            resolved: list[dict[str, Any]] = []
            for assoc in raw:
                to_type: str = (assoc.get("to_object_type") or "").lower()
                to_id: str = str(assoc.get("to_object_id", ""))
                if not to_id:
                    continue
                if _is_uuid(to_id):
                    try:
                        uid = UUID(to_id)
                        if to_type == "deal":
                            row = await sess.get(Deal, uid)
                            if row and getattr(row, "source_id", None):
                                to_id = str(row.source_id)
                        elif to_type == "contact":
                            row = await sess.get(Contact, uid)
                            if row and getattr(row, "source_id", None):
                                to_id = str(row.source_id)
                        elif to_type == "company":
                            row = await sess.get(Account, uid)
                            if row and getattr(row, "source_id", None):
                                to_id = str(row.source_id)
                    except (ValueError, TypeError):
                        pass
                resolved.append({"to_object_type": to_type, "to_object_id": to_id})
            return resolved

        for local_id, eng_type, hs_props, raw_assocs in engagements_to_create:
            try:
                resolved_assocs: list[dict[str, Any]] = (
                    await _resolve_association_ids(session, raw_assocs)
                    if raw_assocs else []
                )
                hs_associations: list[dict[str, Any]] = (
                    connector.build_engagement_associations(eng_type, resolved_assocs)
                    if resolved_assocs else []
                )
                _log.info(
                    "[commit:create] Pushing %s %s to HubSpot: props=%s assocs=%d",
                    eng_type, local_id, list(hs_props.keys()), len(hs_associations),
                )
                hs_result = await connector.create_engagement(
                    engagement_type=eng_type,
                    properties=hs_props,
                    associations=hs_associations if hs_associations else None,
                )
                hs_id = hs_result.get("id")
                _log.info("[commit:create] HubSpot returned id=%s for %s %s", hs_id, eng_type, local_id)

                if hs_id:
                    from models.activity import Activity as ActivityModel

                    subject_val: str | None = (
                        hs_props.get("hs_call_title")
                        or hs_props.get("hs_email_subject")
                        or hs_props.get("hs_meeting_title")
                        or None
                    )
                    description_val: str | None = (
                        hs_props.get("hs_call_body")
                        or hs_props.get("hs_email_text")
                        or hs_props.get("hs_meeting_body")
                        or hs_props.get("hs_note_body")
                        or None
                    )
                    activity_date_val: datetime | None = None
                    ts_raw: str | None = hs_props.get("hs_timestamp")
                    if ts_raw:
                        try:
                            from dateutil.parser import parse as parse_dt
                            parsed: datetime = parse_dt(str(ts_raw))
                            # Store as naive UTC for TIMESTAMP WITHOUT TIME ZONE column
                            if getattr(parsed, "tzinfo", None) is not None:
                                activity_date_val = parsed.astimezone(timezone.utc).replace(tzinfo=None)
                            else:
                                activity_date_val = parsed
                        except Exception:
                            pass

                    activity = ActivityModel(
                        id=local_id, organization_id=org_uuid, source_system="hubspot",
                        source_id=str(hs_id), type=eng_type,
                        subject=subject_val, description=description_val,
                        activity_date=activity_date_val, custom_fields=hs_props,
                        updated_at=now, updated_by=user_uuid,
                    )
                    session.add(activity)
                    synced_count += 1
                    _log.info("[commit:create] Local activity created %s (hs=%s)", local_id, hs_id)
                else:
                    _log.warning("[commit:create] No HS id for %s %s: %s", eng_type, local_id, hs_result)
            except Exception as e:
                _log.error("[commit:create] FAILED %s %s: %s", eng_type, local_id, e, exc_info=True)
                errors.append({"table": "activities", "record_id": str(local_id), "error": str(e)})

        # ══════════════════════════════════════════════════════════════════
        # 3b. UPDATES – load existing record, push to HubSpot, apply local
        # ══════════════════════════════════════════════════════════════════

        # ── Update contacts ──────────────────────────────────────────────
        for local_id, hs_props, raw_fields in contacts_to_update:
            try:
                existing = await session.get(Contact, local_id)
                if not existing:
                    _log.warning("[commit:update] Contact %s not found locally – skipping", local_id)
                    errors.append({"table": "contacts", "record_id": str(local_id), "error": "Local record not found"})
                    continue
                hs_source_id: str | None = existing.source_id
                _log.info("[commit:update] Contact %s (hs=%s) updating with: %s", local_id, hs_source_id, hs_props)

                if hs_source_id and hs_props:
                    await connector.update_contact(hs_source_id, hs_props)
                    _log.info("[commit:update] HubSpot contact %s updated", hs_source_id)

                # Apply locally using raw field names (map to model attrs)
                for field_key, field_val in raw_fields.items():
                    model_attr: str | None = _CONTACT_FIELD_MAP.get(field_key)
                    if model_attr and hasattr(existing, model_attr):
                        setattr(existing, model_attr, field_val)
                existing.updated_at = now
                existing.updated_by = user_uuid
                synced_count += 1
                _log.info("[commit:update] Local contact %s updated", local_id)
            except Exception as e:
                _log.error("[commit:update] FAILED contact %s: %s", local_id, e, exc_info=True)
                errors.append({"table": "contacts", "record_id": str(local_id), "error": str(e)})

        # ── Update accounts ──────────────────────────────────────────────
        for local_id, hs_props, raw_fields in accounts_to_update:
            try:
                existing_acct = await session.get(Account, local_id)
                if not existing_acct:
                    _log.warning("[commit:update] Account %s not found locally – skipping", local_id)
                    errors.append({"table": "accounts", "record_id": str(local_id), "error": "Local record not found"})
                    continue
                hs_source_id = existing_acct.source_id
                _log.info("[commit:update] Account %s (hs=%s) updating with: %s", local_id, hs_source_id, hs_props)

                if hs_source_id and hs_props:
                    await connector.update_company(hs_source_id, hs_props)
                    _log.info("[commit:update] HubSpot company %s updated", hs_source_id)

                for field_key, field_val in raw_fields.items():
                    model_attr = _ACCOUNT_FIELD_MAP.get(field_key)
                    if model_attr and hasattr(existing_acct, model_attr):
                        setattr(existing_acct, model_attr, field_val)
                existing_acct.updated_at = now
                existing_acct.updated_by = user_uuid
                synced_count += 1
                _log.info("[commit:update] Local account %s updated", local_id)
            except Exception as e:
                _log.error("[commit:update] FAILED account %s: %s", local_id, e, exc_info=True)
                errors.append({"table": "accounts", "record_id": str(local_id), "error": str(e)})

        # ── Update deals ─────────────────────────────────────────────────
        for local_id, hs_props, raw_fields in deals_to_update:
            try:
                existing_deal = await session.get(Deal, local_id)
                if not existing_deal:
                    _log.warning("[commit:update] Deal %s not found locally – skipping", local_id)
                    errors.append({"table": "deals", "record_id": str(local_id), "error": "Local record not found"})
                    continue
                hs_source_id = existing_deal.source_id
                _log.info("[commit:update] Deal %s (hs=%s) updating with: %s", local_id, hs_source_id, hs_props)

                if hs_source_id and hs_props:
                    await connector.update_deal(hs_source_id, hs_props)
                    _log.info("[commit:update] HubSpot deal %s updated", hs_source_id)

                for field_key, field_val in raw_fields.items():
                    model_attr = _DEAL_FIELD_MAP.get(field_key)
                    if model_attr and hasattr(existing_deal, model_attr):
                        if model_attr == "amount" and field_val is not None:
                            try:
                                setattr(existing_deal, model_attr, Decimal(str(field_val)))
                            except (ValueError, TypeError):
                                pass
                        else:
                            setattr(existing_deal, model_attr, field_val)
                existing_deal.updated_at = now
                existing_deal.updated_by = user_uuid
                synced_count += 1
                _log.info("[commit:update] Local deal %s updated", local_id)
            except Exception as e:
                _log.error("[commit:update] FAILED deal %s: %s", local_id, e, exc_info=True)
                errors.append({"table": "deals", "record_id": str(local_id), "error": str(e)})

        # ── 4. Mark session approved in the SAME session (RLS context already set) ──
        change_session_obj = await session.get(ChangeSession, UUID(change_session_id))
        if change_session_obj:
            change_session_obj.status = "approved"
            change_session_obj.resolved_at = datetime.utcnow()
            change_session_obj.resolved_by = UUID(user_id) if user_id else None

        await session.commit()
        _log.info("[commit] DB commit complete – %d rows synced, session marked approved", synced_count)

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




async def _enrich_with_apollo(
    params: dict[str, Any], organization_id: str
) -> dict[str, Any]:
    """Unified Apollo enrichment dispatcher."""
    enrich_type: str = params.get("type", "contacts")
    if enrich_type == "company":
        return await _enrich_company_with_apollo(params, organization_id)
    return await _enrich_contacts_with_apollo(params, organization_id)


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
        import asyncio

        connector = ApolloConnector(organization_id)

        # Use single-person /people/match (free-plan compatible); bulk_match requires paid plan
        enriched_contacts: list[dict[str, Any]] = []
        match_count = 0
        no_match_count = 0

        for i, person in enumerate(contacts):
            original: dict[str, Any] = person if isinstance(person, dict) else {}
            enrichment: dict[str, Any] | None = await connector.enrich_person(
                email=original.get("email"),
                first_name=original.get("first_name"),
                last_name=original.get("last_name"),
                domain=original.get("domain"),
                linkedin_url=original.get("linkedin_url"),
                organization_name=original.get("organization_name"),
                reveal_personal_emails=reveal_personal_emails,
                reveal_phone_number=reveal_phone_numbers,
            )
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
            # Brief pause between calls to respect rate limits
            if i < len(contacts) - 1:
                await asyncio.sleep(0.5)

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




async def _send_sms(
    params: dict[str, Any],
    organization_id: str,
    user_id: str | None,
) -> dict[str, Any]:
    """Send an SMS text message via Twilio."""
    from services.sms import send_sms

    to: str = params.get("to", "").strip()
    body: str = params.get("body", "").strip()

    if not to:
        return {"error": "to is required (E.164 phone number, e.g. +14155551234)."}
    if not body:
        return {"error": "body is required."}

    # Normalise bare US 10-digit numbers
    import re as _re

    digits_only: str = _re.sub(r"[^\d]", "", to)
    if not to.startswith("+"):
        if len(digits_only) == 10:
            digits_only = f"1{digits_only}"
        to = f"+{digits_only}"

    if len(digits_only) < 7 or len(digits_only) > 15:
        return {"error": f"Invalid phone number '{to}'. Expected E.164 format, e.g. +14155551234."}

    result: dict[str, str | bool] = await send_sms(to=to, body=body)

    if result.get("success"):
        return {
            "status": "sent",
            "to": to,
            "message_sid": result.get("message_sid"),
        }
    return {"error": result.get("error", "Failed to send SMS.")}


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

# Maximum number of workflows a workflow execution tree may create.
MAX_CREATED_CHILD_WORKFLOWS: int = 5

# Maximum items that can be processed in loop_over
MAX_LOOP_ITEMS: int = 500

# Maximum concurrent workflow executions in loop_over
MAX_CONCURRENT_WORKFLOWS: int = 10


def _workflow_child_creation_limit_error(
    context: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Return an error when a workflow execution has created too many child workflows."""
    if not context or not context.get("is_workflow"):
        return None

    created_count = int(context.get("created_workflow_count", 0))
    if created_count < MAX_CREATED_CHILD_WORKFLOWS:
        return None

    workflow_id = context.get("workflow_id")
    logger.warning(
        "[Tools._run_sql_write] Blocked workflow child creation limit: workflow_id=%s created=%d limit=%d",
        workflow_id,
        created_count,
        MAX_CREATED_CHILD_WORKFLOWS,
    )
    return {
        "error": (
            "Workflow child-creation limit reached. "
            f"A workflow execution can create at most {MAX_CREATED_CHILD_WORKFLOWS} child workflows."
        ),
        "status": "rejected",
    }


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
    raw_input: dict[str, Any] = params.get("input_data", {}) or {}
    # Strip None values — the agent uses None to mean "not provided"
    input_data: dict[str, Any] = {k: v for k, v in raw_input.items() if v is not None}
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
        
        # Parent workflow permissions must always bound child permissions.
        inherited_auto_approve_tools: list[str] | None = None
        if context:
            inherited_auto_approve_tools = context.get("auto_approve_tools")

        parent_context: dict[str, Any] = {
            "call_stack": call_stack + [workflow_id],
            "parent_workflow_id": context.get("workflow_id") if context else None,
            "parent_conversation_id": context.get("conversation_id") if context else None,
        }
        if inherited_auto_approve_tools is not None:
            parent_context["auto_approve_tools"] = inherited_auto_approve_tools

        if not wait_for_completion:
            # Fire and forget - queue via Celery
            from workers.tasks.workflows import execute_workflow
            task = execute_workflow.delay(
                workflow_id,
                "run_workflow",
                {
                    **input_data,
                    "_parent_context": parent_context,
                },
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
        # Build trigger_data with parent context (child workflow will set root_conversation_id from this)
        trigger_data: dict[str, Any] = {
            **input_data,
            "_parent_context": parent_context,
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
    
    # Send initial progress update (0 completed)
    await send_progress()
    
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
    conversation_id: str | None = progress.conversation_id
    
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
            conversation_id=UUID(conversation_id) if conversation_id else None,
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
    
    # Send final completion update
    await progress.update({
        "message": f"Created {content_type} artifact: {title}",
        "status": "complete",
        "title": title,
        "content_type": content_type,
        "chars_processed": content_len,
        "total_chars": content_len,
    }, status="complete")
    
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


# =============================================================================
# Google Drive Tools
# =============================================================================


async def _search_cloud_files(
    params: dict[str, Any], organization_id: str, user_id: str | None
) -> dict[str, Any]:
    """
    Search synced cloud files by name across all sources.

    Queries the shared_files table directly (no connector needed for search).
    """
    name_query: str = params.get("name_query", "").strip()
    source_filter: str | None = params.get("source")
    limit: int = min(params.get("limit", 20), 50)

    if not name_query:
        return {"error": "name_query is required."}

    try:
        from uuid import UUID as _UUID
        from sqlalchemy import select, and_
        from models.shared_file import SharedFile
        from models.database import get_session

        org_uuid = _UUID(organization_id)

        # Normalise wildcard-only queries (e.g. "*") to match all files
        cleaned_query: str = name_query.replace("*", "").strip()

        # Org-wide search: all team members' files are visible
        filters: list[Any] = [
            SharedFile.organization_id == org_uuid,
            SharedFile.mime_type != "application/vnd.google-apps.folder",
        ]

        if cleaned_query:
            like_pattern: str = f"%{cleaned_query}%"
            filters.append(SharedFile.name.ilike(like_pattern))

        if source_filter:
            filters.append(SharedFile.source == source_filter)

        async with get_session(organization_id=organization_id) as session:
            query = (
                select(SharedFile)
                .where(and_(*filters))
                .order_by(SharedFile.source_modified_at.desc())
                .limit(limit)
            )
            result = await session.execute(query)
            rows: list[SharedFile] = list(result.scalars().all())

        files: list[dict[str, Any]] = [row.to_dict() for row in rows]

        if not files:
            return {
                "files": [],
                "count": 0,
                "message": (
                    f"No files matching '{name_query}' found. "
                    "Make sure your cloud files have been synced from the Data Sources page."
                ),
            }

        return {
            "files": files,
            "count": len(files),
        }
    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        logger.error("[Tools._search_cloud_files] Failed: %s", e)
        return {"error": f"Failed to search cloud files: {str(e)}"}


async def _read_cloud_file(
    params: dict[str, Any], organization_id: str, user_id: str | None
) -> dict[str, Any]:
    """
    Read the text content of a synced cloud file.

    Looks up the file's source, then dispatches to the appropriate connector.
    """
    external_id: str = params.get("external_id", "").strip()

    if not external_id:
        return {"error": "external_id is required."}

    try:
        from uuid import UUID as _UUID
        from sqlalchemy import select, and_
        from models.shared_file import SharedFile
        from models.database import get_session

        # Look up the file to determine its source (org-wide, not user-scoped)
        org_uuid = _UUID(organization_id)

        async with get_session(organization_id=organization_id) as session:
            result = await session.execute(
                select(SharedFile).where(
                    and_(
                        SharedFile.organization_id == org_uuid,
                        SharedFile.external_id == external_id,
                    )
                )
            )
            file_record: SharedFile | None = result.scalars().first()

        if not file_record:
            return {"error": f"File not found in synced metadata: {external_id}"}

        source: str = file_record.source
        # Use the file owner's credentials to fetch content from the source API
        file_owner_id: str = str(file_record.user_id)

        # Dispatch to the right connector based on source
        if source == "google_drive":
            from connectors.google_drive import GoogleDriveConnector
            connector = GoogleDriveConnector(organization_id, file_owner_id)
            return await connector.get_file_content(external_id)
        else:
            return {"error": f"Reading files from '{source}' is not yet supported."}

    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        logger.error("[Tools._read_cloud_file] Failed: %s", e)
        return {"error": f"Failed to read cloud file: {str(e)}"}


# =============================================================================
# User Memory Tools
# =============================================================================


async def _keep_notes(
    params: dict[str, Any],
    organization_id: str,
    user_id: str | None,
    context: dict[str, Any] | None,
    skip_approval: bool = False,
) -> dict[str, Any]:
    """Save a workflow-scoped note that can be reused in future runs."""
    content: str = params.get("content", "").strip()
    if not content:
        return {"error": "content is required."}

    workflow_id: str | None = (context or {}).get("workflow_id")
    run_id: str | None = (context or {}).get("workflow_run_id")
    if not workflow_id or not run_id:
        return {"error": "keep_notes can only be used in a workflow run."}

    if skip_approval:
        logger.info("[Tools._keep_notes] Auto-approved, saving note immediately")
        return await execute_keep_notes(params, organization_id, user_id, workflow_id, run_id)

    operation_id = str(uuid4())
    pending_params = dict(params)
    pending_params["workflow_id"] = workflow_id
    pending_params["run_id"] = run_id
    store_pending_operation(
        operation_id=operation_id,
        tool_name="keep_notes",
        params=pending_params,
        organization_id=organization_id,
        user_id=user_id,
    )

    return {
        "type": "pending_approval",
        "status": "pending_approval",
        "operation_id": operation_id,
        "tool_name": "keep_notes",
        "preview": {
            "content": content[:500] + ("..." if len(content) > 500 else ""),
        },
        "message": "Ready to keep workflow notes. Please review and click Approve to persist them.",
    }


async def execute_keep_notes(
    params: dict[str, Any],
    organization_id: str,
    user_id: str | None,
    workflow_id: str,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Persist a workflow-scoped note onto the active workflow run."""
    content: str = params.get("content", "").strip()
    if not content:
        return {"status": "failed", "error": "content is required."}

    from datetime import datetime, timezone
    from sqlalchemy import and_
    from models.workflow import WorkflowRun

    if not run_id:
        return {"status": "failed", "error": "run_id is required for keep_notes."}

    async with get_session(organization_id=organization_id) as session:
        result = await session.execute(
            select(WorkflowRun).where(
                and_(
                    WorkflowRun.id == UUID(run_id),
                    WorkflowRun.organization_id == UUID(organization_id),
                    WorkflowRun.workflow_id == UUID(workflow_id),
                )
            )
        )
        run: WorkflowRun | None = result.scalar_one_or_none()
        if not run:
            logger.warning(
                "[Tools.execute_keep_notes] Workflow run not found: run_id=%s workflow_id=%s",
                run_id,
                workflow_id,
            )
            return {"status": "failed", "error": "Workflow run not found for keep_notes."}

        notes = list(run.workflow_notes or [])
        notes.append(
            {
                "content": content,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "created_by_user_id": user_id,
            }
        )
        run.workflow_notes = notes
        await session.commit()

    return {"note_id": f"{run_id}:{len(notes) - 1}", "content": content, "status": "saved"}


async def _manage_memory(
    params: dict[str, Any], organization_id: str, user_id: str | None, skip_approval: bool = False
) -> dict[str, Any]:
    """Unified memory dispatcher: save, update, or delete."""
    action: str = params.get("action", "save")
    if action == "delete":
        return await _delete_memory(params, organization_id, user_id)
    if action == "update":
        return await _update_memory(params, organization_id, user_id)
    return await _save_memory(params, organization_id, user_id, skip_approval)


async def _save_memory(
    params: dict[str, Any],
    organization_id: str,
    user_id: str | None,
    skip_approval: bool = False,
) -> dict[str, Any]:
    """Save a persistent memory at the user, organization, or job level."""
    content: str = params.get("content", "").strip()
    if not content:
        return {"error": "content is required."}
    if not user_id:
        return {"error": "Cannot save memory without a user context."}

    entity_type: str = params.get("entity_type", "user").strip()
    if entity_type not in ("user", "organization", "organization_member"):
        return {"error": f"Invalid entity_type '{entity_type}'. Must be 'user', 'organization', or 'organization_member'."}

    if skip_approval:
        logger.info("[Tools._save_memory] Auto-approved, saving memory immediately")
        return await execute_save_memory(params, organization_id, user_id)

    operation_id = str(uuid4())
    store_pending_operation(
        operation_id=operation_id,
        tool_name="manage_memory",
        params=params,
        organization_id=organization_id,
        user_id=user_id,
    )

    return {
        "type": "pending_approval",
        "status": "pending_approval",
        "operation_id": operation_id,
        "tool_name": "manage_memory",
        "preview": {
            "content": content[:500] + ("..." if len(content) > 500 else ""),
            "entity_type": entity_type,
        },
        "message": "Ready to save memory. Please review and click Approve to persist it.",
    }


async def execute_save_memory(
    params: dict[str, Any],
    organization_id: str,
    user_id: str,
) -> dict[str, Any]:
    """Persist a memory after approval."""
    content: str = params.get("content", "").strip()
    if not content:
        return {"status": "failed", "error": "content is required."}

    entity_type: str = params.get("entity_type", "user").strip()
    category: str | None = params.get("category")
    if category:
        category = category.strip() or None

    from models.memory import Memory
    from models.org_member import OrgMember

    # Resolve the entity_id based on entity_type
    entity_id: UUID
    if entity_type == "user":
        entity_id = UUID(user_id)
    elif entity_type == "organization":
        entity_id = UUID(organization_id)
    elif entity_type == "organization_member":
        # Look up the membership for this user + org
        async with get_session(organization_id=organization_id) as session:
            result = await session.execute(
                select(OrgMember.id).where(
                    OrgMember.user_id == UUID(user_id),
                    OrgMember.organization_id == UUID(organization_id),
                )
            )
            membership_id: UUID | None = result.scalar_one_or_none()
            if not membership_id:
                return {"status": "failed", "error": "No organization membership found for this user."}
            entity_id = membership_id
    else:
        return {"status": "failed", "error": f"Invalid entity_type '{entity_type}'."}

    memory = Memory(
        entity_type=entity_type,
        entity_id=entity_id,
        organization_id=UUID(organization_id),
        category=category,
        content=content,
        created_by_user_id=UUID(user_id),
    )

    async with get_session(organization_id=organization_id) as session:
        session.add(memory)
        await session.commit()

    return {
        "memory_id": str(memory.id),
        "entity_type": entity_type,
        "content": content,
        "status": "saved",
    }


async def _delete_memory(
    params: dict[str, Any],
    organization_id: str,
    user_id: str | None,
) -> dict[str, Any]:
    """Delete a previously saved memory by ID."""
    memory_id: str = params.get("memory_id", "").strip()
    if not memory_id:
        return {"error": "memory_id is required."}
    if not user_id:
        return {"error": "Cannot delete memory without a user context."}

    from models.memory import Memory

    async with get_session(organization_id=organization_id) as session:
        result = await session.execute(
            select(Memory).where(
                Memory.id == UUID(memory_id),
                Memory.organization_id == UUID(organization_id),
            )
        )
        memory: Memory | None = result.scalar_one_or_none()
        if not memory:
            return {"error": f"Memory {memory_id} not found."}

        # User-level and job-level memories can only be deleted by the owner.
        # Org-level memories can be deleted by any org member.
        if memory.entity_type != "organization":
            if memory.created_by_user_id and str(memory.created_by_user_id) != user_id:
                return {"error": f"Memory {memory_id} does not belong to this user."}

        await session.delete(memory)
        await session.commit()

    return {"status": "deleted", "memory_id": memory_id}


async def _update_memory(
    params: dict[str, Any],
    organization_id: str,
    user_id: str | None,
) -> dict[str, Any]:
    """Update the content of an existing memory."""
    memory_id: str = params.get("memory_id", "").strip()
    if not memory_id:
        return {"error": "memory_id is required."}
    new_content: str = params.get("content", "").strip()
    if not new_content:
        return {"error": "content is required."}
    if not user_id:
        return {"error": "Cannot update memory without a user context."}

    from models.memory import Memory

    async with get_session(organization_id=organization_id) as session:
        result = await session.execute(
            select(Memory).where(
                Memory.id == UUID(memory_id),
                Memory.organization_id == UUID(organization_id),
            )
        )
        memory: Memory | None = result.scalar_one_or_none()
        if not memory:
            return {"error": f"Memory {memory_id} not found."}

        # Permission check: same rules as delete
        if memory.entity_type != "organization":
            if memory.created_by_user_id and str(memory.created_by_user_id) != user_id:
                return {"error": f"Memory {memory_id} does not belong to this user."}

        memory.content = new_content
        await session.commit()

    return {"status": "updated", "memory_id": memory_id, "content": new_content}


# =============================================================================
# Bulk Tool Run — General-purpose parallel tool execution
# =============================================================================


async def _bulk_tool_run(
    params: dict[str, Any],
    organization_id: str,
    user_id: str | None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Run any registered tool over a list of items in parallel via Celery.

    The items are specified via ``items_query`` (a SQL SELECT) or ``items``
    (an inline list).  The tool is called once per item with params built from
    ``params_template`` by substituting ``{{field}}`` placeholders.

    Returns immediately with an ``operation_id`` that the agent can monitor
    via ``monitor_operation``.
    """
    from models.bulk_operation import BulkOperation
    from workers.tasks.bulk_operations import bulk_tool_run_coordinator

    tool_name: str = params.get("tool", "").strip()
    items_query: str | None = params.get("items_query")
    inline_items: list[dict[str, Any]] | None = params.get("items")
    params_template: dict[str, Any] = params.get("params_template", {})
    rate_limit: int = min(max(params.get("rate_limit_per_minute", 200), 1), 2000)
    operation_name: str = params.get("operation_name", f"Bulk {tool_name}").strip()

    if not tool_name:
        return {"error": "tool is required (the name of the tool to run per item)."}

    if not params_template:
        return {"error": "params_template is required (how to build tool params from each item)."}

    if not items_query and not inline_items:
        return {
            "error": "Either items_query (SQL SELECT) or items (inline list) is required.",
        }

    # Validate items_query is a SELECT
    if items_query:
        stripped: str = items_query.strip()
        if not stripped.upper().startswith("SELECT"):
            return {"error": "items_query must be a SELECT statement."}

    # Extract conversation / tool_call context for progress broadcasts
    conversation_id: str | None = (context or {}).get("conversation_id")
    tool_call_id: str | None = (context or {}).get("tool_id")

    # Create the BulkOperation record
    async with get_session(organization_id=organization_id) as session:
        operation = BulkOperation(
            organization_id=UUID(organization_id),
            user_id=UUID(user_id) if user_id else None,
            operation_name=operation_name,
            tool_name=tool_name,
            params_template=params_template,
            items_query=items_query,
            rate_limit_per_minute=rate_limit,
            conversation_id=conversation_id,
            tool_call_id=tool_call_id,
            status="pending",
        )
        session.add(operation)
        await session.commit()
        await session.refresh(operation)
        op_id: str = str(operation.id)

    # If inline items were provided, we need to store them temporarily.
    # The coordinator will use items_query if present, otherwise it reads
    # the items from a Redis key (for inline items that are too large for
    # the Celery message).
    if inline_items and not items_query:
        import redis.asyncio as aioredis
        from config import settings

        r: aioredis.Redis = aioredis.from_url(
            settings.REDIS_URL, decode_responses=True
        )
        try:
            # Stringify UUIDs in inline items
            clean_items: list[dict[str, Any]] = []
            for item in inline_items:
                clean: dict[str, Any] = {}
                for k, v in item.items():
                    if isinstance(v, UUID):
                        clean[k] = str(v)
                    else:
                        clean[k] = v
                clean_items.append(clean)
            await r.set(
                f"bulk_op:{op_id}:items",
                json.dumps(clean_items),
                ex=3600 * 6,  # Expire after 6 hours
            )
        finally:
            await r.close()

    # Queue the coordinator task
    task = bulk_tool_run_coordinator.delay(
        operation_id=op_id,
        organization_id=organization_id,
        user_id=user_id,
    )

    # Update celery task ID
    async with get_session(organization_id=organization_id) as session:
        result = await session.execute(
            select(BulkOperation).where(BulkOperation.id == UUID(op_id))
        )
        op = result.scalar_one()
        op.celery_task_id = task.id
        await session.commit()

    return {
        "status": "queued",
        "operation_id": op_id,
        "operation_name": operation_name,
        "tool": tool_name,
        "rate_limit_per_minute": rate_limit,
        "message": (
            f"Bulk operation '{operation_name}' queued. "
            f"Use monitor_operation(operation_id='{op_id}') to wait for completion."
        ),
    }


async def _monitor_operation(
    params: dict[str, Any],
    organization_id: str,
    context: dict[str, Any] | None,
) -> dict[str, Any]:
    """Poll a bulk operation until completion, broadcasting live progress via WebSocket.

    Reads real-time progress from Redis counters (updated atomically by each
    item task) and checks the DB for terminal status (set by the last item).

    Safety features:
    - Max polling duration (4 hours) to prevent zombie tasks
    - Stale detection: if no progress changes for 10 minutes, bail out
    """
    import asyncio
    import time
    import redis.asyncio as aioredis
    from models.bulk_operation import BulkOperation
    from config import settings

    op_id: str = params.get("operation_id", "").strip()
    if not op_id:
        return {"error": "operation_id is required."}

    progress: ToolProgressUpdater = ToolProgressUpdater(context, organization_id)
    poll_interval: float = 5.0
    terminal_statuses: frozenset[str] = frozenset({"completed", "failed", "cancelled"})

    # Safety limits
    max_poll_seconds: float = 4 * 3600  # 4 hours absolute max
    stale_timeout_seconds: float = 600.0  # 10 minutes with no progress change
    start_time: float = time.monotonic()
    last_progress_change_time: float = start_time
    last_completed: int = -1

    def _rkey(field: str) -> str:
        return f"bulk_op:{op_id}:{field}"

    r: aioredis.Redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        while True:
            elapsed: float = time.monotonic() - start_time

            # Absolute timeout
            if elapsed > max_poll_seconds:
                logger.warning(
                    "[monitor_operation] Aborting after %.0fs for operation %s",
                    elapsed, op_id[:8],
                )
                return {
                    "operation_id": op_id,
                    "status": "timeout",
                    "error": f"Monitoring timed out after {elapsed / 3600:.1f} hours.",
                    "message": "The operation may still be running. Check status with run_sql_query.",
                }

            # Read real-time counters from Redis
            total_raw: str | None = await r.get(_rkey("total"))
            completed_raw: str | None = await r.get(_rkey("completed"))
            succeeded_raw: str | None = await r.get(_rkey("succeeded"))
            failed_raw: str | None = await r.get(_rkey("failed"))

            total: int = int(total_raw) if total_raw else 0
            completed: int = int(completed_raw) if completed_raw else 0
            succeeded: int = int(succeeded_raw) if succeeded_raw else 0
            failed: int = int(failed_raw) if failed_raw else 0

            # Check DB for status (terminal status set by last item task)
            async with get_session(organization_id=organization_id) as session:
                result = await session.execute(
                    select(BulkOperation).where(
                        BulkOperation.id == UUID(op_id),
                        BulkOperation.organization_id == UUID(organization_id),
                    )
                )
                operation: BulkOperation | None = result.scalar_one_or_none()

            if not operation:
                return {"error": f"Bulk operation {op_id} not found."}

            status: str = operation.status
            op_name: str = operation.operation_name or "bulk operation"

            # Use Redis counters if available, fall back to DB
            if total == 0:
                total = operation.total_items
                completed = operation.completed_items
                succeeded = operation.succeeded_items
                failed = operation.failed_items

            # Track stale progress
            if completed != last_completed:
                last_completed = completed
                last_progress_change_time = time.monotonic()
            elif time.monotonic() - last_progress_change_time > stale_timeout_seconds:
                logger.warning(
                    "[monitor_operation] No progress for %.0fs on operation %s (completed=%d/%d), aborting",
                    stale_timeout_seconds, op_id[:8], completed, total,
                )
                return {
                    "operation_id": op_id,
                    "operation_name": op_name,
                    "status": "stale",
                    "total_items": total,
                    "succeeded_items": succeeded,
                    "failed_items": failed,
                    "error": f"No progress for {stale_timeout_seconds / 60:.0f} minutes.",
                    "message": (
                        f"Stale: {completed}/{total} completed but no progress for "
                        f"{stale_timeout_seconds / 60:.0f} min. Check status with run_sql_query."
                    ),
                }

            # Broadcast progress to UI
            await progress.update({
                "operation_id": op_id,
                "operation_name": op_name,
                "total": total,
                "completed": completed,
                "succeeded": succeeded,
                "failed": failed,
                "status": status,
            })

            if status in terminal_statuses:
                response: dict[str, Any] = {
                    "operation_id": op_id,
                    "operation_name": op_name,
                    "status": status,
                    "total_items": total,
                    "succeeded_items": succeeded,
                    "failed_items": failed,
                }
                if status == "completed":
                    response["message"] = (
                        f"Completed: {succeeded} succeeded, {failed} failed out of {total}."
                    )
                elif status == "failed":
                    response["error"] = operation.error
                    response["message"] = f"Failed: {operation.error}"
                else:
                    response["message"] = "Cancelled."
                return response

            await asyncio.sleep(poll_interval)
    finally:
        await r.close()


