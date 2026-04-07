"""Execute named SELECT queries from an App's server-side spec (shared by API routes)."""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from access_control import RightsContext, check_sql
from models.app import App

logger = logging.getLogger(__name__)


class AppQueryResponse(BaseModel):
    data: list[dict[str, Any]]
    columns: list[str]


_SELECT_RE = re.compile(r"^\s*SELECT\b", re.IGNORECASE)
_DANGEROUS_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|EXEC|EXECUTE)\b",
    re.IGNORECASE,
)


def validate_sql_is_select(sql: str) -> None:
    """Raise if the SQL is not a plain SELECT statement."""
    if not _SELECT_RE.match(sql):
        raise ValueError("Only SELECT queries are allowed")
    if _DANGEROUS_RE.search(sql):
        raise ValueError("Query contains disallowed SQL keywords")


def json_serial(obj: Any) -> Any:
    """JSON serializer for types not handled by default."""
    if isinstance(obj, datetime):
        if obj.tzinfo is not None:
            return obj.isoformat()
        return f"{obj.isoformat()}Z"
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, UUID):
        return str(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


async def run_named_app_query(
    *,
    app: App,
    organization_id: str,
    query_name: str,
    params: dict[str, Any],
    session: AsyncSession,
) -> AppQueryResponse:
    """Run a named query from `app.queries` with rights checks and RLS session."""
    queries: dict[str, Any] = app.queries or {}
    query_spec: dict[str, Any] | None = queries.get(query_name)

    if query_spec is None:
        raise HTTPException(
            status_code=404,
            detail=f"Query '{query_name}' not found in app spec",
        )

    sql: str = query_spec.get("sql", "")
    try:
        validate_sql_is_select(sql)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    param_defs: dict[str, Any] = query_spec.get("params", {})
    bound_params: dict[str, Any] = {"org_id": organization_id}
    for pname, pdef in param_defs.items():
        value: Any = params.get(pname)
        if value is None and pdef.get("required", False):
            raise HTTPException(
                status_code=400,
                detail=f"Missing required parameter: {pname}",
            )
        if value is not None:
            bound_params[pname] = value

    sql_upper: str = sql.upper()
    if "LIMIT" not in sql_upper:
        sql = f"{sql.rstrip().rstrip(';')} LIMIT 5000"

    rights_ctx = RightsContext(
        organization_id=organization_id,
        user_id=None,
        conversation_id=None,
        is_workflow=False,
    )
    rights_result = await check_sql(rights_ctx, sql, bound_params)
    if not rights_result.allowed:
        raise HTTPException(
            status_code=403,
            detail=rights_result.deny_reason or "Query not allowed",
        )
    query_to_run: str = (
        rights_result.transformed_query if rights_result.transformed_query is not None else sql
    )
    params_to_use: dict[str, Any] = (
        rights_result.transformed_params if rights_result.transformed_params is not None else bound_params
    )

    try:
        raw_result = await session.execute(text(query_to_run), params_to_use)
        rows = raw_result.mappings().all()
        columns: list[str] = list(raw_result.keys()) if rows else []

        data: list[dict[str, Any]] = [
            {
                k: json_serial(v)
                if not isinstance(v, (str, int, float, bool, type(None)))
                else v
                for k, v in dict(row).items()
            }
            for row in rows
        ]

        return AppQueryResponse(data=data, columns=columns)
    except Exception as exc:
        logger.error("App query execution failed: %s", exc)
        raise HTTPException(status_code=400, detail=f"Query error: {exc}") from exc
