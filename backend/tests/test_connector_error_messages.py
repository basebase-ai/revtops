from connectors.base import (
    build_connection_removed_message,
    get_provider_display_name,
    is_connection_removed_error,
)


def test_build_connection_removed_message_for_slack() -> None:
    message = build_connection_removed_message("slack")

    assert "Slack connection has expired or been revoked" in message
    assert "/connectors" in message
    assert "disconnect and reconnect Slack" in message


def test_provider_display_name_humanizes_known_and_unknown_values() -> None:
    assert get_provider_display_name("google_calendar") == "Google Calendar"
    assert get_provider_display_name("salesforce") == "Salesforce"


def test_is_connection_removed_error_detects_revoked_auth_patterns() -> None:
    assert is_connection_removed_error("Slack API error: invalid_auth") is True
    assert is_connection_removed_error("Client error '404 Not Found' for url") is True
    assert is_connection_removed_error("Client error '400 Bad Request' for url") is True
    assert is_connection_removed_error("temporary upstream timeout") is False
