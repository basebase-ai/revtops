#!/usr/bin/env python3
"""
Map a messenger workspace (Slack team, Teams tenant, Discord guild, …) to an org.

Creates or updates a messenger_bot_installs row so that incoming messages
from the workspace are routed to the correct Basebase organisation.

Usage:
    python scripts/map_messenger_workspace.py <platform> <workspace_id> <org_id>

Examples:
    python scripts/map_messenger_workspace.py teams 4efd831f-d924-4a05-a52a-28d45a6fa235 b8be36a3-...
    python scripts/map_messenger_workspace.py discord 123456789012345678 b8be36a3-...

Run from backend/ or project root.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from uuid import UUID

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

_env_path: Path = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(_env_path)

_raw_url: str = os.environ.get("DATABASE_URL", "")
DB_URL: str = _raw_url.replace("+asyncpg", "")

if not DB_URL:
    print("ERROR: DATABASE_URL not set", file=sys.stderr)
    sys.exit(1)

VALID_PLATFORMS: frozenset[str] = frozenset({"slack", "teams", "discord", "signal", "whatsapp", "sms"})


def map_workspace(platform: str, workspace_id: str, org_id: str) -> None:
    if platform not in VALID_PLATFORMS:
        print(f"ERROR: Unknown platform '{platform}'. Valid: {', '.join(sorted(VALID_PLATFORMS))}", file=sys.stderr)
        sys.exit(1)

    try:
        UUID(org_id)
    except ValueError:
        print(f"ERROR: '{org_id}' is not a valid UUID", file=sys.stderr)
        sys.exit(1)

    conn: psycopg2.extensions.connection = psycopg2.connect(DB_URL)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, name FROM organizations WHERE id = %s",
                (org_id,),
            )
            org = cur.fetchone()
            if not org:
                print(f"ERROR: No organisation found with id {org_id}", file=sys.stderr)
                sys.exit(1)

            cur.execute(
                """
                INSERT INTO messenger_bot_installs
                    (id, platform, workspace_id, organization_id,
                     access_token_encrypted, extra_data, created_at, updated_at)
                VALUES
                    (gen_random_uuid(), %s, %s, %s,
                     'not-applicable', '{}'::jsonb, now(), now())
                ON CONFLICT ON CONSTRAINT uq_messenger_bot_installs_platform_ws
                DO UPDATE SET organization_id = EXCLUDED.organization_id,
                              updated_at = now()
                RETURNING id
                """,
                (platform, workspace_id, org_id),
            )
            row = cur.fetchone()
            conn.commit()

        print(f"OK — mapped {platform} workspace {workspace_id} → org \"{org['name']}\" ({org_id})")
        if row:
            print(f"     install id: {row['id']}")
    finally:
        conn.close()


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print(__doc__.strip())
        sys.exit(0)

    map_workspace(
        platform=sys.argv[1].strip().lower(),
        workspace_id=sys.argv[2].strip(),
        org_id=sys.argv[3].strip(),
    )
