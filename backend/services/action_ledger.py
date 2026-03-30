"""
Action Ledger service — records intent before and outcome after connector mutations.

Both functions swallow their own exceptions so ledger failures never block
connector operations.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Optional

from models.action_ledger import ActionLedgerEntry
from models.database import get_session

logger = logging.getLogger(__name__)

# Best-effort entity extraction from operation name + data dict.
_ENTITY_HEURISTICS: dict[str, tuple[str, str]] = {
    # operation_prefix -> (entity_type, id_key)
    "update_deal": ("deal", "deal_id"),
    "create_deal": ("deal", ""),
    "update_contact": ("contact", "contact_id"),
    "create_contact": ("contact", ""),
    "update_company": ("company", "company_id"),
    "create_company": ("company", ""),
    "create_note": ("note", "deal_id"),
    "send_email": ("email", ""),
    "insert_text": ("file", "external_id"),
    "edit_file": ("file", "external_id"),
    "create_file": ("file", ""),
    "append_rows": ("sheet", "external_id"),
}


def _extract_entity(operation: str, data: dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    """Return (entity_type, entity_id) from operation + data, best-effort."""
    for prefix, (etype, id_key) in _ENTITY_HEURISTICS.items():
        if operation.startswith(prefix):
            eid = str(data.get(id_key, "") or data.get("id", "")).strip() or None
            return etype, eid
    return None, None


async def record_intent(
    organization_id: str,
    user_id: str | None,
    context: dict[str, Any] | None,
    connector: str,
    dispatch_type: str,
    operation: str,
    data: dict[str, Any],
    connector_instance: Any | None = None,
) -> uuid.UUID | None:
    """Insert an INTENT row before a connector mutation. Returns change_id."""
    try:
        entity_type, entity_id = _extract_entity(operation, data)

        # Optional before-state capture
        before_state: dict[str, Any] | None = None
        if connector_instance is not None and hasattr(connector_instance, "capture_before_state"):
            try:
                before_state = await connector_instance.capture_before_state(operation, data)
            except Exception:
                logger.warning("capture_before_state failed for %s.%s", connector, operation, exc_info=True)

        ctx = context or {}
        conversation_id = ctx.get("conversation_id")
        workflow_id = ctx.get("workflow_id")

        change_id = uuid.uuid4()
        entry = ActionLedgerEntry(
            id=change_id,
            organization_id=uuid.UUID(organization_id),
            user_id=uuid.UUID(user_id) if user_id else None,
            conversation_id=uuid.UUID(conversation_id) if conversation_id else None,
            workflow_id=uuid.UUID(workflow_id) if workflow_id else None,
            connector=connector,
            dispatch_type=dispatch_type,
            operation=operation,
            entity_type=entity_type,
            entity_id=entity_id,
            intent={"changes": data, "before_state": before_state},
        )

        async with get_session(organization_id) as session:
            session.add(entry)
            await session.commit()

        return change_id
    except Exception:
        logger.warning("record_intent failed for %s.%s — ledger row not written", connector, operation, exc_info=True)
        return None


async def record_outcome(
    change_id: uuid.UUID | None,
    organization_id: str,
    result: dict[str, Any],
) -> None:
    """Update an existing ledger row with the mutation outcome."""
    if change_id is None:
        return
    try:
        status = "error" if "error" in result else "success"
        outcome: dict[str, Any] = {"status": status}
        if status == "error":
            outcome["error"] = result.get("error")
        else:
            outcome["response"] = result

        async with get_session(organization_id) as session:
            entry: ActionLedgerEntry | None = await session.get(ActionLedgerEntry, change_id)
            if entry:
                entry.outcome = outcome
                entry.executed_at = datetime.utcnow()
                await session.commit()
    except Exception:
        logger.warning("record_outcome failed for change_id=%s — outcome not recorded", change_id, exc_info=True)
