#!/usr/bin/env python3
"""
Quick integration test for foreach (tool mode — Celery path).

Usage:
    cd backend
    source venv/bin/activate
    python3 test_bulk_tool_run.py

Requires:
    - API server running (uvicorn)
    - Celery worker running with enrichment queue:
        PYTHONPATH=. celery -A workers.celery_app worker --loglevel=info -Q default,sync,workflows,enrichment
    - Redis running
    - .env loaded with DATABASE_URL, REDIS_URL, PERPLEXITY_API_KEY
"""

import asyncio
import json
import sys
from pathlib import Path

# Ensure backend is on path
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")
if not (Path(__file__).parent / ".env").exists():
    load_dotenv(Path(__file__).parent.parent / ".env")


async def test_small_batch() -> None:
    """Test with a tiny inline list (no DB query, no Perplexity — uses web_search on 3 items)."""
    from agents.tools import execute_tool

    print("\n=== Test: foreach with tool='web_search' (small inline batch) ===\n")

    # Use a real org_id from the running system
    # You can find yours with: python3 dbq.py "SELECT id, name FROM organizations LIMIT 1"
    org_id: str = "dbe0b687-6967-4874-a26d-10f6289ae350"
    user_id: str | None = None

    # foreach with tool mode — blocks until completion (no separate monitor_operation needed)
    result = await execute_tool(
        tool_name="foreach",
        tool_input={
            "tool": "web_search",
            "items": [
                {"name": "Satya Nadella", "company": "Microsoft"},
                {"name": "Jensen Huang", "company": "NVIDIA"},
                {"name": "Tim Cook", "company": "Apple"},
            ],
            "params_template": {
                "query": "What is {{name}}'s current job title at {{company}}? Reply in one sentence.",
            },
            "rate_limit_per_minute": 30,
            "operation_name": "Test enrichment (3 CEOs)",
        },
        organization_id=org_id,
        user_id=user_id,
    )

    print(f"Result: {json.dumps(result, indent=2)}")

    if "error" in result:
        print(f"\nERROR: {result['error']}")
        return

    operation_id: str = result.get("operation_id", "")
    if not operation_id:
        print("\nNo operation_id in result — foreach may have completed inline.")
        return

    # Fetch results via SQL
    print("\n=== Results ===\n")
    results = await execute_tool(
        tool_name="run_sql_query",
        tool_input={
            "query": f"SELECT item_data, result_data, success, error FROM bulk_operation_results WHERE bulk_operation_id = '{operation_id}' ORDER BY item_index",
        },
        organization_id=org_id,
        user_id=user_id,
    )

    for r in results.get("rows", []):
        item: dict = r.get("item_data", {}) if isinstance(r.get("item_data"), dict) else {}
        result_data: dict = r.get("result_data", {}) if isinstance(r.get("result_data"), dict) else {}
        success: bool = r.get("success", False)
        error: str | None = r.get("error")

        if success:
            answer: str = str(result_data.get("answer", ""))[:200]
            print(f"  OK {item.get('name')}: {answer}")
        else:
            print(f"  FAIL {item.get('name')}: ERROR — {error}")

    print("\nDone!")


if __name__ == "__main__":
    asyncio.run(test_small_batch())
