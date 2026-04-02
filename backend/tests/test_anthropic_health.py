from __future__ import annotations

from typing import Any

import httpx
from anthropic import APIStatusError

from services import anthropic_health


def _api_status_error(message: str, status_code: int = 429, error_type: str = "rate_limit_error") -> APIStatusError:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(status_code=status_code, request=request, headers={"request-id": "req_test"})
    return APIStatusError(
        message=message,
        response=response,
        body={"error": {"type": error_type, "message": message}},
    )


def test_is_anthropic_out_of_credits_error_detects_known_message() -> None:
    exc = _api_status_error("Credit balance is too low to access this model")
    assert anthropic_health.is_anthropic_out_of_credits_error(exc) is True


def test_is_anthropic_out_of_credits_error_ignores_non_credit_error() -> None:
    exc = _api_status_error("Request exceeded max context window", status_code=400, error_type="invalid_request_error")
    assert anthropic_health.is_anthropic_out_of_credits_error(exc) is False


def test_user_message_for_agent_stream_failure_overloaded() -> None:
    exc = _api_status_error("Overloaded", status_code=529, error_type="overloaded_error")
    assert anthropic_health.user_message_for_agent_stream_failure(exc) == "\nAnthropic is overloaded right now."


def test_user_message_for_agent_stream_failure_default() -> None:
    exc = _api_status_error("Bad request", status_code=400, error_type="invalid_request_error")
    assert anthropic_health.user_message_for_agent_stream_failure(exc) == (
        "\nSorry, something went wrong processing your message. Please try again."
    )


def test_user_message_for_agent_stream_failure_api_error() -> None:
    exc = _api_status_error("Internal server error", status_code=500, error_type="api_error")
    assert anthropic_health.user_message_for_agent_stream_failure(exc) == (
        "\nAnthropic had a temporary error. Please try again in a moment."
    )


def test_user_message_for_agent_stream_failure_rate_limit() -> None:
    exc = _api_status_error("Too many requests", status_code=429, error_type="rate_limit_error")
    assert anthropic_health.user_message_for_agent_stream_failure(exc) == (
        "\nAnthropic rate-limited this request. Please try again shortly."
    )


def test_report_anthropic_call_failure_creates_incident_when_allowed(monkeypatch: Any) -> None:
    eval_calls: list[str] = []
    incident_titles: list[str] = []

    async def _fake_evaluate(check_name: str) -> tuple[bool, str]:
        eval_calls.append(check_name)
        return True, "new_failure"

    async def _fake_incident(*, title: str, details: str) -> bool:
        incident_titles.append(title)
        assert "workers.tasks.workflows._action_llm" in details
        return True

    monkeypatch.setattr(anthropic_health, "evaluate_incident_creation", _fake_evaluate)
    monkeypatch.setattr(anthropic_health, "create_pagerduty_incident", _fake_incident)

    import asyncio

    asyncio.run(
        anthropic_health.report_anthropic_call_failure(
            exc=_api_status_error("out of credits"),
            source="workers.tasks.workflows._action_llm",
        )
    )

    assert eval_calls == ["Anthropic Credits"]
    assert incident_titles == ["Anthropic credits exhausted"]


def test_report_anthropic_call_failure_suppresses_when_throttled(monkeypatch: Any) -> None:
    incident_titles: list[str] = []

    async def _fake_evaluate(check_name: str) -> tuple[bool, str]:
        return False, "suppressed_for_100s"

    async def _fake_incident(*, title: str, details: str) -> bool:
        incident_titles.append(title)
        return True

    monkeypatch.setattr(anthropic_health, "evaluate_incident_creation", _fake_evaluate)
    monkeypatch.setattr(anthropic_health, "create_pagerduty_incident", _fake_incident)

    import asyncio

    asyncio.run(
        anthropic_health.report_anthropic_call_failure(
            exc=_api_status_error("insufficient credits"),
            source="agents.orchestrator._stream_with_tools",
        )
    )

    assert incident_titles == []


def test_report_anthropic_call_success_clears_failure_state(monkeypatch: Any) -> None:
    cleared: list[str] = []

    async def _fake_clear(check_name: str) -> None:
        cleared.append(check_name)

    monkeypatch.setattr(anthropic_health, "clear_incident_failure", _fake_clear)

    import asyncio

    asyncio.run(
        anthropic_health.report_anthropic_call_success(source="services.conversation_summary.generate_conversation_summary")
    )

    assert cleared == ["Anthropic Credits"]
