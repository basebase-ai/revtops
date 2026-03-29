from __future__ import annotations

import base64
import email
from urllib.parse import parse_qs

import pytest

from connectors.gmail import GmailConnector
from connectors.microsoft_mail import MicrosoftMailConnector
from services.automated_agent_footer import AUTOMATED_AGENT_FOOTER
from services.email import send_email
from services.sms import send_sms


@pytest.mark.asyncio
async def test_send_email_applies_footer_right_before_send(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_payload: dict[str, object] = {}

    class _MockResponse:
        status_code = 200
        text = "ok"

    class _MockClient:
        async def __aenter__(self) -> _MockClient:
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

        async def post(self, url: str, headers: dict[str, str], json: dict[str, object], timeout: float) -> _MockResponse:
            captured_payload.update(json)
            return _MockResponse()

    monkeypatch.setattr("services.email.settings.RESEND_API_KEY", "test-key")
    monkeypatch.setattr("services.email.httpx.AsyncClient", _MockClient)

    success = await send_email(
        to="test@example.com",
        subject="subject",
        body="Hello there",
    )

    assert success is True
    sent_text = str(captured_payload["text"])
    sent_html = str(captured_payload["html"])
    assert AUTOMATED_AGENT_FOOTER in sent_text
    assert AUTOMATED_AGENT_FOOTER in sent_html


@pytest.mark.asyncio
async def test_send_sms_applies_footer_right_before_send(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_content: str = ""

    class _MockResponse:
        status_code = 201

        @staticmethod
        def json() -> dict[str, str]:
            return {"sid": "SM123", "status": "queued"}

    class _MockClient:
        async def __aenter__(self) -> _MockClient:
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

        async def post(self, url: str, headers: dict[str, str], content: str, timeout: float) -> _MockResponse:
            nonlocal captured_content
            captured_content = content
            return _MockResponse()

    monkeypatch.setattr("services.sms.settings.TWILIO_ACCOUNT_SID", "AC123")
    monkeypatch.setattr("services.sms.settings.TWILIO_AUTH_TOKEN", "tok")
    monkeypatch.setattr("services.sms.settings.TWILIO_PHONE_NUMBER", "+15550001111")
    monkeypatch.setattr("services.sms.httpx.AsyncClient", _MockClient)

    result = await send_sms(
        to="+15550002222",
        body="Hello via SMS",
        allow_unverified=True,
    )

    assert result["success"] is True
    body_values = parse_qs(captured_content).get("Body", [])
    assert body_values, "Body should be present in Twilio form payload"
    assert AUTOMATED_AGENT_FOOTER in body_values[0]


@pytest.mark.asyncio
async def test_gmail_connector_send_email_applies_footer(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_json: dict[str, object] = {}

    class _MockResponse:
        status_code = 202

        def raise_for_status(self) -> None:
            return None

        @staticmethod
        def json() -> dict[str, object]:
            return {"id": "mid_1", "threadId": "tid_1", "labelIds": ["SENT"]}

    class _MockClient:
        async def __aenter__(self) -> _MockClient:
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

        async def post(self, url: str, headers: dict[str, str], json: dict[str, object], timeout: float) -> _MockResponse:
            captured_json.update(json)
            return _MockResponse()

    async def _mock_get_headers(self: GmailConnector) -> dict[str, str]:
        return {"Authorization": "Bearer test", "Content-Type": "application/json"}

    monkeypatch.setattr("connectors.gmail.httpx.AsyncClient", _MockClient)
    monkeypatch.setattr(GmailConnector, "_get_headers", _mock_get_headers)

    connector = GmailConnector(
        "00000000-0000-0000-0000-000000000001",
        user_id="00000000-0000-0000-0000-000000000002",
    )
    result = await connector.send_email(
        to="recipient@example.com",
        subject="hello",
        body="Message body",
    )

    assert result["success"] is True
    raw_message = str(captured_json["raw"])
    parsed = email.message_from_bytes(base64.urlsafe_b64decode(raw_message.encode("utf-8")))
    plain_parts = [p for p in parsed.walk() if p.get_content_type() == "text/plain"]
    assert plain_parts
    body_text = plain_parts[0].get_payload(decode=True).decode("utf-8")
    assert AUTOMATED_AGENT_FOOTER in body_text


@pytest.mark.asyncio
async def test_microsoft_connector_send_email_applies_footer(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_json: dict[str, object] = {}

    class _MockResponse:
        status_code = 202

        def raise_for_status(self) -> None:
            return None

        @staticmethod
        def json() -> dict[str, object]:
            return {}

    class _MockClient:
        async def __aenter__(self) -> _MockClient:
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

        async def post(self, url: str, headers: dict[str, str], json: dict[str, object], timeout: float) -> _MockResponse:
            captured_json.update(json)
            return _MockResponse()

    async def _mock_get_headers(self: MicrosoftMailConnector) -> dict[str, str]:
        return {"Authorization": "Bearer test", "Content-Type": "application/json"}

    monkeypatch.setattr("connectors.microsoft_mail.httpx.AsyncClient", _MockClient)
    monkeypatch.setattr(MicrosoftMailConnector, "_get_headers", _mock_get_headers)

    connector = MicrosoftMailConnector(
        "00000000-0000-0000-0000-000000000001",
        user_id="00000000-0000-0000-0000-000000000002",
    )
    result = await connector.send_email(
        to="recipient@example.com",
        subject="hello",
        body="Message body",
    )

    assert result["success"] is True
    payload_body = ((captured_json.get("message") or {}).get("body") or {})
    assert AUTOMATED_AGENT_FOOTER in str(payload_body.get("content", ""))
