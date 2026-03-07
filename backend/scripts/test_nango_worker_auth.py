#!/usr/bin/env python3
"""Worker-side Nango auth diagnostic for connector token failures.

This script is intended to be run in the same environment as your Celery worker
so it validates the exact runtime env vars and API permissions used by workers.

Examples:
  # Explicit connection id (fastest)
  python3 scripts/test_nango_worker_auth.py \
    --provider slack \
    --connection-id 5f5eae3e-d312-4ccd-a057-2e93726edda5

  # Resolve connection id from integrations table
  python3 scripts/test_nango_worker_auth.py \
    --provider slack \
    --organization-id <org_uuid> \
    --user-id <user_uuid>

  # Override integration id if your Nango provider_config_key differs
  python3 scripts/test_nango_worker_auth.py \
    --provider slack \
    --integration-id slack-prod \
    --connection-id <conn_id>
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import desc, select

# Make backend imports work whether launched from repo root or backend/
_BACKEND_DIR: Path = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_DIR))

_ROOT_DIR: Path = _BACKEND_DIR.parent
load_dotenv(_BACKEND_DIR / ".env")
if not (_BACKEND_DIR / ".env").exists():
    load_dotenv(_ROOT_DIR / ".env")

from config import get_nango_integration_id, settings
from models.database import dispose_engine, get_session
from models.integration import Integration
from services.nango import NangoClient


def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )


def _mask(value: str | None, keep: int = 6) -> str:
    if not value:
        return "<missing>"
    if len(value) <= keep * 2:
        return "*" * len(value)
    return f"{value[:keep]}...{value[-keep:]}"


async def _resolve_connection_id_from_db(
    provider: str,
    organization_id: str,
    user_id: str | None,
) -> Integration | None:
    async with get_session(organization_id=organization_id, user_id=user_id) as session:
        stmt = select(Integration).where(
            Integration.organization_id == organization_id,
            Integration.provider == provider,
        )
        if user_id:
            stmt = stmt.where(Integration.user_id == user_id)

        stmt = stmt.order_by(desc(Integration.is_active), desc(Integration.updated_at)).limit(1)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()


async def run(args: argparse.Namespace) -> int:
    logger = logging.getLogger("test_nango_worker_auth")

    if not settings.NANGO_SECRET_KEY:
        logger.error("NANGO_SECRET_KEY is missing in worker environment")
        return 2

    integration_id = args.integration_id or get_nango_integration_id(args.provider)

    logger.info("Nango diagnostics starting")
    logger.info("provider=%s", args.provider)
    logger.info("integration_id(provider_config_key)=%s", integration_id)
    logger.info("NANGO_HOST=%s", settings.NANGO_HOST)
    logger.info("NANGO_SECRET_KEY(masked)=%s", _mask(settings.NANGO_SECRET_KEY))

    connection_id = args.connection_id
    if not connection_id:
        if not args.organization_id:
            logger.error(
                "No --connection-id provided. Pass --organization-id (and optionally --user-id) to resolve from DB."
            )
            return 2

        integration = await _resolve_connection_id_from_db(
            provider=args.provider,
            organization_id=args.organization_id,
            user_id=args.user_id,
        )
        if not integration:
            logger.error(
                "No integration row found for provider=%s org=%s user=%s",
                args.provider,
                args.organization_id,
                args.user_id,
            )
            return 2

        connection_id = integration.nango_connection_id
        logger.info(
            "Resolved integration row id=%s is_active=%s nango_connection_id=%s",
            integration.id,
            integration.is_active,
            connection_id,
        )

    if not connection_id:
        logger.error("Resolved integration has empty nango_connection_id")
        return 2

    nango = NangoClient(secret_key=settings.NANGO_SECRET_KEY, public_key=settings.NANGO_PUBLIC_KEY)

    # 1) Raw connection fetch (captures precise HTTP code/body)
    logger.info("Step 1/3: GET /connection/{id} with provider_config_key")
    response = await nango._fetch_connection(integration_id, connection_id)
    logger.info("HTTP status=%s", response.status_code)
    preview = response.text.strip()[:1000]
    logger.info("response_body_preview=%s", preview if preview else "<empty>")

    # 2) Attempt high-level get_connection
    logger.info("Step 2/3: NangoClient.get_connection()")
    try:
        connection = await nango.get_connection(integration_id, connection_id)
        logger.info("get_connection() succeeded; keys=%s", sorted(connection.keys()))
    except Exception:
        logger.exception("get_connection() failed")
        if response.status_code == 401:
            logger.error(
                "Detected 401 from Nango. Likely causes: bad/expired NANGO_SECRET_KEY, wrong NANGO_HOST, or key from different workspace/environment."
            )
        return 1

    # 3) Attempt token extraction via get_token (same path connectors use)
    logger.info("Step 3/3: NangoClient.get_token()")
    try:
        token = await nango.get_token(integration_id, connection_id)
        logger.info("get_token() succeeded; token_prefix=%s token_len=%d", _mask(token, keep=8), len(token))
    except Exception:
        logger.exception("get_token() failed")
        return 1

    if args.list_connections_end_user_id:
        logger.info("Optional: list_connections(end_user_id=%s)", args.list_connections_end_user_id)
        try:
            connections = await nango.list_connections(end_user_id=args.list_connections_end_user_id)
            compact: list[dict[str, Any]] = [
                {
                    "id": c.get("id"),
                    "connection_id": c.get("connection_id"),
                    "provider_config_key": c.get("provider_config_key"),
                    "provider": c.get("provider"),
                }
                for c in connections
            ]
            logger.info("list_connections count=%d", len(compact))
            print(json.dumps(compact[:25], indent=2))
        except Exception:
            logger.exception("list_connections() failed")

    logger.info("All checks passed ✅")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Diagnose worker-side Nango auth failures")
    parser.add_argument("--provider", default="slack", help="Connector provider (default: slack)")
    parser.add_argument(
        "--integration-id",
        help="Override Nango provider_config_key (defaults to config mapping for --provider)",
    )
    parser.add_argument("--connection-id", help="Nango connection id to test directly")
    parser.add_argument("--organization-id", help="Org id used to resolve integration row when --connection-id is omitted")
    parser.add_argument("--user-id", help="Optional user id filter while resolving integration row")
    parser.add_argument(
        "--list-connections-end-user-id",
        help="Optional: also call list_connections for this end_user_id",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    _configure_logging(verbose=args.verbose)

    # Defensive for Celery worker-like processes that may have loop-bound pooled DB conns.
    dispose_engine()

    exit_code = asyncio.run(run(args))
    raise SystemExit(exit_code)
