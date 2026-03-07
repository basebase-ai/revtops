from __future__ import annotations

from typing import Any

from config import EXPECTED_ENV_VARS, settings
from services import pagerduty
from workers.tasks import monitoring


class _FakeResponse:
    def __init__(self, status_code: int = 201, text: str = "ok") -> None:
        self.status_code = status_code
        self.text = text

    def json(self) -> dict[str, Any]:
        return {}


class _FakeAsyncClient:
    last_call: dict[str, Any] | None = None

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    async def post(self, url: str, json: dict[str, Any], headers: dict[str, str]) -> _FakeResponse:
        _FakeAsyncClient.last_call = {
            "url": url,
            "json": json,
            "headers": headers,
        }
        return _FakeResponse()


def test_expected_env_vars_include_pagerduty() -> None:
    assert "PAGERDUTY_FROM_EMAIL" in EXPECTED_ENV_VARS
    assert "PagerDuty_Key" in EXPECTED_ENV_VARS
    assert "PAGERDUTY_SERVICE_ID" in EXPECTED_ENV_VARS


def test_pagerduty_incident_request_shape(monkeypatch: Any) -> None:
    monkeypatch.setattr(pagerduty.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setenv("PAGERDUTY_FROM_EMAIL", "alerts@revtops.com")
    monkeypatch.setenv("PagerDuty_Key", "pd_test_key")
    monkeypatch.setenv("PAGERDUTY_SERVICE_ID", "svc_123")
    monkeypatch.setattr(pagerduty, "settings", settings.__class__())

    import asyncio

    asyncio.run(
        monitoring._create_pagerduty_incident(
            check_result=monitoring.CheckResult(
                name="Redis",
                healthy=False,
                details="timeout",
            ),
        )
    )

    last_call = _FakeAsyncClient.last_call
    assert last_call["url"] == "https://api.pagerduty.com/incidents"
    assert last_call["headers"]["From"] == "alerts@revtops.com"
    assert last_call["headers"]["Authorization"] == "Token token=pd_test_key"
    assert last_call["json"]["incident"]["service"]["id"] == "svc_123"
    assert last_call["json"]["incident"]["title"] == "Redis is down"


def test_pagerduty_alias_var_is_loaded(monkeypatch: Any) -> None:
    monkeypatch.setenv("PagerDuty_Key", "alias_value")
    fresh_settings = settings.__class__()
    assert fresh_settings.PAGERDUTY_KEY == "alias_value"


def test_monitor_dependencies_logs_health_check_outcome(monkeypatch: Any, caplog: Any) -> None:
    async def _fake_run_dependency_checks() -> list[monitoring.CheckResult]:
        return [
            monitoring.CheckResult(name="Supabase", healthy=True, details="ok"),
            monitoring.CheckResult(name="Redis", healthy=False, details="timeout"),
        ]

    monkeypatch.setattr(monitoring, "_run_dependency_checks", _fake_run_dependency_checks)

    async def _fake_record_check_heartbeat() -> None:
        return None

    monkeypatch.setattr(monitoring, "_record_check_heartbeat", _fake_record_check_heartbeat)

    created_incidents: list[str] = []

    async def _fake_create_pagerduty_incident(**kwargs: Any) -> None:
        created_incidents.append(kwargs["check_result"].name)

    monkeypatch.setattr(monitoring, "_create_pagerduty_incident", _fake_create_pagerduty_incident)

    async def _fake_clear_incident_failure(check_name: str) -> None:
        return None

    async def _fake_evaluate_incident_creation(check_name: str) -> tuple[bool, str]:
        return True, "new_failure"

    monkeypatch.setattr(monitoring, "clear_incident_failure", _fake_clear_incident_failure)
    monkeypatch.setattr(monitoring, "evaluate_incident_creation", _fake_evaluate_incident_creation)
    monkeypatch.setenv("PAGERDUTY_FROM_EMAIL", "alerts@revtops.com")
    monkeypatch.setenv("PagerDuty_Key", "pd_test_key")
    monkeypatch.setenv("PAGERDUTY_SERVICE_ID", "svc_123")
    monkeypatch.setattr(pagerduty, "settings", settings.__class__())

    caplog.set_level("INFO")
    result = monitoring.monitor_dependencies.__wrapped__()

    assert result["down_services"] == ["Redis"]
    assert created_incidents == ["Redis"]
    assert "PagerDuty health check succeeded for Supabase; incident creation skipped" in caplog.text
    assert "PagerDuty health check failed for Redis; evaluating incident throttle" in caplog.text


def test_monitor_dependencies_creates_incident_when_checks_fail(monkeypatch: Any) -> None:
    async def _fake_run_dependency_checks() -> list[monitoring.CheckResult]:
        raise RuntimeError("boom")

    monkeypatch.setattr(monitoring, "_run_dependency_checks", _fake_run_dependency_checks)

    incident_titles: list[str] = []

    async def _fake_create_pagerduty_incident(*, title: str, details: str) -> bool:
        incident_titles.append(title)
        return True

    monkeypatch.setattr(monitoring, "create_pagerduty_incident", _fake_create_pagerduty_incident)

    result = monitoring.monitor_dependencies.__wrapped__()

    assert result["status"] == "failed"
    assert incident_titles == ["Dependency monitor failed to run"]


def test_monitoring_heartbeat_watchdog_incidents_on_stale_heartbeat(monkeypatch: Any) -> None:
    async def _fake_heartbeat_age_seconds() -> int | None:
        return (30 * 60) + 5

    incident_titles: list[str] = []

    async def _fake_create_pagerduty_incident(*, title: str, details: str) -> bool:
        incident_titles.append(title)
        return True

    monkeypatch.setattr(monitoring, "_heartbeat_age_seconds", _fake_heartbeat_age_seconds)
    monkeypatch.setattr(monitoring, "create_pagerduty_incident", _fake_create_pagerduty_incident)

    result = monitoring.monitoring_heartbeat_watchdog.__wrapped__()

    assert result["status"] == "stale"
    assert incident_titles == ["Dependency monitor heartbeat stale"]



def test_check_jwks_endpoint_unhealthy_when_supabase_url_missing(monkeypatch: Any) -> None:
    monkeypatch.setenv("SUPABASE_URL", "")
    monkeypatch.setattr(monitoring, "settings", settings.__class__())

    import asyncio

    result = asyncio.run(monitoring._check_jwks_endpoint())

    assert result.name == "Auth JWKS"
    assert result.healthy is False
    assert result.details == "SUPABASE_URL is not configured"


def test_api_healthcheck_url_uses_backend_public_url(monkeypatch: Any) -> None:
    monkeypatch.setenv("BACKEND_PUBLIC_URL", "https://revtops.example.com/")
    monkeypatch.setattr(monitoring, "settings", settings.__class__())

    assert monitoring._api_healthcheck_url() == "https://revtops.example.com/health"


def test_check_http_endpoint_marks_supabase_522_as_connection_pool_outage(monkeypatch: Any) -> None:
    class _FakeHttpClient:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

        async def __aenter__(self) -> "_FakeHttpClient":
            return self

        async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
            return None

        async def get(self, url: str) -> _FakeResponse:
            return _FakeResponse(status_code=522)

    monkeypatch.setattr(monitoring.httpx, "AsyncClient", _FakeHttpClient)

    import asyncio

    result = asyncio.run(monitoring._check_http_endpoint("Supabase", "https://example.supabase.co"))

    assert result.name == "Supabase"
    assert result.healthy is False
    assert result.details == "HTTP 522 from https://example.supabase.co (possible Supabase connection pool outage)"


def test_monitor_dependencies_raises_incident_for_supabase_522(monkeypatch: Any) -> None:
    async def _fake_run_dependency_checks() -> list[monitoring.CheckResult]:
        return [
            monitoring.CheckResult(
                name="Supabase",
                healthy=False,
                details="HTTP 522 from https://example.supabase.co (possible Supabase connection pool outage)",
            ),
        ]

    async def _fake_record_check_heartbeat() -> None:
        return None

    created_incidents: list[str] = []

    async def _fake_create_pagerduty_incident(**kwargs: Any) -> None:
        created_incidents.append(kwargs["check_result"].name)

    monkeypatch.setattr(monitoring, "_run_dependency_checks", _fake_run_dependency_checks)
    monkeypatch.setattr(monitoring, "_record_check_heartbeat", _fake_record_check_heartbeat)
    monkeypatch.setattr(monitoring, "_create_pagerduty_incident", _fake_create_pagerduty_incident)

    async def _fake_clear_incident_failure(check_name: str) -> None:
        return None

    async def _fake_evaluate_incident_creation(check_name: str) -> tuple[bool, str]:
        return True, "new_failure"

    monkeypatch.setattr(monitoring, "clear_incident_failure", _fake_clear_incident_failure)
    monkeypatch.setattr(monitoring, "evaluate_incident_creation", _fake_evaluate_incident_creation)

    result = monitoring.monitor_dependencies.__wrapped__()

    assert result["down_services"] == ["Supabase"]
    assert created_incidents == ["Supabase"]


def test_monitor_dependencies_suppresses_repeated_incident_for_same_failure(monkeypatch: Any) -> None:
    async def _fake_run_dependency_checks() -> list[monitoring.CheckResult]:
        return [
            monitoring.CheckResult(name="Redis", healthy=False, details="timeout"),
        ]

    async def _fake_record_check_heartbeat() -> None:
        return None

    created_incidents: list[str] = []

    async def _fake_create_pagerduty_incident(**kwargs: Any) -> None:
        created_incidents.append(kwargs["check_result"].name)

    async def _fake_clear_incident_failure(check_name: str) -> None:
        return None

    async def _fake_evaluate_incident_creation(check_name: str) -> tuple[bool, str]:
        return False, "suppressed_for_7200s"

    monkeypatch.setattr(monitoring, "_run_dependency_checks", _fake_run_dependency_checks)
    monkeypatch.setattr(monitoring, "_record_check_heartbeat", _fake_record_check_heartbeat)
    monkeypatch.setattr(monitoring, "_create_pagerduty_incident", _fake_create_pagerduty_incident)
    monkeypatch.setattr(monitoring, "clear_incident_failure", _fake_clear_incident_failure)
    monkeypatch.setattr(monitoring, "evaluate_incident_creation", _fake_evaluate_incident_creation)

    result = monitoring.monitor_dependencies.__wrapped__()

    assert result["down_services"] == ["Redis"]
    assert created_incidents == []


def test_monitor_dependencies_allows_incident_when_different_check_fails(monkeypatch: Any) -> None:
    async def _fake_run_dependency_checks() -> list[monitoring.CheckResult]:
        return [
            monitoring.CheckResult(name="Redis", healthy=False, details="timeout"),
            monitoring.CheckResult(name="Nango", healthy=False, details="HTTP 503"),
        ]

    async def _fake_record_check_heartbeat() -> None:
        return None

    created_incidents: list[str] = []

    async def _fake_create_pagerduty_incident(**kwargs: Any) -> None:
        created_incidents.append(kwargs["check_result"].name)

    async def _fake_clear_incident_failure(check_name: str) -> None:
        return None

    async def _fake_evaluate_incident_creation(check_name: str) -> tuple[bool, str]:
        if check_name == "Redis":
            return False, "suppressed_for_100s"
        return True, "new_failure"

    monkeypatch.setattr(monitoring, "_run_dependency_checks", _fake_run_dependency_checks)
    monkeypatch.setattr(monitoring, "_record_check_heartbeat", _fake_record_check_heartbeat)
    monkeypatch.setattr(monitoring, "_create_pagerduty_incident", _fake_create_pagerduty_incident)
    monkeypatch.setattr(monitoring, "clear_incident_failure", _fake_clear_incident_failure)
    monkeypatch.setattr(monitoring, "evaluate_incident_creation", _fake_evaluate_incident_creation)

    result = monitoring.monitor_dependencies.__wrapped__()

    assert result["down_services"] == ["Redis", "Nango"]
    assert created_incidents == ["Nango"]
