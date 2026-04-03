from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from api.auth_middleware import AuthContext
from api.routes import billing


class _Result:
    def __init__(self, *, scalar: Any = None, rows: list[tuple[Any, ...]] | None = None) -> None:
        self._scalar = scalar
        self._rows = rows or []

    def scalar_one_or_none(self) -> Any:
        return self._scalar

    def all(self) -> list[tuple[Any, ...]]:
        return self._rows


def test_credit_details_non_admin_scopes_queries_to_requesting_user() -> None:
    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    captured_queries: list[str] = []

    org = MagicMock()
    org.current_period_start = datetime(2026, 3, 1, tzinfo=timezone.utc)
    org.current_period_end = datetime(2026, 4, 1, tzinfo=timezone.utc)
    org.credits_included = 500

    @asynccontextmanager
    async def _fake_admin_session(*_a: object, **_kw: object):
        mock = MagicMock()

        async def _execute(query: Any) -> _Result:
            sql = str(query)
            captured_queries.append(sql)
            if "FROM organizations" in sql:
                return _Result(scalar=org)
            return _Result(rows=[])

        mock.execute = AsyncMock(side_effect=_execute)
        yield mock

    async def _run() -> None:
        with patch.object(billing, "_is_org_admin", new=AsyncMock(return_value=False)), patch.object(
            billing,
            "get_admin_session",
            _fake_admin_session,
        ):
            response = await billing.get_credit_details(
                auth=AuthContext(
                    user_id=user_id,
                    organization_id=org_id,
                    email="user@example.com",
                    role="member",
                    is_global_admin=False,
                )
            )

            assert response.transactions == []

    asyncio.run(_run())

    joined = "\n".join(captured_queries)
    assert "credit_transactions.user_id" in joined
