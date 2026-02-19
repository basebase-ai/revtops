"""
Persistence layer for the new-style connector return types.

When a connector's ``sync_*`` method returns a ``list`` of Pydantic record
objects (rather than doing its own DB upserts and returning an ``int``),
the sync engine delegates to :func:`persist_records` to handle the upsert.

This module extracts the upsert logic that was previously duplicated across
individual connectors into a single, reusable function.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Sequence
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from models.database import get_session

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Model → table mapping
# ---------------------------------------------------------------------------

def _get_table_config(entity: str) -> dict[str, Any] | None:
    """Return (model_class, conflict_keys, updatable_columns) for an entity type."""
    from models.deal import Deal
    from models.account import Account
    from models.contact import Contact
    from models.activity import Activity
    from models.pipeline import Pipeline, PipelineStage
    from models.goal import Goal

    configs: dict[str, dict[str, Any]] = {
        "deals": {
            "model": Deal,
            "conflict_keys": ["organization_id", "source_system", "source_id"],
            "field_map": {
                "source_id": "source_id",
                "name": "name",
                "amount": "amount",
                "stage": "stage",
                "probability": "probability",
                "close_date": "close_date",
                "created_date": "created_date",
                "last_modified_date": "last_modified_date",
                "custom_fields": "custom_fields",
                "source_system": "source_system",
            },
        },
        "accounts": {
            "model": Account,
            "conflict_keys": ["organization_id", "source_system", "source_id"],
            "field_map": {
                "source_id": "source_id",
                "name": "name",
                "domain": "domain",
                "industry": "industry",
                "employee_count": "employee_count",
                "annual_revenue": "annual_revenue",
                "custom_fields": "custom_fields",
                "source_system": "source_system",
            },
        },
        "contacts": {
            "model": Contact,
            "conflict_keys": ["organization_id", "source_system", "source_id"],
            "field_map": {
                "source_id": "source_id",
                "name": "name",
                "email": "email",
                "title": "title",
                "phone": "phone",
                "custom_fields": "custom_fields",
                "source_system": "source_system",
            },
        },
        "activities": {
            "model": Activity,
            "conflict_keys": ["organization_id", "source_system", "source_id"],
            "field_map": {
                "source_id": "source_id",
                "type": "type",
                "subject": "subject",
                "description": "description",
                "activity_date": "activity_date",
                "custom_fields": "custom_fields",
                "source_system": "source_system",
            },
        },
        "pipelines": {
            "model": Pipeline,
            "conflict_keys": ["organization_id", "source_system", "source_id"],
            "field_map": {
                "source_id": "source_id",
                "name": "name",
                "display_order": "display_order",
                "is_default": "is_default",
                "source_system": "source_system",
            },
        },
        "goals": {
            "model": Goal,
            "conflict_keys": ["organization_id", "source_system", "source_id"],
            "field_map": {
                "source_id": "source_id",
                "name": "name",
                "target_amount": "target_amount",
                "start_date": "start_date",
                "end_date": "end_date",
                "goal_type": "goal_type",
                "custom_fields": "custom_fields",
                "source_system": "source_system",
            },
        },
    }
    return configs.get(entity)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def persist_records(
    organization_id: str,
    entity: str,
    records: Sequence[BaseModel],
    source_system: str,
) -> int:
    """Upsert a batch of Pydantic records into the corresponding DB table.

    Returns the number of records persisted.
    """
    if not records:
        return 0

    config = _get_table_config(entity)
    if config is None:
        logger.warning("No persistence config for entity type %r – skipping", entity)
        return 0

    model_cls = config["model"]
    conflict_keys: list[str] = config["conflict_keys"]
    field_map: dict[str, str] = config["field_map"]

    org_uuid = UUID(organization_id)
    now = datetime.utcnow()

    rows: list[dict[str, Any]] = []
    for record in records:
        record_dict = record.model_dump(exclude_none=False)
        row: dict[str, Any] = {"organization_id": org_uuid, "synced_at": now}
        for pydantic_field, db_column in field_map.items():
            if pydantic_field in record_dict:
                row[db_column] = record_dict[pydantic_field]
        if "source_system" in row and not row["source_system"]:
            row["source_system"] = source_system
        rows.append(row)

    async with get_session(organization_id=organization_id) as session:
        table = model_cls.__table__
        update_cols = {
            col: getattr(pg_insert(table).excluded, col)
            for col in field_map.values()
            if col not in conflict_keys
        }
        update_cols["synced_at"] = now

        stmt = (
            pg_insert(table)
            .values(rows)
            .on_conflict_do_update(
                index_elements=conflict_keys,
                set_=update_cols,
            )
        )
        await session.execute(stmt)
        await session.commit()

    logger.info(
        "Persisted %d %s records for org %s (source=%s)",
        len(rows), entity, organization_id, source_system,
    )
    return len(rows)
