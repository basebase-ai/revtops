"""Tests for _share_gemini_doc — sharing Gemini summary docs with org domain
and external meeting participants.

This test file extracts and executes just the _share_gemini_doc function
from workers/tasks/sync.py to avoid the heavy import chain (Celery, Pydantic,
SQLAlchemy, etc.) that isn't needed to test pure HTTP sharing logic.
Full integration tests run in CI with all dependencies installed.
"""

import asyncio
import logging
import re
import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# ── Extract and compile _share_gemini_doc from the sync module source ──
_sync_source = Path(__file__).resolve().parent.parent / "workers" / "tasks" / "sync.py"
_full_source = _sync_source.read_text()

# Pull out the function via regex (it ends at the next top-level 'async def' or 'def')
_match = re.search(
    r"^(async def _share_gemini_doc\(.*?)(?=\n(?:async )?def |\nclass |\Z)",
    _full_source,
    re.MULTILINE | re.DOTALL,
)
assert _match, "_share_gemini_doc not found in sync.py"

_func_source = _match.group(1)

# Compile and execute in a minimal namespace with just logging
_ns = {"logger": logging.getLogger("test_share_gemini_doc")}
exec(compile(_func_source, str(_sync_source), "exec"), _ns)
_share_gemini_doc = _ns["_share_gemini_doc"]


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _resp(status_code=200):
    r = MagicMock()
    r.status_code = status_code
    r.text = ""
    return r


HEADERS = {"Authorization": "Bearer tok"}


class TestShareGeminiDoc:
    def test_shares_with_organizer_domain(self):
        client = AsyncMock()
        client.post.return_value = _resp(200)

        _run(_share_gemini_doc(client, HEADERS, "doc1", "alice@acme.com", [], "m1"))

        assert client.post.call_count == 1
        body = client.post.call_args.kwargs["json"]
        assert body == {"type": "domain", "role": "reader", "domain": "acme.com"}

    def test_shares_with_external_participants(self):
        client = AsyncMock()
        client.post.return_value = _resp(200)

        _run(_share_gemini_doc(
            client, HEADERS, "doc1", "alice@acme.com",
            ["bob@acme.com", "charlie@external.com", "dana@other.org"], "m1",
        ))

        # 1 domain + 2 external (bob@acme.com is internal, skipped)
        assert client.post.call_count == 3
        user_calls = [
            c for c in client.post.call_args_list
            if c.kwargs["json"].get("type") == "user"
        ]
        shared_emails = {c.kwargs["json"]["emailAddress"] for c in user_calls}
        assert shared_emails == {"charlie@external.com", "dana@other.org"}

    def test_skips_organizer_in_external_list(self):
        client = AsyncMock()
        client.post.return_value = _resp(200)

        _run(_share_gemini_doc(
            client, HEADERS, "doc1", "alice@acme.com",
            ["alice@acme.com", "bob@external.com"], "m1",
        ))

        # 1 domain + 1 external (alice is organizer & internal)
        assert client.post.call_count == 2

    def test_no_crash_on_missing_domain(self):
        client = AsyncMock()

        _run(_share_gemini_doc(client, HEADERS, "doc1", "nodomain", [], "m1"))
        assert client.post.call_count == 0

    def test_domain_failure_does_not_block_user_shares(self):
        client = AsyncMock()
        client.post.side_effect = [_resp(403), _resp(200)]

        _run(_share_gemini_doc(
            client, HEADERS, "doc1", "alice@acme.com", ["ext@other.com"], "m1",
        ))

        assert client.post.call_count == 2

    def test_exception_does_not_propagate(self):
        client = AsyncMock()
        client.post.side_effect = Exception("network error")

        # Must not raise
        _run(_share_gemini_doc(
            client, HEADERS, "doc1", "alice@acme.com", ["ext@other.com"], "m1",
        ))

    def test_no_notification_emails_sent(self):
        client = AsyncMock()
        client.post.return_value = _resp(200)

        _run(_share_gemini_doc(
            client, HEADERS, "doc1", "alice@acme.com", ["ext@other.com"], "m1",
        ))

        for call in client.post.call_args_list:
            assert call.kwargs["params"]["sendNotificationEmail"] == "false"

    def test_case_insensitive_domain_match(self):
        client = AsyncMock()
        client.post.return_value = _resp(200)

        _run(_share_gemini_doc(
            client, HEADERS, "doc1", "alice@Acme.COM",
            ["bob@acme.com", "ext@other.com"], "m1",
        ))

        # bob is internal (case-insensitive match), so 1 domain + 1 external
        assert client.post.call_count == 2
