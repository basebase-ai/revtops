"""
Microsoft Mail connector implementation via Microsoft Graph API.

Responsibilities:
- Authenticate with Microsoft using OAuth token (via Nango)
- Fetch emails from Outlook
- Normalize email data to activity records
- Handle pagination
"""

import uuid
from datetime import datetime, timedelta
from typing import Any, Optional

import httpx

from connectors.base import BaseConnector
from connectors.registry import (
    AuthType, Capability, ConnectorAction, ConnectorMeta, ConnectorScope,
)
from models.activity import Activity
from models.database import get_session

MICROSOFT_GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"


class MicrosoftMailConnector(BaseConnector):
    """Connector for Microsoft Outlook Mail data via Microsoft Graph."""

    source_system = "microsoft_mail"
    meta = ConnectorMeta(
        name="Microsoft Mail",
        slug="microsoft_mail",
        auth_type=AuthType.OAUTH2,
        scope=ConnectorScope.USER,
        entity_types=["activities"],
        capabilities=[Capability.SYNC, Capability.ACTION],
        actions=[
            ConnectorAction(
                name="send_email",
                description="Send an email via the user's connected Microsoft Outlook account.",
                parameters=[
                    {"name": "to", "type": "string", "required": True, "description": "Recipient email address"},
                    {"name": "subject", "type": "string", "required": True, "description": "Email subject line"},
                    {"name": "body", "type": "string", "required": True, "description": "Email body (plain text)"},
                    {"name": "cc", "type": "array", "required": False, "description": "CC recipients"},
                    {"name": "bcc", "type": "array", "required": False, "description": "BCC recipients"},
                ],
            ),
        ],
        nango_integration_id="microsoft-mail",
        description="Microsoft Outlook Mail – email sync and send",
    )

    async def _get_headers(self) -> dict[str, str]:
        """Get authorization headers for Microsoft Graph API."""
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
        """Make an authenticated request to Microsoft Graph API."""
        headers = await self._get_headers()
        url = f"{MICROSOFT_GRAPH_API_BASE}{endpoint}"

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

    async def get_mail_folders(self) -> list[dict[str, Any]]:
        """Get list of mail folders."""
        folders: list[dict[str, Any]] = []
        next_link: Optional[str] = None

        while True:
            if next_link:
                async with httpx.AsyncClient() as client:
                    headers = await self._get_headers()
                    response = await client.get(next_link, headers=headers, timeout=30.0)
                    response.raise_for_status()
                    data = response.json()
            else:
                data = await self._make_request("GET", "/me/mailFolders")

            folders.extend(data.get("value", []))

            next_link = data.get("@odata.nextLink")
            if not next_link:
                break

        return folders

    async def get_emails(
        self,
        folder_id: Optional[str] = None,
        received_after: Optional[datetime] = None,
        received_before: Optional[datetime] = None,
        max_results: int = 100,
    ) -> list[dict[str, Any]]:
        """Get emails from inbox or a specific folder."""
        if received_after is None:
            received_after = datetime.utcnow() - timedelta(days=30)
        if received_before is None:
            received_before = datetime.utcnow()

        emails: list[dict[str, Any]] = []
        next_link: Optional[str] = None

        # Build endpoint - use inbox if no folder_id provided
        if folder_id:
            endpoint = f"/me/mailFolders/{folder_id}/messages"
        else:
            endpoint = "/me/messages"

        while len(emails) < max_results:
            if next_link:
                async with httpx.AsyncClient() as client:
                    headers = await self._get_headers()
                    response = await client.get(next_link, headers=headers, timeout=30.0)
                    response.raise_for_status()
                    data = response.json()
            else:
                params: dict[str, Any] = {
                    "$top": min(50, max_results - len(emails)),
                    "$orderby": "receivedDateTime desc",
                    "$filter": (
                        f"receivedDateTime ge {received_after.isoformat()}Z and "
                        f"receivedDateTime le {received_before.isoformat()}Z"
                    ),
                    "$select": (
                        "id,subject,bodyPreview,from,toRecipients,ccRecipients,"
                        "receivedDateTime,sentDateTime,hasAttachments,importance,"
                        "isRead,isDraft,conversationId,internetMessageId"
                    ),
                }
                data = await self._make_request("GET", endpoint, params=params)

            emails.extend(data.get("value", []))

            next_link = data.get("@odata.nextLink")
            if not next_link:
                break

        return emails

    async def sync_deals(self) -> int:
        """Microsoft Mail doesn't have deals - return 0."""
        return 0

    async def sync_accounts(self) -> int:
        """Microsoft Mail doesn't have accounts - return 0."""
        return 0

    async def sync_contacts(self) -> int:
        """Microsoft Mail doesn't have contacts - return 0."""
        return 0

    async def sync_activities(self) -> int:
        """
        Sync Microsoft Mail emails as activities.

        This captures email activity that can be correlated
        with deals and accounts.
        """
        # Get emails from the last 30 days
        received_after = datetime.utcnow() - timedelta(days=30)
        received_before = datetime.utcnow()

        emails = await self.get_emails(
            received_after=received_after,
            received_before=received_before,
            max_results=500,
        )

        count = 0
        async with get_session(organization_id=self.organization_id) as session:
            for email in emails:
                activity = self._normalize_email(email)
                if activity:
                    await session.merge(activity)
                    count += 1

            await session.commit()

        return count

    def _normalize_email(self, ms_email: dict[str, Any]) -> Optional[Activity]:
        """Transform Microsoft Mail email to our Activity model."""
        email_id: str = ms_email.get("id", "")
        subject: str = ms_email.get("subject", "")
        body_preview: str = ms_email.get("bodyPreview", "")

        # Skip drafts
        if ms_email.get("isDraft"):
            return None

        # Parse received time
        activity_date: Optional[datetime] = None
        received_dt: Optional[str] = ms_email.get("receivedDateTime")
        if received_dt:
            try:
                activity_date = datetime.fromisoformat(received_dt.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass

        # Extract sender
        from_info = ms_email.get("from", {})
        from_email_obj = from_info.get("emailAddress", {})
        from_email: Optional[str] = from_email_obj.get("address")
        from_name: Optional[str] = from_email_obj.get("name")

        # Extract recipients
        to_recipients: list[dict[str, Any]] = ms_email.get("toRecipients", [])
        to_emails: list[str] = []
        for recipient in to_recipients:
            email_addr = recipient.get("emailAddress", {})
            addr = email_addr.get("address")
            if addr:
                to_emails.append(addr)

        cc_recipients: list[dict[str, Any]] = ms_email.get("ccRecipients", [])
        cc_emails: list[str] = []
        for recipient in cc_recipients:
            email_addr = recipient.get("emailAddress", {})
            addr = email_addr.get("address")
            if addr:
                cc_emails.append(addr)

        return Activity(
            id=uuid.uuid4(),
            organization_id=uuid.UUID(self.organization_id),
            source_system=self.source_system,
            source_id=email_id,
            type="email",
            subject=subject or "(No Subject)",
            description=body_preview[:2000] if body_preview else None,
            activity_date=activity_date,
            custom_fields={
                "from_email": from_email,
                "from_name": from_name,
                "to_emails": to_emails[:10],
                "cc_emails": cc_emails[:5],
                "recipient_count": len(to_recipients) + len(cc_recipients),
                "has_attachments": ms_email.get("hasAttachments", False),
                "importance": ms_email.get("importance"),
                "is_read": ms_email.get("isRead", False),
                "conversation_id": ms_email.get("conversationId"),
                "internet_message_id": ms_email.get("internetMessageId"),
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
        """Microsoft Mail doesn't have deals."""
        return {"error": "Microsoft Mail does not support deals"}

    async def execute_action(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        """Execute a side-effect action."""
        if action == "send_email":
            return await self.send_email(
                to=params["to"],
                subject=params["subject"],
                body=params["body"],
                cc=params.get("cc"),
                bcc=params.get("bcc"),
                reply_to=params.get("reply_to"),
                save_to_sent=params.get("save_to_sent", True),
            )
        raise ValueError(f"Unknown action: {action}")

    async def send_email(
        self,
        to: str | list[str],
        subject: str,
        body: str,
        cc: Optional[list[str]] = None,
        bcc: Optional[list[str]] = None,
        reply_to: Optional[str] = None,
        save_to_sent: bool = True,
    ) -> dict[str, Any]:
        """
        Send an email via the user's Microsoft/Outlook account.
        
        Args:
            to: Recipient email address(es)
            subject: Email subject
            body: Email body (plain text)
            cc: Optional CC recipients
            bcc: Optional BCC recipients
            reply_to: Optional reply-to address
            save_to_sent: Whether to save to Sent Items (default True)
            
        Returns:
            Dict with success status and message details
        """
        # Build recipients list
        to_list = [to] if isinstance(to, str) else to
        
        # Build recipient objects
        to_recipients = [
            {"emailAddress": {"address": addr}} for addr in to_list
        ]
        
        cc_recipients = []
        if cc:
            cc_recipients = [{"emailAddress": {"address": addr}} for addr in cc]
        
        bcc_recipients = []
        if bcc:
            bcc_recipients = [{"emailAddress": {"address": addr}} for addr in bcc]
        
        # Build message payload
        message_payload: dict[str, Any] = {
            "message": {
                "subject": subject,
                "body": {
                    "contentType": "Text",
                    "content": body,
                },
                "toRecipients": to_recipients,
            },
            "saveToSentItems": save_to_sent,
        }
        
        if cc_recipients:
            message_payload["message"]["ccRecipients"] = cc_recipients
        if bcc_recipients:
            message_payload["message"]["bccRecipients"] = bcc_recipients
        if reply_to:
            message_payload["message"]["replyTo"] = [
                {"emailAddress": {"address": reply_to}}
            ]
        
        headers = await self._get_headers()
        url = f"{MICROSOFT_GRAPH_API_BASE}/me/sendMail"
        
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    url,
                    headers=headers,
                    json=message_payload,
                    timeout=30.0,
                )
                
                # Microsoft returns 202 Accepted on success (no body)
                if response.status_code == 202:
                    return {
                        "success": True,
                        "status": "sent",
                        "to": to_list,
                    }
                
                response.raise_for_status()
                return {
                    "success": True,
                    "status": "sent",
                    "to": to_list,
                }
                
            except httpx.HTTPStatusError as e:
                error_msg = e.response.text if e.response else str(e)
                return {
                    "success": False,
                    "error": f"Microsoft Graph API error: {error_msg}",
                }
            except Exception as e:
                return {
                    "success": False,
                    "error": str(e),
                }
