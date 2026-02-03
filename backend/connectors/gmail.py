"""
Gmail connector implementation via Google Gmail API.

Responsibilities:
- Authenticate with Google using OAuth token (via Nango)
- Fetch emails from Gmail
- Normalize email data to activity records
- Handle pagination
"""

import base64
import uuid
from datetime import datetime, timedelta
from typing import Any, Optional

import httpx

from connectors.base import BaseConnector
from models.activity import Activity
from models.database import get_session

GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1"


class GmailConnector(BaseConnector):
    """Connector for Gmail data."""

    source_system = "gmail"

    async def _get_headers(self) -> dict[str, str]:
        """Get authorization headers for Gmail API."""
        token, _ = await self.get_oauth_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    async def _make_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Make an authenticated request to Gmail API."""
        headers = await self._get_headers()
        url = f"{GMAIL_API_BASE}{endpoint}"

        async with httpx.AsyncClient() as client:
            response = await client.request(
                method=method,
                url=url,
                headers=headers,
                params=params,
                timeout=30.0,
            )
            response.raise_for_status()
            return response.json()

    async def get_labels(self) -> list[dict[str, Any]]:
        """Get list of Gmail labels."""
        data = await self._make_request("GET", "/users/me/labels")
        return data.get("labels", [])

    async def get_messages(
        self,
        label_ids: Optional[list[str]] = None,
        after: Optional[datetime] = None,
        before: Optional[datetime] = None,
        max_results: int = 100,
    ) -> list[dict[str, Any]]:
        """Get message list from Gmail."""
        if after is None:
            after = datetime.utcnow() - timedelta(days=30)
        if before is None:
            before = datetime.utcnow()

        # Build query string
        query_parts: list[str] = []
        query_parts.append(f"after:{int(after.timestamp())}")
        query_parts.append(f"before:{int(before.timestamp())}")
        query = " ".join(query_parts)

        messages: list[dict[str, Any]] = []
        page_token: Optional[str] = None

        while len(messages) < max_results:
            params: dict[str, Any] = {
                "maxResults": min(100, max_results - len(messages)),
                "q": query,
            }
            if label_ids:
                params["labelIds"] = ",".join(label_ids)
            if page_token:
                params["pageToken"] = page_token

            data = await self._make_request("GET", "/users/me/messages", params=params)
            
            # Get message IDs
            message_list = data.get("messages", [])
            
            # Fetch full message details for each
            for msg_summary in message_list:
                if len(messages) >= max_results:
                    break
                msg_id = msg_summary.get("id")
                if msg_id:
                    try:
                        full_msg = await self._get_message_detail(msg_id)
                        messages.append(full_msg)
                    except Exception as e:
                        print(f"Failed to fetch message {msg_id}: {e}")

            page_token = data.get("nextPageToken")
            if not page_token:
                break

        return messages

    async def _get_message_detail(self, message_id: str) -> dict[str, Any]:
        """Get full message details."""
        params = {"format": "metadata", "metadataHeaders": ["From", "To", "Cc", "Subject", "Date"]}
        return await self._make_request("GET", f"/users/me/messages/{message_id}", params=params)

    async def sync_deals(self) -> int:
        """Gmail doesn't have deals - return 0."""
        return 0

    async def sync_accounts(self) -> int:
        """Gmail doesn't have accounts - return 0."""
        return 0

    async def sync_contacts(self) -> int:
        """Gmail doesn't have contacts - return 0."""
        return 0

    async def sync_activities(self) -> int:
        """
        Sync Gmail emails as activities.

        This captures email activity that can be correlated
        with deals and accounts.
        """
        # Get emails from the last 30 days
        after = datetime.utcnow() - timedelta(days=30)
        before = datetime.utcnow()

        messages = await self.get_messages(
            after=after,
            before=before,
            max_results=500,
        )

        count = 0
        async with get_session(organization_id=self.organization_id) as session:
            for message in messages:
                activity = self._normalize_message(message)
                if activity:
                    await session.merge(activity)
                    count += 1

            await session.commit()

        return count

    def _normalize_message(self, gmail_msg: dict[str, Any]) -> Optional[Activity]:
        """Transform Gmail message to our Activity model."""
        msg_id: str = gmail_msg.get("id", "")
        
        # Extract headers
        headers = gmail_msg.get("payload", {}).get("headers", [])
        header_dict: dict[str, str] = {}
        for header in headers:
            name = header.get("name", "").lower()
            value = header.get("value", "")
            header_dict[name] = value

        subject = header_dict.get("subject", "(No Subject)")
        from_header = header_dict.get("from", "")
        to_header = header_dict.get("to", "")
        cc_header = header_dict.get("cc", "")
        date_header = header_dict.get("date", "")

        # Parse from email
        from_email: Optional[str] = None
        from_name: Optional[str] = None
        if "<" in from_header and ">" in from_header:
            # Format: "Name <email@example.com>"
            parts = from_header.split("<")
            from_name = parts[0].strip().strip('"')
            from_email = parts[1].rstrip(">").strip()
        else:
            from_email = from_header.strip()

        # Parse to emails
        to_emails: list[str] = []
        for addr in to_header.split(","):
            addr = addr.strip()
            if "<" in addr and ">" in addr:
                email = addr.split("<")[1].rstrip(">").strip()
                to_emails.append(email)
            elif addr:
                to_emails.append(addr)

        # Parse cc emails
        cc_emails: list[str] = []
        for addr in cc_header.split(","):
            addr = addr.strip()
            if "<" in addr and ">" in addr:
                email = addr.split("<")[1].rstrip(">").strip()
                cc_emails.append(email)
            elif addr:
                cc_emails.append(addr)

        # Parse date
        activity_date: Optional[datetime] = None
        internal_date = gmail_msg.get("internalDate")
        if internal_date:
            try:
                # internalDate is in milliseconds
                activity_date = datetime.utcfromtimestamp(int(internal_date) / 1000)
            except (ValueError, TypeError):
                pass

        # Get snippet as description
        snippet = gmail_msg.get("snippet", "")

        # Get labels
        label_ids = gmail_msg.get("labelIds", [])
        is_unread = "UNREAD" in label_ids
        is_sent = "SENT" in label_ids
        has_attachments = any(
            part.get("filename") 
            for part in gmail_msg.get("payload", {}).get("parts", [])
        )

        return Activity(
            id=uuid.uuid4(),
            organization_id=uuid.UUID(self.organization_id),
            source_system=self.source_system,
            source_id=msg_id,
            type="email",
            subject=subject,
            description=snippet[:2000] if snippet else None,
            activity_date=activity_date,
            custom_fields={
                "from_email": from_email,
                "from_name": from_name,
                "to_emails": to_emails[:10],
                "cc_emails": cc_emails[:5],
                "recipient_count": len(to_emails) + len(cc_emails),
                "has_attachments": has_attachments,
                "is_unread": is_unread,
                "is_sent": is_sent,
                "labels": label_ids[:10],
                "thread_id": gmail_msg.get("threadId"),
            },
        )

    async def sync_all(self) -> dict[str, int]:
        """Run all sync operations."""
        activities_count = await self.sync_activities()

        return {
            "accounts": 0,
            "deals": 0,
            "contacts": 0,
            "activities": activities_count,
        }

    async def fetch_deal(self, deal_id: str) -> dict[str, Any]:
        """Gmail doesn't have deals."""
        return {"error": "Gmail does not support deals"}

    async def send_email(
        self,
        to: str | list[str],
        subject: str,
        body: str,
        cc: Optional[list[str]] = None,
        bcc: Optional[list[str]] = None,
        reply_to: Optional[str] = None,
        thread_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Send an email via the user's Gmail account.
        
        Args:
            to: Recipient email address(es)
            subject: Email subject
            body: Email body (plain text)
            cc: Optional CC recipients
            bcc: Optional BCC recipients
            reply_to: Optional reply-to address
            thread_id: Optional thread ID to reply in thread
            
        Returns:
            Dict with id, threadId on success, or error on failure
        """
        import email.mime.text
        import email.mime.multipart
        
        # Build recipients list
        to_list = [to] if isinstance(to, str) else to
        
        # Create message
        message = email.mime.multipart.MIMEMultipart()
        message["To"] = ", ".join(to_list)
        message["Subject"] = subject
        
        if cc:
            message["Cc"] = ", ".join(cc)
        if bcc:
            message["Bcc"] = ", ".join(bcc)
        if reply_to:
            message["Reply-To"] = reply_to
        
        # Attach body
        message.attach(email.mime.text.MIMEText(body, "plain"))
        
        # Encode to base64url
        raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
        
        # Build request body
        request_body: dict[str, Any] = {"raw": raw_message}
        if thread_id:
            request_body["threadId"] = thread_id
        
        headers = await self._get_headers()
        url = f"{GMAIL_API_BASE}/users/me/messages/send"
        
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    url,
                    headers=headers,
                    json=request_body,
                    timeout=30.0,
                )
                response.raise_for_status()
                data = response.json()
                
                return {
                    "success": True,
                    "id": data.get("id"),
                    "threadId": data.get("threadId"),
                    "labelIds": data.get("labelIds", []),
                }
                
            except httpx.HTTPStatusError as e:
                error_msg = e.response.text if e.response else str(e)
                return {
                    "success": False,
                    "error": f"Gmail API error: {error_msg}",
                }
            except Exception as e:
                return {
                    "success": False,
                    "error": str(e),
                }
