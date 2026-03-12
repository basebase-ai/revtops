"""Add huddle fields to meetings table.

Revision ID: 099_meeting_huddle_fields
Revises: 098_apps_integration
Create Date: 2026-03-11

Adds columns for Google Meet huddle support: conference link, event ID,
huddle status, and recording references.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "099_meeting_huddle_fields"
down_revision: Union[str, None] = "098_apps_integration"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("meetings", sa.Column("conference_link", sa.String(500), nullable=True))
    op.add_column("meetings", sa.Column("google_event_id", sa.String(255), nullable=True))
    op.add_column("meetings", sa.Column("huddle_status", sa.String(50), nullable=True))
    op.add_column("meetings", sa.Column("recording_url", sa.String(500), nullable=True))
    op.add_column("meetings", sa.Column("recording_drive_id", sa.String(255), nullable=True))

    op.create_index("ix_meetings_google_event_id", "meetings", ["google_event_id"])
    op.create_index("ix_meetings_huddle_status", "meetings", ["huddle_status"])


def downgrade() -> None:
    op.drop_index("ix_meetings_huddle_status", table_name="meetings")
    op.drop_index("ix_meetings_google_event_id", table_name="meetings")

    op.drop_column("meetings", "recording_drive_id")
    op.drop_column("meetings", "recording_url")
    op.drop_column("meetings", "huddle_status")
    op.drop_column("meetings", "google_event_id")
    op.drop_column("meetings", "conference_link")
