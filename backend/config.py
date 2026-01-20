"""Configuration management using Pydantic settings."""

from pathlib import Path
from pydantic_settings import BaseSettings
from typing import Optional

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

    # Nango - OAuth & credential management for all integrations
    NANGO_SECRET_KEY: Optional[str] = None
    NANGO_PUBLIC_KEY: Optional[str] = None  # For frontend connect UI
    NANGO_HOST: str = "https://api.nango.dev"

    # Integration IDs in Nango (map to your Nango integration configs)
    NANGO_HUBSPOT_INTEGRATION_ID: str = "hubspot"
    NANGO_SLACK_INTEGRATION_ID: str = "slack"
    NANGO_GOOGLE_CALENDAR_INTEGRATION_ID: str = "google-calendar"
    NANGO_SALESFORCE_INTEGRATION_ID: str = "salesforce"

    # App
    SECRET_KEY: str = "dev-secret-change-in-production"
    ENVIRONMENT: str = "development"
    FRONTEND_URL: str = "http://localhost:5173"

    class Config:
        env_file = str(_env_file)
        env_file_encoding = "utf-8"
        extra = "ignore"  # Ignore extra env vars from shared .env files


settings = Settings()


# Nango integration ID mapping
NANGO_INTEGRATION_IDS: dict[str, str] = {
    "hubspot": settings.NANGO_HUBSPOT_INTEGRATION_ID,
    "slack": settings.NANGO_SLACK_INTEGRATION_ID,
    "google_calendar": settings.NANGO_GOOGLE_CALENDAR_INTEGRATION_ID,
    "salesforce": settings.NANGO_SALESFORCE_INTEGRATION_ID,
}


def get_nango_integration_id(provider: str) -> str:
    """Get the Nango integration ID for a provider."""
    integration_id = NANGO_INTEGRATION_IDS.get(provider)
    if not integration_id:
        raise ValueError(f"Unknown provider: {provider}")
    return integration_id
