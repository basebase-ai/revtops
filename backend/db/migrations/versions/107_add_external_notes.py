"""Add external_notes JSONB column to meetings.

Revision ID: 107_external_notes
Revises: 106_add_phone_number_verified_at
Create Date: 2026-03-14

Stores per-source meeting notes (gemini, granola, fireflies, etc.) in a JSONB
column keyed by source name.  Existing summary/summary_doc_id data is migrated
into the new column so nothing is lost.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "107_external_notes"
down_revision: Union[str, None] = "106_add_phone_number_verified_at"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "meetings",
        sa.Column("external_notes", sa.dialects.postgresql.JSONB(), nullable=True),
    )
    # GIN index (default ops) for has-key (?) queries
    op.create_index(
        "ix_meetings_external_notes",
        "meetings",
        ["external_notes"],
        postgresql_using="gin",
    )

    # Migrate existing summary data into external_notes.
    # We can't know the original source for sure, but summary_doc_id implies
    # gemini; everything else we tag as "unknown" so it still shows up.
    # Each source key holds an array of note entries.
    op.execute(
        sa.text("""
            UPDATE meetings
            SET external_notes = jsonb_build_object(
                'gemini', jsonb_build_array(jsonb_build_object(
                    'content', summary,
                    'doc_id', summary_doc_id,
                    'fetched_at', to_char(updated_at, 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
                    'content_type', 'text/plain'
                ))
            )
            WHERE summary IS NOT NULL
              AND summary_doc_id IS NOT NULL
        """)
    )
    op.execute(
        sa.text("""
            UPDATE meetings
            SET external_notes = jsonb_build_object(
                'unknown', jsonb_build_array(jsonb_build_object(
                    'content', summary,
                    'fetched_at', to_char(updated_at, 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
                    'content_type', 'text/plain'
                ))
            )
            WHERE summary IS NOT NULL
              AND summary_doc_id IS NULL
              AND external_notes IS NULL
        """)
    )


def downgrade() -> None:
    # Restore summary/summary_doc_id from external_notes before dropping
    op.execute(
        sa.text("""
            UPDATE meetings
            SET summary = (external_notes->'gemini'->-1->>'content'),
                summary_doc_id = (external_notes->'gemini'->-1->>'doc_id')
            WHERE external_notes ? 'gemini'
              AND jsonb_array_length(external_notes->'gemini') > 0
        """)
    )
    op.drop_index("ix_meetings_external_notes", table_name="meetings")
    op.drop_column("meetings", "external_notes")
