"""Passive Anthropic credit health monitoring and incidenting."""
from __future__ import annotations

import logging
from typing import Any

from anthropic import APIStatusError

from services.incident_throttling import clear_incident_failure, evaluate_incident_creation, mark_incident_created
from services.pagerduty import create_pagerduty_incident

logger = logging.getLogger(__name__)

_ANTHROPIC_CREDITS_CHECK_NAME = "Anthropic Credits"

# Common Anthropic/API billing exhaustion phrases observed in 4xx responses.
_OUT_OF_CREDITS_PATTERNS = (
    "out of credits",
    "credit balance is too low",
    "insufficient credits",
    "exceeded your current quota",
    "billing",
)


def _extract_error_message(exc: Exception) -> str:
    """Extract a normalized error message from Anthropic exceptions."""
    if isinstance(exc, APIStatusError):
        body = getattr(exc, "body", None)
        if isinstance(body, dict):
            error_payload = body.get("error")
            if isinstance(error_payload, dict):
                msg = error_payload.get("message")
                if isinstance(msg, str):
                    return msg.lower()
        return str(exc).lower()

    return str(exc).lower()


_DEFAULT_AGENT_STREAM_FAILURE_MESSAGE: str = (
    "\nSorry, something went wrong processing your message. Please try again."
)


def user_message_for_agent_stream_failure(exc: BaseException) -> str:
    """User-visible suffix when the agent stream fails (e.g. Slack incremental replies).

    Maps common transient Anthropic API failures (after retries are exhausted) to clear copy.
    """
    if isinstance(exc, APIStatusError):
        body: Any = getattr(exc, "body", None)
        if isinstance(body, dict):
            error_payload: Any = body.get("error")
            if isinstance(error_payload, dict):
                error_type: Any = error_payload.get("type")
                if error_type == "overloaded_error":
                    return "\nAnthropic is overloaded right now."
                if error_type == "api_error":
                    # e.g. Anthropic 5xx / "Internal server error" in response body
                    return "\nAnthropic had a temporary error. Please try again in a moment."
                if error_type == "rate_limit_error":
                    return "\nAnthropic rate-limited this request. Please try again shortly."
    return _DEFAULT_AGENT_STREAM_FAILURE_MESSAGE


def is_anthropic_out_of_credits_error(exc: Exception) -> bool:
    """Return True when an Anthropic failure indicates account credits are exhausted."""
    message = _extract_error_message(exc)
    return any(pattern in message for pattern in _OUT_OF_CREDITS_PATTERNS)


async def report_anthropic_call_success(*, source: str) -> None:
    """Clear throttled failure state after a successful Anthropic call."""
    logger.info("Anthropic passive credit health passing source=%s", source)
    await clear_incident_failure(_ANTHROPIC_CREDITS_CHECK_NAME)


async def report_anthropic_call_failure(*, exc: Exception, source: str) -> None:
    """Raise a throttled incident if Anthropic reports exhausted account credits."""
    if not is_anthropic_out_of_credits_error(exc):
        logger.debug("Anthropic failure is not credit exhaustion source=%s error=%s", source, exc)
        return

    logger.warning("Anthropic passive credit health failing source=%s error=%s", source, exc)
    should_create, reason = await evaluate_incident_creation(_ANTHROPIC_CREDITS_CHECK_NAME)
    if not should_create:
        logger.info(
            "Anthropic credit incident suppressed by throttle source=%s reason=%s",
            source,
            reason,
        )
        return

    incident_created = await create_pagerduty_incident(
        title="Anthropic credits exhausted",
        details=(
            "Passive Anthropic credit health check observed an out-of-credits response. "
            f"Source={source}. Reason={reason}. Error={exc}"
        ),
    )
    if incident_created:
        await mark_incident_created(_ANTHROPIC_CREDITS_CHECK_NAME)
