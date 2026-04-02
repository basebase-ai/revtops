#!/usr/bin/env python3
"""
Preview rows that migration 122 would DELETE (read-only).

Simulates the three UPDATE steps (conversation org from membership, message org
from conversation, message org from author membership), then applies the same
DELETE rules as 122_tighten_conversations_chat_rls.py.

Usage (from repo root or backend/):
  python3 backend/scripts/preview_migration_122_deletions.py
  python3 backend/scripts/preview_migration_122_deletions.py --json

Requires DATABASE_URL in .env (same as dbq.py).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

_env_path: Path = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(_env_path)

_raw_url: str = os.environ.get("DATABASE_URL", "")
DB_URL: str = _raw_url.replace("+asyncpg", "")

# Mirrors 122_tighten_conversations_chat_rls.py (membership statuses + DISTINCT ON order)
_SQL_SIMULATION: str = """
WITH first_org_per_user AS (
    SELECT DISTINCT ON (om.user_id)
        om.user_id,
        om.organization_id AS resolved_org_id
    FROM org_members om
    WHERE om.status IN ('active', 'onboarding', 'invited')
    ORDER BY om.user_id, om.joined_at NULLS LAST, om.created_at NULLS LAST
),
conv_step1 AS (
    SELECT
        c.id,
        c.user_id,
        c.title,
        c.source,
        c.type,
        c.participating_user_ids,
        c.created_at,
        COALESCE(c.organization_id, fou.resolved_org_id) AS org_after_step1
    FROM conversations c
    LEFT JOIN first_org_per_user fou ON fou.user_id = c.user_id
),
msg_step2 AS (
    SELECT
        m.id AS message_id,
        m.conversation_id,
        m.user_id AS msg_user_id,
        COALESCE(
            m.organization_id,
            CASE
                WHEN cs.org_after_step1 IS NOT NULL THEN cs.org_after_step1
                ELSE NULL
            END
        ) AS org_after_step2
    FROM chat_messages m
    LEFT JOIN conv_step1 cs ON cs.id = m.conversation_id
),
msg_step3 AS (
    SELECT
        ms.message_id,
        ms.conversation_id,
        ms.msg_user_id,
        COALESCE(ms.org_after_step2, fou.resolved_org_id) AS org_final
    FROM msg_step2 ms
    LEFT JOIN first_org_per_user fou ON fou.user_id = ms.msg_user_id
),
conv_will_delete AS (
    SELECT * FROM conv_step1 WHERE org_after_step1 IS NULL
),
msg_will_delete AS (
    SELECT DISTINCT m.message_id, m.conversation_id, m.org_final
    FROM msg_step3 m
    WHERE m.org_final IS NULL
       OR (
           m.conversation_id IS NOT NULL
           AND m.conversation_id IN (SELECT id FROM conv_will_delete)
       )
)
SELECT
    (SELECT COUNT(*)::bigint FROM conv_will_delete) AS conversations_to_delete,
    (SELECT COUNT(*)::bigint FROM msg_will_delete) AS messages_to_delete,
    (SELECT COUNT(*)::bigint FROM msg_will_delete mwd
     WHERE mwd.conversation_id IS NULL) AS orphan_messages_to_delete;
"""

_SQL_CONVERSATION_DETAIL: str = """
WITH first_org_per_user AS (
    SELECT DISTINCT ON (om.user_id)
        om.user_id,
        om.organization_id AS resolved_org_id
    FROM org_members om
    WHERE om.status IN ('active', 'onboarding', 'invited')
    ORDER BY om.user_id, om.joined_at NULLS LAST, om.created_at NULLS LAST
),
conv_step1 AS (
    SELECT
        c.id,
        c.user_id,
        c.title,
        c.source,
        c.type,
        c.participating_user_ids,
        c.created_at,
        COALESCE(c.organization_id, fou.resolved_org_id) AS org_after_step1
    FROM conversations c
    LEFT JOIN first_org_per_user fou ON fou.user_id = c.user_id
),
conv_will_delete AS (
    SELECT * FROM conv_step1 WHERE org_after_step1 IS NULL
)
SELECT
    cd.id::text AS conversation_id,
    cd.title,
    cd.source,
    cd.type,
    cd.user_id::text AS owner_user_id,
    u_owner.email AS owner_email,
    cd.participating_user_ids,
    cd.created_at,
    (
        SELECT COUNT(*)::bigint
        FROM chat_messages cm
        WHERE cm.conversation_id = cd.id
    ) AS message_count,
    (
        SELECT COALESCE(
            (
                SELECT json_agg(
                    json_build_object('user_id', u.id::text, 'email', u.email)
                    ORDER BY u.email NULLS LAST
                )
                FROM unnest(COALESCE(cd.participating_user_ids, ARRAY[]::uuid[])) AS pid(uid)
                LEFT JOIN users u ON u.id = pid.uid
            ),
            '[]'::json
        )
    ) AS participants
FROM conv_will_delete cd
LEFT JOIN users u_owner ON u_owner.id = cd.user_id
ORDER BY message_count DESC, cd.created_at DESC NULLS LAST;
"""


def _json_default(obj: object) -> str:
    if isinstance(obj, UUID):
        return str(obj)
    raise TypeError(type(obj))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON only",
    )
    args = parser.parse_args()

    if not DB_URL:
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        return 1

    conn: psycopg2.extensions.connection = psycopg2.connect(DB_URL)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(_SQL_SIMULATION)
            summary: dict[str, Any] = dict(cur.fetchone() or {})

            cur.execute(_SQL_CONVERSATION_DETAIL)
            rows: list[dict[str, Any]] = [dict(r) for r in cur.fetchall()]

        if args.json:
            out: dict[str, Any] = {
                "summary": {
                    "conversations_to_delete": int(summary["conversations_to_delete"]),
                    "messages_to_delete": int(summary["messages_to_delete"]),
                    "orphan_messages_to_delete": int(summary["orphan_messages_to_delete"]),
                },
                "conversations": rows,
            }
            print(json.dumps(out, default=_json_default, indent=2))
            return 0

        c_del: int = int(summary["conversations_to_delete"])
        m_del: int = int(summary["messages_to_delete"])
        o_del: int = int(summary["orphan_messages_to_delete"])

        print("=== Migration 122 deletion preview (simulated, read-only) ===\n")
        print(f"Conversations that would be DELETED:     {c_del}")
        print(f"Chat messages that would be DELETED:      {m_del}")
        print(f"  (of which orphan conversation_id NULL): {o_del}")
        print()

        if not rows:
            print("No conversations slated for deletion.")
            return 0

        print("Per conversation (sorted by message count, descending):\n")
        for i, r in enumerate(rows, start=1):
            cid: str = r["conversation_id"]
            title: str | None = r.get("title")
            src: str | None = r.get("source")
            typ: str | None = r.get("type")
            mc: int = int(r["message_count"])
            owner: str | None = r.get("owner_email") or r.get("owner_user_id")
            parts: Any = r.get("participants")
            if isinstance(parts, str):
                try:
                    parts = json.loads(parts)
                except json.JSONDecodeError:
                    pass
            created: Any = r.get("created_at")

            print(f"--- {i}. {cid} ---")
            print(f"    title:     {title!r}")
            print(f"    source:    {src!r}  type: {typ!r}")
            print(f"    created:   {created}")
            print(f"    messages:  {mc}")
            print(f"    owner:     {owner}")
            print(f"    participants ({len(parts) if isinstance(parts, list) else '?'}): {json.dumps(parts, default=str)}")
            print()

        print(
            "Note: Messages tied to these conversations are deleted even if a message-level "
            "org could be inferred from user_id (migration deletes by conversation_id IN unscoped convs)."
        )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
