"""Plain-text conversation summary + metadata columns.

Revision ID: 125_conv_summary_plain
Revises: 124_convo_msg_visibility
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision: str = "125_conv_summary_plain"
down_revision: Union[str, None] = "124_convo_msg_visibility"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("summary_word_count", sa.Integer(), nullable=True),
    )
    op.add_column(
        "conversations",
        sa.Column("summary_updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "conversations",
        sa.Column(
            "title_llm_upgraded",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    conn = op.get_bind()
    rows = conn.execute(
        text("SELECT id, summary FROM conversations WHERE summary IS NOT NULL")
    ).fetchall()
    for row in rows:
        rid, summary_val = row[0], row[1]
        if not summary_val or not str(summary_val).strip().startswith("{"):
            continue
        try:
            data: dict[str, Any] = json.loads(summary_val)
            if not isinstance(data, dict):
                continue
            overall: str = str(data.get("overall") or "").strip()
            recent: str = str(data.get("recent") or "").strip()
            plain: str = (overall + (" " + recent if recent else "")).strip()
            if not plain:
                continue
            mc_raw = data.get("message_count_at_generation")
            swc: int | None = int(mc_raw) if mc_raw is not None else None
            su_raw = data.get("updated_at")
            su_dt: datetime | None = None
            if isinstance(su_raw, str) and su_raw.strip():
                try:
                    su_dt = datetime.fromisoformat(su_raw.replace("Z", "+00:00"))
                except ValueError:
                    su_dt = datetime.now(timezone.utc)
            conn.execute(
                text(
                    """
                    UPDATE conversations
                    SET summary = :plain,
                        summary_word_count = COALESCE(:swc, summary_word_count),
                        summary_updated_at = COALESCE(:su, summary_updated_at)
                    WHERE id = :rid
                    """
                ),
                {"plain": plain, "swc": swc, "su": su_dt, "rid": rid},
            )
        except (json.JSONDecodeError, TypeError, ValueError):
            continue

    conn.execute(
        text(
            """
            UPDATE conversations
            SET summary_word_count = COALESCE(
                summary_word_count,
                NULLIF(CARDINALITY(regexp_split_to_array(btrim(summary), '\\s+')), 0)
            )
            WHERE summary IS NOT NULL
              AND summary_word_count IS NULL
              AND btrim(summary) <> ''
            """
        )
    )

    # Keep server_default so INSERTs that omit the column still succeed
    # (e.g. during rolling deploys before new model code is live).


def downgrade() -> None:
    op.drop_column("conversations", "title_llm_upgraded")
    op.drop_column("conversations", "summary_updated_at")
    op.drop_column("conversations", "summary_word_count")
