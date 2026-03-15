"""Tests for Meeting.external_notes — set_notes, has_notes_from,
missing_notes_filter, and find_or_create_meeting integration."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from models.meeting import Meeting


# ── Model unit tests ──────────────────────────────────────────────


class TestSetNotes:
    def test_first_note_creates_array(self):
        m = Meeting()
        m.set_notes("granola", "hello")

        assert "granola" in m.external_notes
        assert len(m.external_notes["granola"]) == 1
        assert m.external_notes["granola"][0]["content"] == "hello"
        assert m.external_notes["granola"][0]["content_type"] == "text/plain"
        assert "fetched_at" in m.external_notes["granola"][0]

    def test_doc_id_stored_when_provided(self):
        m = Meeting()
        m.set_notes("gemini", "notes", doc_id="abc123")

        assert m.external_notes["gemini"][0]["doc_id"] == "abc123"

    def test_doc_id_omitted_when_not_provided(self):
        m = Meeting()
        m.set_notes("granola", "notes")

        assert "doc_id" not in m.external_notes["granola"][0]

    def test_multiple_entries_same_source(self):
        m = Meeting()
        m.set_notes("gemini", "first", doc_id="d1")
        m.set_notes("gemini", "second", doc_id="d2")

        assert len(m.external_notes["gemini"]) == 2
        assert m.external_notes["gemini"][0]["content"] == "first"
        assert m.external_notes["gemini"][1]["content"] == "second"

    def test_multiple_sources_coexist(self):
        m = Meeting()
        m.set_notes("granola", "granola notes")
        m.set_notes("gemini", "gemini notes")
        m.set_notes("fireflies", "fireflies notes")

        assert len(m.external_notes) == 3
        assert len(m.external_notes["granola"]) == 1
        assert len(m.external_notes["gemini"]) == 1
        assert len(m.external_notes["fireflies"]) == 1

    def test_duplicate_content_skipped(self):
        m = Meeting()
        m.set_notes("gemini", "same content", doc_id="d1")
        m.set_notes("gemini", "same content", doc_id="d2")  # same content

        assert len(m.external_notes["gemini"]) == 1

    def test_different_content_not_skipped(self):
        m = Meeting()
        m.set_notes("gemini", "version 1")
        m.set_notes("gemini", "version 2")

        assert len(m.external_notes["gemini"]) == 2

    def test_new_dict_reference_on_mutation(self):
        """SQLAlchemy needs a new dict reference to detect JSONB changes."""
        m = Meeting()
        m.set_notes("granola", "first")
        ref1 = m.external_notes
        m.set_notes("granola", "second")
        ref2 = m.external_notes
        assert ref1 is not ref2


class TestHasNotesFrom:
    def test_false_when_none(self):
        m = Meeting()
        assert not m.has_notes_from("gemini")

    def test_false_when_empty_dict(self):
        m = Meeting()
        m.external_notes = {}
        assert not m.has_notes_from("gemini")

    def test_false_when_empty_array(self):
        m = Meeting()
        m.external_notes = {"gemini": []}
        assert not m.has_notes_from("gemini")

    def test_true_when_present(self):
        m = Meeting()
        m.set_notes("gemini", "notes")
        assert m.has_notes_from("gemini")

    def test_false_for_other_source(self):
        m = Meeting()
        m.set_notes("granola", "notes")
        assert not m.has_notes_from("gemini")


class TestSetNotesDoesNotTouchSummary:
    def test_summary_unchanged(self):
        m = Meeting()
        m.summary = "Original summary"
        m.set_notes("granola", "granola notes")
        assert m.summary == "Original summary"

    def test_summary_stays_none(self):
        m = Meeting()
        m.set_notes("gemini", "gemini notes")
        assert m.summary is None


class TestSetNotesReturnValue:
    def test_returns_true_on_new_content(self):
        m = Meeting()
        assert m.set_notes("granola", "hello") is True

    def test_returns_false_on_duplicate(self):
        m = Meeting()
        m.set_notes("granola", "hello")
        assert m.set_notes("granola", "hello") is False

    def test_returns_true_on_different_content(self):
        m = Meeting()
        m.set_notes("granola", "v1")
        assert m.set_notes("granola", "v2") is True


class TestMissingNotesFilter:
    def test_generates_valid_clause(self):
        clause = Meeting.missing_notes_filter("gemini")
        compiled = str(clause.compile(compile_kwargs={"literal_binds": True}))
        assert "external_notes IS NULL" in compiled
        assert "external_notes" in compiled


class TestToDict:
    def test_external_notes_included(self):
        m = Meeting(
            id=uuid4(),
            organization_id=uuid4(),
            scheduled_start=datetime(2026, 1, 1),
            status="completed",
        )
        m.set_notes("gemini", "notes")
        d = m.to_dict()
        assert "external_notes" in d
        assert "gemini" in d["external_notes"]

    def test_external_notes_none_when_empty(self):
        m = Meeting(
            id=uuid4(),
            organization_id=uuid4(),
            scheduled_start=datetime(2026, 1, 1),
            status="scheduled",
        )
        d = m.to_dict()
        assert d["external_notes"] is None


# ── Dedup service integration ─────────────────────────────────────


class TestFindOrCreateMeetingNotes:
    """Test that find_or_create_meeting correctly calls set_notes."""

    def _make_meeting(self, **kwargs):
        defaults = {
            "id": uuid4(),
            "organization_id": uuid4(),
            "scheduled_start": datetime(2026, 3, 1, 10, 0),
            "status": "scheduled",
            "summary": None,
            "external_notes": None,
            "title": None,
            "duration_minutes": None,
            "scheduled_end": None,
            "organizer_email": None,
            "participants": None,
            "participant_count": None,
            "action_items": None,
            "key_topics": None,
        }
        defaults.update(kwargs)
        m = Meeting(**{k: v for k, v in defaults.items() if k != "external_notes"})
        m.external_notes = defaults["external_notes"]
        return m

    @pytest.mark.asyncio
    async def test_create_with_notes_source(self):
        from services.meeting_dedup import find_or_create_meeting

        org_id = uuid4()
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None))
        )
        # Capture the Meeting that gets added
        added = []
        mock_session.add = lambda obj: added.append(obj)
        mock_session.commit = AsyncMock()
        mock_session.refresh = AsyncMock()

        with patch("services.meeting_dedup.get_session") as mock_gs:
            mock_gs.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_gs.return_value.__aexit__ = AsyncMock(return_value=False)

            await find_or_create_meeting(
                organization_id=org_id,
                scheduled_start=datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc),
                title="Test Meeting",
                notes_source="granola",
                notes_text="My granola notes",
            )

        assert len(added) == 1
        meeting = added[0]
        assert meeting.has_notes_from("granola")
        assert meeting.external_notes["granola"][0]["content"] == "My granola notes"

    @pytest.mark.asyncio
    async def test_update_with_notes_source(self):
        from services.meeting_dedup import find_or_create_meeting

        org_id = uuid4()
        existing = self._make_meeting(
            organization_id=org_id,
            title="Standup",
        )
        existing.set_notes("granola", "Old granola notes")

        mock_session = AsyncMock()
        # find_matching_meeting calls session.execute → .scalars().all()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [existing]
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()
        mock_session.refresh = AsyncMock()

        with patch("services.meeting_dedup.get_session") as mock_gs:
            mock_gs.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_gs.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await find_or_create_meeting(
                organization_id=org_id,
                scheduled_start=datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc),
                title="Standup",  # exact match triggers update path
                notes_source="gemini",
                notes_text="Gemini summary",
                notes_doc_id="doc123",
            )

        # Both sources should coexist
        assert result.has_notes_from("granola")
        assert result.has_notes_from("gemini")
        assert result.external_notes["gemini"][0]["doc_id"] == "doc123"

    @pytest.mark.asyncio
    async def test_plain_summary_still_works(self):
        """Connectors not yet migrated can still pass summary= directly."""
        from services.meeting_dedup import find_or_create_meeting

        org_id = uuid4()
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None))
        )
        added = []
        mock_session.add = lambda obj: added.append(obj)
        mock_session.commit = AsyncMock()
        mock_session.refresh = AsyncMock()

        with patch("services.meeting_dedup.get_session") as mock_gs:
            mock_gs.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_gs.return_value.__aexit__ = AsyncMock(return_value=False)

            await find_or_create_meeting(
                organization_id=org_id,
                scheduled_start=datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc),
                summary="Plain summary",
            )

        meeting = added[0]
        assert meeting.summary == "Plain summary"
        assert meeting.external_notes is None  # not routed through set_notes


# ── Edge cases ────────────────────────────────────────────────────


class TestEdgeCases:
    def test_empty_string_content_still_stored(self):
        m = Meeting()
        m.set_notes("gemini", "")
        assert m.has_notes_from("gemini")
        assert m.external_notes["gemini"][0]["content"] == ""

    def test_custom_content_type(self):
        m = Meeting()
        m.set_notes("gemini", "<html>rich</html>", content_type="text/html")
        assert m.external_notes["gemini"][0]["content_type"] == "text/html"

    def test_set_notes_preserves_existing_sources(self):
        m = Meeting()
        m.set_notes("granola", "g1")
        m.set_notes("fireflies", "f1")
        m.set_notes("gemini", "gem1")

        # All three should still be there
        assert set(m.external_notes.keys()) == {"granola", "fireflies", "gemini"}

    def test_many_entries_same_source(self):
        m = Meeting()
        for i in range(10):
            m.set_notes("gemini", f"version {i}", doc_id=f"doc{i}")
        assert len(m.external_notes["gemini"]) == 10
