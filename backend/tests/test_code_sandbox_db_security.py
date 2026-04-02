from config import settings
from connectors import code_sandbox
from agents import tools


def test_sandbox_database_connection_env_uses_discrete_fields() -> None:
    original_database_url: str = settings.DATABASE_URL
    try:
        settings.DATABASE_URL = "postgresql+asyncpg://alice:secret@db.internal:5433/revdb?sslmode=require"
        env = settings.sandbox_database_connection_env
    finally:
        settings.DATABASE_URL = original_database_url

    assert env == {
        "DB_HOST": "db.internal",
        "DB_PORT": "5433",
        "DB_NAME": "revdb",
        "DB_USER": "alice",
        "DB_PASSWORD": "secret",
        "DB_SSLMODE": "require",
    }


def test_sandbox_db_helper_does_not_require_database_uri_env() -> None:
    connector_template: str = code_sandbox._SANDBOX_DB_HELPER_TEMPLATE
    tools_template: str = tools._SANDBOX_DB_HELPER_TEMPLATE

    assert "DATABASE_URL" not in connector_template
    assert "DATABASE_URL" not in tools_template
    assert "SET default_transaction_read_only = on" in connector_template
    assert "SET default_transaction_read_only = on" in tools_template
