"""Tests for the agent action ledger (Phase 1).

Covers:
- ActionLedgerEntry model (to_dict, derived status)
- Entity extraction heuristics
- Service layer failure isolation (record_intent, record_outcome)
- API route registration and auth enforcement
- Chokepoint wiring (ledger calls in _write_on_connector / _run_on_connector)
"""
import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models.action_ledger import ActionLedgerEntry
from services.action_ledger import _extract_entity, record_intent, record_outcome


# ---------------------------------------------------------------------------
# Model: to_dict and derived status
# ---------------------------------------------------------------------------


class TestActionLedgerEntryModel:
    """Test ActionLedgerEntry serialization and status derivation."""

    def _make_entry(self, **overrides: Any) -> ActionLedgerEntry:
        defaults: dict[str, Any] = {
            "id": uuid.uuid4(),
            "organization_id": uuid.uuid4(),
            "connector": "hubspot",
            "dispatch_type": "write",
            "operation": "update_deal",
            "intent": {"changes": {"dealname": "New"}},
            "reversible": False,
            "created_at": datetime(2026, 3, 29, 12, 0, 0),
        }
        defaults.update(overrides)
        return ActionLedgerEntry(**defaults)

    def test_derived_status_in_flight_when_outcome_is_null(self) -> None:
        entry = self._make_entry(outcome=None)
        assert entry._derived_status == "in-flight"

    def test_derived_status_success(self) -> None:
        entry = self._make_entry(outcome={"status": "success", "response": {}})
        assert entry._derived_status == "success"

    def test_derived_status_error(self) -> None:
        entry = self._make_entry(outcome={"status": "error", "error": "boom"})
        assert entry._derived_status == "error"

    def test_derived_status_unknown_when_outcome_missing_status_key(self) -> None:
        entry = self._make_entry(outcome={"unexpected": True})
        assert entry._derived_status == "unknown"

    def test_to_dict_includes_all_fields(self) -> None:
        entry_id = uuid.uuid4()
        org_id = uuid.uuid4()
        entry = self._make_entry(
            id=entry_id,
            organization_id=org_id,
            connector="google_drive",
            dispatch_type="action",
            operation="insert_text",
            entity_type="file",
            entity_id="abc123",
            outcome={"status": "success"},
            executed_at=datetime(2026, 3, 29, 12, 0, 1),
        )
        d = entry.to_dict()
        assert d["id"] == str(entry_id)
        assert d["organization_id"] == str(org_id)
        assert d["connector"] == "google_drive"
        assert d["dispatch_type"] == "action"
        assert d["operation"] == "insert_text"
        assert d["entity_type"] == "file"
        assert d["entity_id"] == "abc123"
        assert d["status"] == "success"
        assert d["executed_at"] is not None

    def test_to_dict_nullable_fields_serialize_as_none(self) -> None:
        entry = self._make_entry()
        d = entry.to_dict()
        assert d["user_id"] is None
        assert d["conversation_id"] is None
        assert d["workflow_id"] is None
        assert d["reversed_at"] is None
        assert d["reversed_by"] is None
        assert d["executed_at"] is None


# ---------------------------------------------------------------------------
# Service: entity extraction heuristics
# ---------------------------------------------------------------------------


class TestEntityExtraction:
    """Test _extract_entity helper for various operations."""

    def test_update_deal_extracts_deal_id(self) -> None:
        etype, eid = _extract_entity("update_deal", {"deal_id": "123"})
        assert etype == "deal"
        assert eid == "123"

    def test_update_deal_falls_back_to_id(self) -> None:
        etype, eid = _extract_entity("update_deal", {"id": "456"})
        assert etype == "deal"
        assert eid == "456"

    def test_create_deal_has_no_entity_id(self) -> None:
        etype, eid = _extract_entity("create_deal", {"dealname": "New"})
        assert etype == "deal"
        assert eid is None

    def test_update_contact_extracts_contact_id(self) -> None:
        etype, eid = _extract_entity("update_contact", {"contact_id": "789"})
        assert etype == "contact"
        assert eid == "789"

    def test_insert_text_extracts_file_id(self) -> None:
        etype, eid = _extract_entity("insert_text", {"external_id": "gdrive-abc"})
        assert etype == "file"
        assert eid == "gdrive-abc"

    def test_unknown_operation_returns_none(self) -> None:
        etype, eid = _extract_entity("do_magic", {"stuff": 1})
        assert etype is None
        assert eid is None

    def test_update_company_extracts_company_id(self) -> None:
        etype, eid = _extract_entity("update_company", {"company_id": "C1"})
        assert etype == "company"
        assert eid == "C1"

    def test_create_file_has_no_entity_id(self) -> None:
        etype, eid = _extract_entity("create_file", {"title": "doc.txt"})
        assert etype == "file"
        assert eid is None

    def test_append_rows_extracts_external_id(self) -> None:
        etype, eid = _extract_entity("append_rows", {"external_id": "sheet-1"})
        assert etype == "sheet"
        assert eid == "sheet-1"


# ---------------------------------------------------------------------------
# Service: failure isolation
# ---------------------------------------------------------------------------


class TestRecordIntentFailureIsolation:
    """record_intent must never raise — always returns None on failure."""

    def test_returns_none_on_db_error(self) -> None:
        @asynccontextmanager
        async def _fake_session(*_a: object, **_kw: object):
            mock = MagicMock()
            mock.add = MagicMock()
            mock.commit = AsyncMock(side_effect=RuntimeError("DB down"))
            yield mock

        with patch("services.action_ledger.get_session", _fake_session):
            result = asyncio.run(record_intent(
                organization_id="00000000-0000-0000-0000-000000000001",
                user_id=None,
                context={},
                connector="hubspot",
                dispatch_type="write",
                operation="update_deal",
                data={"deal_id": "1", "dealname": "X"},
            ))
        assert result is None

    def test_returns_uuid_on_success(self) -> None:
        @asynccontextmanager
        async def _fake_session(*_a: object, **_kw: object):
            mock = MagicMock()
            mock.add = MagicMock()
            mock.commit = AsyncMock()
            yield mock

        with patch("services.action_ledger.get_session", _fake_session):
            result = asyncio.run(record_intent(
                organization_id="00000000-0000-0000-0000-000000000001",
                user_id=None,
                context={"conversation_id": "00000000-0000-0000-0000-000000000002"},
                connector="hubspot",
                dispatch_type="write",
                operation="update_deal",
                data={"deal_id": "1"},
            ))
        assert isinstance(result, uuid.UUID)

    def test_captures_before_state_when_available(self) -> None:
        added_entries: list[ActionLedgerEntry] = []

        @asynccontextmanager
        async def _fake_session(*_a: object, **_kw: object):
            mock = MagicMock()
            mock.add = MagicMock(side_effect=lambda e: added_entries.append(e))
            mock.commit = AsyncMock()
            yield mock

        connector_instance = MagicMock()
        connector_instance.capture_before_state = AsyncMock(
            return_value={"properties": {"dealname": "Old"}}
        )

        with patch("services.action_ledger.get_session", _fake_session):
            asyncio.run(record_intent(
                organization_id="00000000-0000-0000-0000-000000000001",
                user_id=None,
                context={},
                connector="hubspot",
                dispatch_type="write",
                operation="update_deal",
                data={"deal_id": "1", "dealname": "New"},
                connector_instance=connector_instance,
            ))

        assert len(added_entries) == 1
        assert added_entries[0].intent["before_state"] == {"properties": {"dealname": "Old"}}

    def test_before_state_failure_does_not_block(self) -> None:
        @asynccontextmanager
        async def _fake_session(*_a: object, **_kw: object):
            mock = MagicMock()
            mock.add = MagicMock()
            mock.commit = AsyncMock()
            yield mock

        connector_instance = MagicMock()
        connector_instance.capture_before_state = AsyncMock(
            side_effect=RuntimeError("API timeout")
        )

        with patch("services.action_ledger.get_session", _fake_session):
            result = asyncio.run(record_intent(
                organization_id="00000000-0000-0000-0000-000000000001",
                user_id=None,
                context={},
                connector="hubspot",
                dispatch_type="write",
                operation="update_deal",
                data={"deal_id": "1"},
                connector_instance=connector_instance,
            ))
        # Should still succeed — before_state is best-effort
        assert isinstance(result, uuid.UUID)


class TestRecordOutcomeFailureIsolation:
    """record_outcome must never raise."""

    def test_noop_when_change_id_is_none(self) -> None:
        # Should not even open a session
        asyncio.run(record_outcome(None, "fake-org", {"status": "success"}))

    def test_returns_none_on_db_error(self) -> None:
        @asynccontextmanager
        async def _fake_session(*_a: object, **_kw: object):
            mock = MagicMock()
            mock.get = AsyncMock(side_effect=RuntimeError("DB down"))
            yield mock

        with patch("services.action_ledger.get_session", _fake_session):
            # Should not raise
            asyncio.run(record_outcome(
                uuid.uuid4(), "00000000-0000-0000-0000-000000000001",
                {"error": "something broke"},
            ))

    def test_sets_outcome_and_executed_at(self) -> None:
        mock_entry = MagicMock()
        mock_entry.outcome = None
        mock_entry.executed_at = None

        @asynccontextmanager
        async def _fake_session(*_a: object, **_kw: object):
            mock = MagicMock()
            mock.get = AsyncMock(return_value=mock_entry)
            mock.commit = AsyncMock()
            yield mock

        change_id = uuid.uuid4()
        with patch("services.action_ledger.get_session", _fake_session):
            asyncio.run(record_outcome(
                change_id, "00000000-0000-0000-0000-000000000001",
                {"id": "123", "properties": {"dealname": "Done"}},
            ))

        assert mock_entry.outcome == {
            "status": "success",
            "response": {"id": "123", "properties": {"dealname": "Done"}},
        }
        assert mock_entry.executed_at is not None

    def test_error_result_sets_error_status(self) -> None:
        mock_entry = MagicMock()
        mock_entry.outcome = None
        mock_entry.executed_at = None

        @asynccontextmanager
        async def _fake_session(*_a: object, **_kw: object):
            mock = MagicMock()
            mock.get = AsyncMock(return_value=mock_entry)
            mock.commit = AsyncMock()
            yield mock

        with patch("services.action_ledger.get_session", _fake_session):
            asyncio.run(record_outcome(
                uuid.uuid4(), "00000000-0000-0000-0000-000000000001",
                {"error": "Connection refused"},
            ))

        assert mock_entry.outcome["status"] == "error"
        assert mock_entry.outcome["error"] == "Connection refused"


# ---------------------------------------------------------------------------
# API: route registration and auth enforcement
# ---------------------------------------------------------------------------


class TestActionLedgerAPI:
    """Test API route is registered and requires auth."""

    def test_route_requires_auth(self) -> None:
        from fastapi.testclient import TestClient
        from api.main import app

        client = TestClient(app)
        response = client.get("/api/action-ledger/00000000-0000-0000-0000-000000000001")
        assert response.status_code == 401

    def test_openapi_includes_action_ledger_route(self) -> None:
        from fastapi.testclient import TestClient
        from api.main import app

        client = TestClient(app)
        response = client.get("/openapi.json")
        assert response.status_code == 200
        paths = response.json()["paths"]
        matching = [p for p in paths if "action-ledger" in p]
        assert len(matching) > 0, "action-ledger route not found in OpenAPI schema"

    def test_non_admin_listing_is_scoped_to_requesting_user(self) -> None:
        from api.auth_middleware import AuthContext
        from api.routes import action_ledger as route

        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        captured_queries: list[Any] = []

        class _ExecResult:
            def scalar_one(self) -> int:
                return 0

            def scalars(self) -> "_ExecResult":
                return self

            def all(self) -> list[Any]:
                return []

        @asynccontextmanager
        async def _fake_session(*_a: object, **_kw: object):
            mock = MagicMock()

            async def _execute(query: Any) -> _ExecResult:
                captured_queries.append(query)
                return _ExecResult()

            mock.execute = AsyncMock(side_effect=_execute)
            yield mock

        async def _run() -> None:
            with patch.object(route, "_is_org_admin", new=AsyncMock(return_value=False)), patch.object(
                route,
                "get_session",
                _fake_session,
            ):
                response = await route.list_action_ledger(
                    org_id=str(org_id),
                    auth=AuthContext(
                        user_id=user_id,
                        organization_id=org_id,
                        email="user@example.com",
                        role="member",
                        is_global_admin=False,
                    ),
                    conversation_id=None,
                    connector=None,
                    entity_type=None,
                    entity_id=None,
                    limit=50,
                    offset=0,
                )
                assert response.total == 0

        asyncio.run(_run())
        rendered_queries = " ".join(str(q) for q in captured_queries)
        assert "action_ledger.user_id = :user_id_1" in rendered_queries

    def test_org_admin_listing_is_not_scoped_to_user_id(self) -> None:
        from api.auth_middleware import AuthContext
        from api.routes import action_ledger as route

        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        captured_queries: list[Any] = []

        class _ExecResult:
            def scalar_one(self) -> int:
                return 0

            def scalars(self) -> "_ExecResult":
                return self

            def all(self) -> list[Any]:
                return []

        @asynccontextmanager
        async def _fake_session(*_a: object, **_kw: object):
            mock = MagicMock()

            async def _execute(query: Any) -> _ExecResult:
                captured_queries.append(query)
                return _ExecResult()

            mock.execute = AsyncMock(side_effect=_execute)
            yield mock

        async def _run() -> None:
            with patch.object(route, "_is_org_admin", new=AsyncMock(return_value=True)), patch.object(
                route,
                "get_session",
                _fake_session,
            ):
                response = await route.list_action_ledger(
                    org_id=str(org_id),
                    auth=AuthContext(
                        user_id=user_id,
                        organization_id=org_id,
                        email="admin@example.com",
                        role="admin",
                        is_global_admin=False,
                    ),
                    conversation_id=None,
                    connector=None,
                    entity_type=None,
                    entity_id=None,
                    limit=50,
                    offset=0,
                )
                assert response.total == 0

        asyncio.run(_run())
        rendered_queries = " ".join(str(q) for q in captured_queries)
        assert "action_ledger.user_id = :user_id_1" not in rendered_queries


# ---------------------------------------------------------------------------
# Connector: capture_before_state default
# ---------------------------------------------------------------------------


class TestBaseConnectorBeforeState:
    """Test that BaseConnector.capture_before_state returns None by default."""

    def test_default_returns_none(self) -> None:
        from connectors.base import BaseConnector

        # BaseConnector is abstract, so mock the abstract methods
        class _Stub(BaseConnector):
            source_system = "test"
            async def sync_deals(self) -> int: return 0
            async def sync_accounts(self) -> int: return 0
            async def sync_contacts(self) -> int: return 0
            async def sync_activities(self) -> int: return 0
            async def fetch_deal(self, deal_id: str) -> dict: return {}

        stub = _Stub(organization_id="fake-org")
        result = asyncio.run(stub.capture_before_state("update_deal", {"deal_id": "1"}))
        assert result is None


# ---------------------------------------------------------------------------
# Chokepoint wiring: _write_on_connector and _run_on_connector
# ---------------------------------------------------------------------------


class TestChokepoints:
    """Verify ledger calls are wired into the connector dispatch functions."""

    def test_write_on_connector_calls_record_intent_and_outcome(self) -> None:
        """_write_on_connector should call record_intent before and record_outcome after."""
        from agents import tools

        fake_instance = MagicMock()
        fake_instance.user_id = "user-1"
        fake_instance.write = AsyncMock(return_value={"id": "99", "status": "ok"})

        change_id = uuid.uuid4()

        with patch.object(tools, "_get_connector_instance", new=AsyncMock(return_value=(fake_instance, None))), \
             patch.object(tools, "check_connector_call", new=AsyncMock(return_value=MagicMock(allowed=True))), \
             patch("services.action_ledger.record_intent", new=AsyncMock(return_value=change_id)) as mock_intent, \
             patch("services.action_ledger.record_outcome", new=AsyncMock()) as mock_outcome:

            result = asyncio.run(tools._write_on_connector(
                params={"connector": "hubspot", "operation": "update_deal", "data": {"deal_id": "1"}},
                organization_id="org-1",
                user_id="user-1",
                skip_approval=True,
                context={"conversation_id": "conv-1"},
            ))

        assert result == {"id": "99", "status": "ok"}
        mock_intent.assert_called_once()
        mock_outcome.assert_called_once()
        # Outcome should have the change_id and result
        outcome_args = mock_outcome.call_args
        assert outcome_args[0][0] == change_id
        assert outcome_args[0][2] == {"id": "99", "status": "ok"}

    def test_write_on_connector_adds_warning_for_cross_user_non_slack(self) -> None:
        """Cross-user non-Slack/Teams connector use should add a user-facing warning."""
        from agents import tools

        fake_instance = MagicMock()
        fake_instance.user_id = "teammate-1"
        fake_instance.write = AsyncMock(return_value={"id": "99", "status": "ok"})

        with patch.object(tools, "_get_connector_instance", new=AsyncMock(return_value=(fake_instance, None))), \
             patch.object(tools, "check_connector_call", new=AsyncMock(return_value=MagicMock(allowed=True))), \
             patch("services.action_ledger.record_intent", new=AsyncMock(return_value=uuid.uuid4())), \
             patch("services.action_ledger.record_outcome", new=AsyncMock()):

            result = asyncio.run(tools._write_on_connector(
                params={"connector": "hubspot", "operation": "update_deal", "data": {"deal_id": "1"}},
                organization_id="org-1",
                user_id="user-1",
                skip_approval=True,
                context={"conversation_id": "conv-1"},
            ))

        assert result.get("id") == "99"
        assert "warning" in result
        assert "HubSpot" in str(result["warning"])
        assert "connect to HubSpot as yourself" in str(result["warning"])

    def test_write_on_connector_records_error_on_exception(self) -> None:
        """When instance.write raises, record_outcome should still be called with error."""
        from agents import tools

        fake_instance = MagicMock()
        fake_instance.write = AsyncMock(side_effect=RuntimeError("API down"))

        with patch.object(tools, "_get_connector_instance", new=AsyncMock(return_value=(fake_instance, None))), \
             patch.object(tools, "check_connector_call", new=AsyncMock(return_value=MagicMock(allowed=True))), \
             patch("services.action_ledger.record_intent", new=AsyncMock(return_value=uuid.uuid4())) as mock_intent, \
             patch("services.action_ledger.record_outcome", new=AsyncMock()) as mock_outcome:

            result = asyncio.run(tools._write_on_connector(
                params={"connector": "hubspot", "operation": "update_deal", "data": {}},
                organization_id="org-1",
                user_id="user-1",
                skip_approval=True,
                context=None,
            ))

        assert "error" in result
        mock_intent.assert_called_once()
        mock_outcome.assert_called_once()
        outcome_result = mock_outcome.call_args[0][2]
        assert "error" in outcome_result

    def test_run_on_connector_calls_record_intent_and_outcome(self) -> None:
        """_run_on_connector should call record_intent before and record_outcome after."""
        from agents import tools

        fake_instance = MagicMock()
        fake_instance.execute_action = AsyncMock(return_value={"sent": True})

        change_id = uuid.uuid4()

        with patch.object(tools, "_get_connector_instance", new=AsyncMock(return_value=(fake_instance, None))), \
             patch.object(tools, "check_connector_call", new=AsyncMock(return_value=MagicMock(allowed=True))), \
             patch("services.action_ledger.record_intent", new=AsyncMock(return_value=change_id)) as mock_intent, \
             patch("services.action_ledger.record_outcome", new=AsyncMock()) as mock_outcome:

            result = asyncio.run(tools._run_on_connector(
                params={"connector": "slack", "action": "send_message", "params": {"channel": "C1"}},
                organization_id="org-1",
                user_id="user-1",
                skip_approval=True,
                context=None,
            ))

        assert result == {"sent": True}
        mock_intent.assert_called_once()
        mock_outcome.assert_called_once()


# ---------------------------------------------------------------------------
# Migration: validate revision chain
# ---------------------------------------------------------------------------


class TestMigrationMetadata:
    """Verify migration 118 has correct revision pointers."""

    def _load_migration(self):
        import importlib
        return importlib.import_module("db.migrations.versions.118_create_action_ledger")

    def test_revision_chain(self) -> None:
        mig = self._load_migration()
        assert mig.revision == "118_create_action_ledger"
        assert mig.down_revision == "117_guest_org"

    def test_upgrade_function_exists(self) -> None:
        mig = self._load_migration()
        assert callable(mig.upgrade)
        assert callable(mig.downgrade)
