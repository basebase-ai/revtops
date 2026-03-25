from __future__ import annotations

import asyncio
from typing import Any

import pytest

from services import celery_health


async def _drain_scheduled_tasks() -> None:
    await asyncio.sleep(0)


@pytest.fixture(autouse=True)
def _non_development_for_celery_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests exercise the ping path; development skips checks by default."""
    monkeypatch.setenv("ENVIRONMENT", "production")


def test_celery_startup_check_skipped_in_development(monkeypatch: Any) -> None:
    monkeypatch.setenv("ENVIRONMENT", "development")
    called = False

    async def _fake_inspect() -> dict[str, Any] | None:
        nonlocal called
        called = True
        return None

    monkeypatch.setattr(celery_health, "_inspect_celery_workers", _fake_inspect)

    ok = asyncio.run(celery_health.ensure_celery_workers_available())
    assert ok is True
    assert called is False


def test_celery_startup_check_forced_in_development(monkeypatch: Any) -> None:
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("CELERY_STARTUP_CHECK", "true")

    async def _fake_inspect() -> dict[str, Any] | None:
        return {"worker@a": {"ok": "pong"}}

    monkeypatch.setattr(celery_health, "_inspect_celery_workers", _fake_inspect)
    monkeypatch.setattr(
        celery_health,
        "create_pagerduty_incident_with_details",
        lambda **_: (_ for _ in ()).throw(AssertionError("no incident")),
    )

    ok = asyncio.run(celery_health.ensure_celery_workers_available())
    assert ok is True


def test_ensure_celery_workers_available_success(monkeypatch: Any) -> None:
    async def _fake_inspect() -> dict[str, Any] | None:
        return {"worker@a": {"ok": "pong"}}

    async def _fake_incident(*, title: str, details: str) -> celery_health.PagerDutyIncidentResult:
        raise AssertionError("incident should not be called")

    monkeypatch.setattr(celery_health, "_inspect_celery_workers", _fake_inspect)
    monkeypatch.setattr(celery_health, "create_pagerduty_incident_with_details", _fake_incident)

    ok = asyncio.run(celery_health.ensure_celery_workers_available())
    assert ok is True


def test_ensure_celery_workers_available_retries_before_success(monkeypatch: Any) -> None:
    monkeypatch.setenv("CELERY_STARTUP_PING_ATTEMPTS", "3")
    monkeypatch.setenv("CELERY_STARTUP_RETRY_DELAY_SECONDS", "0")

    attempts = 0

    async def _fake_inspect() -> dict[str, Any] | None:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return None
        return {"worker@a": {"ok": "pong"}}

    async def _fake_incident(*, title: str, details: str) -> celery_health.PagerDutyIncidentResult:
        raise AssertionError("incident should not be called")

    monkeypatch.setattr(celery_health, "_inspect_celery_workers", _fake_inspect)
    monkeypatch.setattr(celery_health, "create_pagerduty_incident_with_details", _fake_incident)

    ok = asyncio.run(celery_health.ensure_celery_workers_available())
    assert ok is True
    assert attempts == 3


def test_ensure_celery_workers_available_incidents_on_no_workers(monkeypatch: Any) -> None:
    monkeypatch.setenv("CELERY_STARTUP_PING_ATTEMPTS", "3")
    monkeypatch.setenv("CELERY_STARTUP_RETRY_DELAY_SECONDS", "0")

    attempts = 0

    async def _fake_inspect() -> dict[str, Any] | None:
        nonlocal attempts
        attempts += 1
        return None

    incident_titles: list[str] = []

    async def _fake_incident(*, title: str, details: str) -> celery_health.PagerDutyIncidentResult:
        incident_titles.append(title)
        return celery_health.PagerDutyIncidentResult(ok=True, reason="created", status_code=201)

    monkeypatch.setattr(celery_health, "_inspect_celery_workers", _fake_inspect)
    monkeypatch.setattr(celery_health, "create_pagerduty_incident_with_details", _fake_incident)

    async def _run() -> bool:
        ok = await celery_health.ensure_celery_workers_available()
        await _drain_scheduled_tasks()
        return ok

    ok = asyncio.run(_run())
    assert ok is False
    assert attempts == 3
    assert incident_titles == ["Celery workers unavailable at startup"]


def test_ensure_celery_workers_available_incidents_on_check_error(monkeypatch: Any) -> None:
    monkeypatch.setenv("CELERY_STARTUP_PING_ATTEMPTS", "2")
    monkeypatch.setenv("CELERY_STARTUP_RETRY_DELAY_SECONDS", "0")

    attempts = 0

    async def _fake_inspect() -> dict[str, Any] | None:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("broker unreachable")

    incident_titles: list[str] = []

    async def _fake_incident(*, title: str, details: str) -> celery_health.PagerDutyIncidentResult:
        incident_titles.append(title)
        return celery_health.PagerDutyIncidentResult(ok=False, reason="http_error", status_code=500)

    monkeypatch.setattr(celery_health, "_inspect_celery_workers", _fake_inspect)
    monkeypatch.setattr(celery_health, "create_pagerduty_incident_with_details", _fake_incident)

    async def _run() -> bool:
        ok = await celery_health.ensure_celery_workers_available()
        await _drain_scheduled_tasks()
        return ok

    ok = asyncio.run(_run())
    assert ok is False
    assert attempts == 2
    assert incident_titles == ["Celery startup check failed"]
