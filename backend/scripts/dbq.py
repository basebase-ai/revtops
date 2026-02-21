#!/usr/bin/env python3
"""
Quick SQL query tool against DATABASE_URL.

Usage:
    python scripts/dbq.py "SELECT * FROM conversations LIMIT 5"
    python backend/scripts/dbq.py "SELECT id, role, created_at FROM chat_messages WHERE conversation_id = '...'"
    echo "SELECT 1" | python scripts/dbq.py

Run from backend/ or project root. Reads .env from project root.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

# .env at project root (backend/scripts -> backend -> project root)
_env_path: Path = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(_env_path)

_raw_url: str = os.environ.get("DATABASE_URL", "")
# psycopg2 needs plain postgresql:// — strip the +asyncpg driver suffix
DB_URL: str = _raw_url.replace("+asyncpg", "")

if not DB_URL:
    print("ERROR: DATABASE_URL not set", file=sys.stderr)
    sys.exit(1)


def _serialise(obj: object) -> str:
    """JSON-safe serialisation for common DB types."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, Decimal):
        return float(obj)  # type: ignore[return-value]
    return repr(obj)


def run_query(sql: str) -> None:
    """Execute *sql*, pretty-print results (or row count for non-SELECT)."""
    conn: psycopg2.extensions.connection = psycopg2.connect(DB_URL)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql)

            if cur.description is None:
                # DML / DDL — no result set
                conn.commit()
                print(f"OK — {cur.rowcount} row(s) affected")
                return

            rows: list[dict] = cur.fetchall()
            if not rows:
                print("(0 rows)")
                return

            print(json.dumps(
                [dict(r) for r in rows],
                default=_serialise,
                indent=2,
                ensure_ascii=False,
            ))
            print(f"\n({len(rows)} row(s))")
    finally:
        conn.close()


if __name__ == "__main__":
    query: str
    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
    elif not sys.stdin.isatty():
        query = sys.stdin.read().strip()
    else:
        print(__doc__.strip())
        sys.exit(0)

    if not query:
        print("No SQL provided.", file=sys.stderr)
        sys.exit(1)

    run_query(query)
