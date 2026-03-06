#!/usr/bin/env python3
"""
One-time backfill: transpile all existing apps that have frontend_code
but no frontend_code_compiled.

Usage:
    cd backend && python scripts/backfill_compiled_apps.py

Requires esbuild to be installed (esbuild binary on PATH).
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Ensure backend/ is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, func

from models.database import get_admin_session
from models.app import App
from utils.transpile_jsx import transpile_jsx


async def backfill() -> None:
    async with get_admin_session() as session:
        # Count apps needing backfill
        count_result = await session.execute(
            select(func.count(App.id)).where(
                App.frontend_code_compiled.is_(None),
                App.frontend_code.isnot(None),
            )
        )
        total = count_result.scalar_one()
        print(f"Found {total} apps to backfill")

        if total == 0:
            return

        result = await session.execute(
            select(App).where(
                App.frontend_code_compiled.is_(None),
                App.frontend_code.isnot(None),
            )
        )
        apps = result.scalars().all()

        success = 0
        failed = 0
        for app in apps:
            transpile_result = transpile_jsx(app.frontend_code)
            if transpile_result:
                app.frontend_code_compiled = transpile_result[0]
                success += 1
            else:
                failed += 1
            print(f"  [{success + failed}/{total}] {app.id} — {'OK' if transpile_result else 'SKIP'}")

        await session.commit()
        print(f"\nDone: {success} compiled, {failed} skipped")


if __name__ == "__main__":
    asyncio.run(backfill())
