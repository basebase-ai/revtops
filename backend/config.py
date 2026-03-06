"""Configuration management using Pydantic settings."""

from datetime import date, datetime
import logging
import os
from pathlib import Path
from typing import Optional

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings


def to_iso8601(dt: datetime | date | None) -> str | None:
    """
    Format datetime/date to ISO8601 string for JSON responses.
    
    All datetimes are assumed to be UTC and get 'Z' suffix.
    Date-only values get no timezone suffix.
    
    Usage:
        "created_at": to_iso8601(self.created_at)
    """
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return f"{dt.isoformat()}Z"
    # date only - no timezone
    return dt.isoformat()

# Find .env file - check current dir, then parent (for when running from backend/)
_env_file = Path(".env")
if not _env_file.exists():
    _env_file = Path("../.env")


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/revenue_copilot"
    DB_POOL_SIZE: int = 1
    DB_MAX_OVERFLOW: int = 2
    DB_POOL_TIMEOUT_SECONDS: int = 10
    DB_POOL_RECYCLE_SECONDS: int = 45
    DB_CONNECT_TIMEOUT_SECONDS: float = 10.0
    DB_COMMAND_TIMEOUT_SECONDS: float = 30.0
    DB_TCP_KEEPALIVES_IDLE_SECONDS: int = 60
    DB_TCP_KEEPALIVES_INTERVAL_SECONDS: int = 30
    DB_TCP_KEEPALIVES_COUNT: int = 5

    # Redis
    REDIS_URL: str = "redis://localhost:6379"
    # Timeouts for remote Redis (e.g. Railway); defaults are often too short
    REDIS_SOCKET_CONNECT_TIMEOUT: float = 10.0
    REDIS_SOCKET_TIMEOUT: float = 10.0

    # Anthropic
    ANTHROPIC_API_KEY: Optional[str] = None
    
    # OpenAI (for embeddings + research fallback)
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_RESEARCH_MODEL: str = "gpt-5"  # Prefer GPT-5+ for web research synthesis
    
    # Perplexity (for web search)
    PERPLEXITY_API_KEY: Optional[str] = None

    # Exa (for web search tool, provider="exa")
    EXA_API_KEY: Optional[str] = None

    # Nango - OAuth & credential management for all integrations
    NANGO_SECRET_KEY: Optional[str] = None
    NANGO_PUBLIC_KEY: Optional[str] = None  # For frontend connect UI
    NANGO_HOST: str = "https://api.nango.dev"

    # Integration IDs in Nango (map to your Nango integration configs)
    NANGO_HUBSPOT_INTEGRATION_ID: str = "hubspot"
    NANGO_SLACK_INTEGRATION_ID: str = "slack"
    NANGO_GOOGLE_CALENDAR_INTEGRATION_ID: str = "google-calendar"
    NANGO_GMAIL_INTEGRATION_ID: str = "google-mail"
    NANGO_SALESFORCE_INTEGRATION_ID: str = "salesforce"
    NANGO_MICROSOFT_CALENDAR_INTEGRATION_ID: str = "microsoft"
    NANGO_MICROSOFT_MAIL_INTEGRATION_ID: str = "microsoft"
    NANGO_FIREFLIES_INTEGRATION_ID: str = "fireflies"
    NANGO_ZOOM_INTEGRATION_ID: str = "zoom"
    NANGO_GOOGLE_DRIVE_INTEGRATION_ID: str = "google-drive"
    NANGO_APOLLO_INTEGRATION_ID: str = "apollo"
    NANGO_GITHUB_INTEGRATION_ID: str = "github"
    NANGO_LINEAR_INTEGRATION_ID: str = "linear"
    NANGO_ASANA_INTEGRATION_ID: str = "asana"

    # App
    SECRET_KEY: str = "dev-secret-change-in-production"
    ENVIRONMENT: str = "development"
    FRONTEND_URL: str = "http://localhost:5173"
    
    # Supabase configuration
    # URL: Your Supabase project URL (e.g., https://xyz.supabase.co)
    SUPABASE_URL: Optional[str] = None
    # JWT Secret: Legacy HS256 secret (optional if using ES256)
    SUPABASE_JWT_SECRET: Optional[str] = None
    # Public anon key for browser-safe auth operations (password recovery, etc.)
    SUPABASE_ANON_KEY: Optional[str] = None
    
    # Admin
    ADMIN_KEY: Optional[str] = None  # Simple admin auth for MVP
    
    # Email (Resend)
    RESEND_API_KEY: Optional[str] = None
    EMAIL_FROM: str = "Basebase <hello@basebase.com>"
    
    # SMS (Twilio)
    TWILIO_ACCOUNT_SID: Optional[str] = None
    TWILIO_AUTH_TOKEN: Optional[str] = None
    TWILIO_PHONE_NUMBER: Optional[str] = None  # E.164 format, e.g., +14155551234
    TWILIO_WEBHOOK_URL: Optional[str] = None  # Exact public URL for signature validation, e.g., https://api.basebase.com/api/twilio/webhook
    WHATSAPP_WEBHOOK_URL: Optional[str] = None  # Exact public URL for WhatsApp webhook, e.g., https://api.basebase.com/api/whatsapp/webhook
    
    # Slack Events API (for receiving DMs)
    SLACK_SIGNING_SECRET: Optional[str] = None
    # Slack OAuth app (same app as Nango uses) — for "Add Penny to Slack" bot-install flow
    SLACK_CLIENT_ID: Optional[str] = None
    SLACK_CLIENT_SECRET: Optional[str] = None
    # Public backend URL (for Slack OAuth redirect_uri). Default from FRONTEND_URL for local.
    BACKEND_PUBLIC_URL: Optional[str] = None
    
    # ScrapingBee (for fetch_url tool - web scraping with proxy support)
    SCRAPINGBEE_API_KEY: Optional[str] = None

    # E2B (sandboxed code execution for execute_command tool)
    E2B_API_KEY: Optional[str] = None

    # Stripe (subscription billing)
    STRIPE_SECRET_KEY: Optional[str] = None
    STRIPE_WEBHOOK_SECRET: Optional[str] = None
    STRIPE_PUBLISHABLE_KEY: Optional[str] = None

    # PagerDuty (outbound incident creation)
    PAGERDUTY_FROM_EMAIL: Optional[str] = None
    PAGERDUTY_KEY: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("PAGERDUTY_KEY", "PagerDuty_Key"),
    )
    PAGERDUTY_SERVICE_ID: Optional[str] = None

    # Credits
    NUM_GRACE_CREDITS: int = 5

    @property
    def sandbox_database_url(self) -> str:
        """Sync Postgres URL for E2B sandbox (strips SQLAlchemy asyncpg prefix)."""
        return self.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")

    class Config:
        env_file = str(_env_file)
        env_file_encoding = "utf-8"
        extra = "ignore"  # Ignore extra env vars from shared .env files


settings = Settings()


def get_redis_connection_kwargs(
    decode_responses: bool = False,
) -> dict[str, str | float | bool]:
    """Connection options for Redis (e.g. Railway). Longer timeouts for remote Redis."""
    kwargs: dict[str, str | float | bool] = {
        "socket_connect_timeout": settings.REDIS_SOCKET_CONNECT_TIMEOUT,
        "socket_timeout": settings.REDIS_SOCKET_TIMEOUT,
        "retry_on_timeout": True,
    }
    if decode_responses:
        kwargs["decode_responses"] = True
    return kwargs


EXPECTED_ENV_VARS: tuple[str, ...] = (
    "DATABASE_URL",
    "REDIS_URL",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "PERPLEXITY_API_KEY",
    "NANGO_SECRET_KEY",
    "NANGO_PUBLIC_KEY",
    "SECRET_KEY",
    "ENVIRONMENT",
    "FRONTEND_URL",
    "SUPABASE_URL",
    "SUPABASE_JWT_SECRET",
    "SUPABASE_ANON_KEY",
    "ADMIN_KEY",
    "RESEND_API_KEY",
    "EMAIL_FROM",
    "PAGERDUTY_FROM_EMAIL",
    "PagerDuty_Key",
    "PAGERDUTY_SERVICE_ID",
)


def log_missing_env_vars(logger: logging.Logger) -> None:
    """Log debug warnings for expected environment variables that are unset."""
    for var_name in EXPECTED_ENV_VARS:
        value = os.environ.get(var_name)
        if value is None or value == "":
            logger.debug(
                "Warning: expected environment variable %s is not set.",
                var_name,
            )


# Nango integration ID mapping
NANGO_INTEGRATION_IDS: dict[str, str] = {
    "hubspot": settings.NANGO_HUBSPOT_INTEGRATION_ID,
    "slack": settings.NANGO_SLACK_INTEGRATION_ID,
    "google_calendar": settings.NANGO_GOOGLE_CALENDAR_INTEGRATION_ID,
    "gmail": settings.NANGO_GMAIL_INTEGRATION_ID,
    "salesforce": settings.NANGO_SALESFORCE_INTEGRATION_ID,
    "microsoft_calendar": settings.NANGO_MICROSOFT_CALENDAR_INTEGRATION_ID,
    "microsoft_mail": settings.NANGO_MICROSOFT_MAIL_INTEGRATION_ID,
    "fireflies": settings.NANGO_FIREFLIES_INTEGRATION_ID,
    "zoom": settings.NANGO_ZOOM_INTEGRATION_ID,
    "google_drive": settings.NANGO_GOOGLE_DRIVE_INTEGRATION_ID,
    "apollo": settings.NANGO_APOLLO_INTEGRATION_ID,
    "github": settings.NANGO_GITHUB_INTEGRATION_ID,
    "linear": settings.NANGO_LINEAR_INTEGRATION_ID,
    "asana": settings.NANGO_ASANA_INTEGRATION_ID,
}

# Default sharing settings for each provider when user first connects.
# All connectors are user-scoped; these defaults populate the sharing modal.
# - share_synced_data: Team can see synced records (deals, contacts, etc.)
# - share_query_access: Team can query live data via this connection
# - share_write_access: Team can write data via this connection
from dataclasses import dataclass


@dataclass(frozen=True)
class SharingDefaults:
    """Default sharing settings for a provider."""

    share_synced_data: bool = False
    share_query_access: bool = False
    share_write_access: bool = False


PROVIDER_SHARING_DEFAULTS: dict[str, SharingDefaults] = {
    # CRMs - typically share synced data with team
    "hubspot": SharingDefaults(share_synced_data=True),
    "salesforce": SharingDefaults(share_synced_data=True),
    "apollo": SharingDefaults(share_synced_data=True),
    # Collaboration tools - share synced data
    "slack": SharingDefaults(share_synced_data=True),
    "github": SharingDefaults(share_synced_data=True),
    "linear": SharingDefaults(share_synced_data=True),
    "asana": SharingDefaults(share_synced_data=True),
    "jira": SharingDefaults(share_synced_data=True),
    # Personal tools - private by default
    "google_calendar": SharingDefaults(),
    "gmail": SharingDefaults(),
    "microsoft_calendar": SharingDefaults(),
    "microsoft_mail": SharingDefaults(),
    "fireflies": SharingDefaults(),
    "zoom": SharingDefaults(),
    "google_drive": SharingDefaults(),
    # Utility connectors - share synced data
    "web_search": SharingDefaults(share_synced_data=True),
    "code_sandbox": SharingDefaults(share_synced_data=True),
    "twilio": SharingDefaults(share_synced_data=True),
}


def get_nango_integration_id(provider: str) -> str:
    """Get the Nango integration ID for a provider.

    Falls back to ConnectorMeta.nango_integration_id for connectors that
    aren't in the hardcoded dict (e.g. community connectors).
    """
    integration_id = NANGO_INTEGRATION_IDS.get(provider)
    if integration_id:
        return integration_id

    try:
        from connectors.registry import discover_connectors

        registry = discover_connectors()
        connector_cls = registry.get(provider)
        if connector_cls and hasattr(connector_cls, "meta") and connector_cls.meta.nango_integration_id:
            return connector_cls.meta.nango_integration_id
    except Exception:
        pass

    raise ValueError(f"Unknown provider: {provider}")


def get_provider_sharing_defaults(provider: str) -> SharingDefaults:
    """Get the default sharing settings for a provider.

    Falls back to ConnectorMeta.default_sharing for community connectors,
    or all-false defaults if not specified.
    """
    defaults = PROVIDER_SHARING_DEFAULTS.get(provider)
    if defaults:
        return defaults

    try:
        from connectors.registry import discover_connectors

        registry = discover_connectors()
        connector_cls = registry.get(provider)
        if connector_cls and hasattr(connector_cls, "meta") and connector_cls.meta.default_sharing:
            return connector_cls.meta.default_sharing
    except Exception:
        pass

    return SharingDefaults()
