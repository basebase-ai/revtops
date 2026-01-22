"""
Tool definitions and execution for Claude.

Tools:
- run_sql_query: Execute arbitrary SELECT queries (read-only)
- create_artifact: Save analysis/dashboard
"""

import logging
import re
from typing import Any
from uuid import UUID

from sqlalchemy import text

from models.artifact import Artifact
from models.database import get_session

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

    elif tool_name == "create_artifact":
        result = await _create_artifact(tool_input, organization_id, user_id)
        logger.info("[Tools] create_artifact result: %s", result)
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
