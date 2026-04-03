import warnings

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from config import settings


@pytest.mark.asyncio
async def test_warn_for_public_tables_without_rls() -> None:
    """Inspect all public tables and warn for each table with RLS disabled."""
    engine = create_async_engine(settings.DATABASE_URL, future=True)

    try:
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        """
                        SELECT c.relname AS table_name, c.relrowsecurity AS rls_enabled
                        FROM pg_class c
                        JOIN pg_namespace n ON n.oid = c.relnamespace
                        WHERE n.nspname = 'public'
                          AND c.relkind = 'r'
                        ORDER BY c.relname
                        """
                    )
                )
            ).mappings().all()
    except Exception as exc:  # pragma: no cover - environment-dependent connectivity
        pytest.skip(f"Unable to connect to database for RLS coverage check: {exc}")
    finally:
        await engine.dispose()

    assert rows, "Expected at least one user table in public schema to evaluate RLS coverage."

    ignored_tables = {"alembic_version"}
    tables_without_rls = [
        row["table_name"]
        for row in rows
        if not row["rls_enabled"] and row["table_name"] not in ignored_tables
    ]

    for table_name in tables_without_rls:
        warnings.warn(
            f"RLS is disabled for table: {table_name}",
            category=UserWarning,
            stacklevel=1,
        )
