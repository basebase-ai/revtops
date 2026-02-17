#!/usr/bin/env python3
"""
Quick integration test for the execute_command sandbox tool (E2B).

Usage:
    cd backend
    source venv/bin/activate
    python3 scripts/test_sandbox.py

Requires:
    - .env loaded with DATABASE_URL, E2B_API_KEY
    - Database accessible (for sandbox_id persistence on conversations table)
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

_backend_dir: Path = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_backend_dir))

from dotenv import load_dotenv

_root: Path = _backend_dir.parent
load_dotenv(_backend_dir / ".env")
if not (_backend_dir / ".env").exists():
    load_dotenv(_root / ".env")


# --- Update these with real IDs from your local DB ---
# Find yours with: python3 scripts/dbq.py "SELECT id FROM organizations LIMIT 1"
ORG_ID: str = "dbe0b687-6967-4874-a26d-10f6289ae350"
# Find a conversation: python3 scripts/dbq.py "SELECT id FROM conversations LIMIT 1"
CONVERSATION_ID: str = "d444b82b-a200-4de5-9c64-0ffd121fa668"


async def main() -> None:
    from agents.tools import execute_tool

    if not CONVERSATION_ID:
        print("ERROR: Set CONVERSATION_ID in the script first.")
        print("  Find one with: python3 scripts/dbq.py \"SELECT id FROM conversations LIMIT 1\"")
        return

    context: dict[str, object] = {"conversation_id": CONVERSATION_ID}

    print("\n=== Test 1: Basic echo ===")
    r1 = await execute_tool(
        "execute_command",
        {"command": "echo 'Hello from sandbox!'"},
        organization_id=ORG_ID,
        user_id=None,
        context=context,
    )
    print(json.dumps(r1, indent=2, default=str))

    if "error" in r1:
        print(f"\nFailed: {r1['error']}")
        return

    print("\n=== Test 2: Install pandas + query DB ===")
    r2 = await execute_tool(
        "execute_command",
        {"command": "pip install -q pandas psycopg2-binary"},
        organization_id=ORG_ID,
        user_id=None,
        context=context,
    )
    print(f"exit_code={r2.get('exit_code')}")

    print("\n=== Test 3: Run Python with DB access ===")
    r3 = await execute_tool(
        "execute_command",
        {"command": """python3 -c "
from db import get_connection
conn = get_connection()
cur = conn.cursor()
cur.execute('SELECT count(*) FROM deals')
count = cur.fetchone()[0]
print(f'Found {count} deals')
conn.close()
" """},
        organization_id=ORG_ID,
        user_id=None,
        context=context,
    )
    print(json.dumps(r3, indent=2, default=str))

    print("\n=== Test 4: Sandbox persists state (same sandbox) ===")
    r4 = await execute_tool(
        "execute_command",
        {"command": "echo 'still here' > /home/user/output/test.txt && ls -la /home/user/output/"},
        organization_id=ORG_ID,
        user_id=None,
        context=context,
    )
    print(json.dumps(r4, indent=2, default=str))

    print("\nAll tests passed!")


if __name__ == "__main__":
    asyncio.run(main())
