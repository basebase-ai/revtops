"""Alembic migration environment configuration."""

from logging.config import fileConfig
from pathlib import Path

from dotenv import load_dotenv
from alembic import context
from sqlalchemy import pool, create_engine

# Load .env from project root before config (alembic cwd may differ)
_root: Path = Path(__file__).resolve().parent.parent.parent
load_dotenv(_root / ".env")

from config import settings
from models.database import Base

# Import all models to ensure they're registered with Base.metadata
from models.user import User
from models.organization import Organization
from models.pipeline import Pipeline, PipelineStage
from models.deal import Deal
from models.account import Account
from models.contact import Contact
from models.activity import Activity
from models.artifact import Artifact
from models.chat_message import ChatMessage
from models.integration import Integration
from models.credit_transaction import CreditTransaction

config = context.config

# Use MIGRATION_DATABASE_URL for DDL (direct connection, table owner) when set;
# otherwise DATABASE_URL (pooler can cause "must be owner" errors).
migration_url: str = (
    settings.MIGRATION_DATABASE_URL
    if settings.MIGRATION_DATABASE_URL
    else settings.DATABASE_URL
)
sync_url: str = migration_url.replace("+asyncpg", "")
config.set_main_option("sqlalchemy.url", sync_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = create_engine(
        sync_url,
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()

    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
