"""Configuration management using Pydantic settings."""

from datetime import date, datetime
import logging
import os
from pathlib import Path
from typing import Optional

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

    # Redis
    REDIS_URL: str = "redis://localhost:6379"

    # Anthropic
    ANTHROPIC_API_KEY: Optional[str] = None
    
    # OpenAI (for embeddings)
    OPENAI_API_KEY: Optional[str] = None
    
    # Perplexity (for web search)
    PERPLEXITY_API_KEY: Optional[str] = None

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
    NANGO_GOOGLE_SHEETS_INTEGRATION_ID: str = "google-sheet"
    NANGO_APOLLO_INTEGRATION_ID: str = "apollo"

    # App
    SECRET_KEY: str = "dev-secret-change-in-production"
    ENVIRONMENT: str = "development"
    FRONTEND_URL: str = "http://localhost:5173"
    
    # Supabase configuration
    # URL: Your Supabase project URL (e.g., https://xyz.supabase.co)
    SUPABASE_URL: Optional[str] = None
    # JWT Secret: Legacy HS256 secret (optional if using ES256)
    SUPABASE_JWT_SECRET: Optional[str] = None
    
    # Admin
    ADMIN_KEY: Optional[str] = None  # Simple admin auth for MVP
    
    # Email (Resend)
    RESEND_API_KEY: Optional[str] = None
    EMAIL_FROM: str = "Revtops <hello@revtops.com>"
    
    # SMS (Twilio)
    TWILIO_ACCOUNT_SID: Optional[str] = None
    TWILIO_AUTH_TOKEN: Optional[str] = None
    TWILIO_PHONE_NUMBER: Optional[str] = None  # E.164 format, e.g., +14155551234
    
    # Slack Events API (for receiving DMs)
    SLACK_SIGNING_SECRET: Optional[str] = None

    class Config:
        env_file = str(_env_file)
        env_file_encoding = "utf-8"
        extra = "ignore"  # Ignore extra env vars from shared .env files


settings = Settings()

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
    "ADMIN_KEY",
    "RESEND_API_KEY",
    "EMAIL_FROM",
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
    "google_sheets": settings.NANGO_GOOGLE_SHEETS_INTEGRATION_ID,
    "apollo": settings.NANGO_APOLLO_INTEGRATION_ID,
}

# Provider scope mapping: which integrations are user-scoped vs org-scoped
# - 'organization': One connection for the entire org (CRMs)
# - 'user': Each user connects individually (email, calendar)
PROVIDER_SCOPES: dict[str, str] = {
    "hubspot": "organization",
    "salesforce": "organization",
    "slack": "organization",
    "google_calendar": "user",
    "gmail": "user",
    "microsoft_calendar": "user",
    "microsoft_mail": "user",
    "fireflies": "user",
    "zoom": "user",
    "google_sheets": "user",
    "apollo": "organization",
}


def get_nango_integration_id(provider: str) -> str:
    """Get the Nango integration ID for a provider."""
    integration_id = NANGO_INTEGRATION_IDS.get(provider)
    if not integration_id:
        raise ValueError(f"Unknown provider: {provider}")
    return integration_id


def get_provider_scope(provider: str) -> str:
    """Get the scope for a provider ('organization' or 'user')."""
    return PROVIDER_SCOPES.get(provider, "organization")
