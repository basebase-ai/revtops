"""
Email service for sending transactional emails.

Uses Resend for email delivery. Set RESEND_API_KEY in environment.
"""
from __future__ import annotations

from typing import Any, Optional

import httpx

from config import settings


async def send_email(
    to: str | list[str],
    subject: str,
    body: str,
    html: Optional[str] = None,
    from_address: Optional[str] = None,
    reply_to: Optional[str] = None,
) -> bool:
    """
    Send an email via Resend (system email).
    
    Args:
        to: Recipient email address(es)
        subject: Email subject
        body: Plain text body
        html: Optional HTML body (will be generated from body if not provided)
        from_address: Optional from address (defaults to system default)
        reply_to: Optional reply-to address
        
    Returns:
        True if email sent successfully, False otherwise
    """
    if not settings.RESEND_API_KEY:
        print(f"[Email] RESEND_API_KEY not set, skipping email to {to}")
        return False

    # Ensure to is a list
    to_list = [to] if isinstance(to, str) else to
    
    # Generate simple HTML if not provided
    if not html:
        html = f"""
        <!DOCTYPE html>
        <html>
        <head><meta charset="utf-8"></head>
        <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px;">
            {body.replace(chr(10), '<br>')}
        </body>
        </html>
        """

    payload: dict[str, Any] = {
        "from": from_address or settings.EMAIL_FROM or "Revtops <hello@revtops.com>",
        "to": to_list,
        "subject": subject,
        "html": html,
        "text": body,
    }
    
    if reply_to:
        payload["reply_to"] = reply_to

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {settings.RESEND_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=10.0,
            )
            
            if response.status_code == 200:
                print(f"[Email] Sent to {to_list}")
                return True
            else:
                print(f"[Email] Failed to send: {response.status_code} {response.text}")
                return False
                
        except Exception as e:
            print(f"[Email] Error sending: {e}")
            return False


async def send_waitlist_notification(
    applicant_email: str,
    applicant_name: str,
    waitlist_data: dict[str, Any],
) -> bool:
    """
    Send notification to support when someone joins the waitlist.
    
    Args:
        applicant_email: The applicant's email
        applicant_name: The applicant's name
        waitlist_data: The waitlist form data (title, company, etc.)
    """
    if not settings.RESEND_API_KEY:
        print(f"[Email] RESEND_API_KEY not set, skipping waitlist notification")
        return False

    apps = ", ".join(waitlist_data.get("apps_of_interest", []))
    needs = ", ".join(waitlist_data.get("core_needs", []))

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
    </head>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px;">
        <h2 style="color: #111;">New Waitlist Signup</h2>
        
        <table style="width: 100%; border-collapse: collapse;">
            <tr>
                <td style="padding: 8px 0; border-bottom: 1px solid #eee; font-weight: 600; width: 140px;">Name</td>
                <td style="padding: 8px 0; border-bottom: 1px solid #eee;">{applicant_name}</td>
            </tr>
            <tr>
                <td style="padding: 8px 0; border-bottom: 1px solid #eee; font-weight: 600;">Email</td>
                <td style="padding: 8px 0; border-bottom: 1px solid #eee;">{applicant_email}</td>
            </tr>
            <tr>
                <td style="padding: 8px 0; border-bottom: 1px solid #eee; font-weight: 600;">Title</td>
                <td style="padding: 8px 0; border-bottom: 1px solid #eee;">{waitlist_data.get("title", "—")}</td>
            </tr>
            <tr>
                <td style="padding: 8px 0; border-bottom: 1px solid #eee; font-weight: 600;">Company</td>
                <td style="padding: 8px 0; border-bottom: 1px solid #eee;">{waitlist_data.get("company_name", "—")}</td>
            </tr>
            <tr>
                <td style="padding: 8px 0; border-bottom: 1px solid #eee; font-weight: 600;">Employees</td>
                <td style="padding: 8px 0; border-bottom: 1px solid #eee;">{waitlist_data.get("num_employees", "—")}</td>
            </tr>
            <tr>
                <td style="padding: 8px 0; border-bottom: 1px solid #eee; font-weight: 600;">Apps</td>
                <td style="padding: 8px 0; border-bottom: 1px solid #eee;">{apps or "—"}</td>
            </tr>
            <tr>
                <td style="padding: 8px 0; font-weight: 600;">Needs</td>
                <td style="padding: 8px 0;">{needs or "—"}</td>
            </tr>
        </table>
    </body>
    </html>
    """

    text_content = f"""New Waitlist Signup

Name: {applicant_name}
Email: {applicant_email}
Title: {waitlist_data.get("title", "—")}
Company: {waitlist_data.get("company_name", "—")}
Employees: {waitlist_data.get("num_employees", "—")}
Apps: {apps or "—"}
Needs: {needs or "—"}
"""

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {settings.RESEND_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": settings.EMAIL_FROM or "Revtops <hello@revtops.com>",
                    "to": ["support@revtops.com"],
                    "subject": f"New waitlist signup: {applicant_name} ({waitlist_data.get('company_name', 'Unknown')})",
                    "html": html_content,
                    "text": text_content,
                },
                timeout=10.0,
            )
            
            if response.status_code == 200:
                print(f"[Email] Waitlist notification sent for {applicant_email}")
                return True
            else:
                print(f"[Email] Failed to send waitlist notification: {response.status_code} {response.text}")
                return False
                
        except Exception as e:
            print(f"[Email] Error sending waitlist notification: {e}")
            return False


async def send_waitlist_confirmation(to_email: str, name: str) -> bool:
    """
    Send confirmation email to user who signed up for the waitlist.
    
    Args:
        to_email: Recipient email address
        name: Recipient's name for personalization
        
    Returns:
        True if email sent successfully, False otherwise
    """
    if not settings.RESEND_API_KEY:
        print(f"[Email] RESEND_API_KEY not set, skipping confirmation to {to_email}")
        return False

    safe_name = name.strip() or "there"

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
    </head>
    <body style="margin: 0; padding: 0; background-color: #f4f7fb; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; color: #111827;">
        <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background-color: #f4f7fb; padding: 24px 12px;">
            <tr>
                <td align="center">
                    <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="max-width: 640px;">
                        <tr>
                            <td style="padding: 8px 8px 16px; text-align: center; font-size: 13px; color: #6b7280; letter-spacing: 0.04em; text-transform: uppercase; font-weight: 600;">
                                RevTops
                            </td>
                        </tr>
                        <tr>
                            <td style="background: #ffffff; border-radius: 16px; border: 1px solid #e5e7eb; overflow: hidden;">
                                <div style="background: linear-gradient(135deg, #6366f1 0%, #4338ca 100%); padding: 26px 24px; text-align: center;">
                                    <div style="font-size: 32px; line-height: 1; margin-bottom: 8px;">🎉</div>
                                    <h1 style="margin: 0; font-size: 26px; color: #ffffff; font-weight: 700;">You&apos;re on the list</h1>
                                </div>
                                <div style="padding: 28px 24px 18px;">
                                    <p style="margin: 0 0 14px; font-size: 16px; line-height: 1.6;">Hey {safe_name},</p>
                                    <p style="margin: 0 0 14px; font-size: 16px; line-height: 1.6; color: #374151;">Thanks for signing up for Revtops. Your waitlist spot is confirmed, and we&apos;ll email you as soon as your access is ready.</p>
                                    <p style="margin: 0 0 18px; font-size: 16px; line-height: 1.6; color: #374151;">We&apos;re building Revtops to help revenue teams unlock insights by connecting your CRM, Slack, email, and calendar through one simple chat interface.</p>
                                    <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="margin: 0 0 20px;">
                                        <tr>
                                            <td style="background: #eef2ff; border: 1px solid #c7d2fe; border-radius: 12px; padding: 14px; color: #3730a3; font-size: 14px; line-height: 1.5;">
                                                We&apos;ll follow up with launch details and next steps soon.
                                            </td>
                                        </tr>
                                    </table>
                                    <p style="margin: 0 0 8px; font-size: 16px; line-height: 1.6;">— The Revtops Team</p>
                                </div>
                                <div style="padding: 16px 24px 24px; border-top: 1px solid #e5e7eb;">
                                    <p style="margin: 0; font-size: 12px; line-height: 1.5; color: #6b7280;">You received this email because you signed up for the Revtops waitlist.</p>
                                </div>
                            </td>
                        </tr>
                    </table>
                </td>
            </tr>
        </table>
    </body>
    </html>
    """

    text_content = f"""Hey {safe_name},

Thanks for signing up for Revtops! We've added you to our waitlist and will reach out as soon as your spot is ready.

We're building Revtops to help revenue teams unlock insights by connecting your CRM, Slack, email, and calendar — all through a simple chat interface.

We'll be in touch soon with next steps.

— The Revtops Team
"""

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {settings.RESEND_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": settings.EMAIL_FROM or "Revtops <hello@revtops.com>",
                    "to": [to_email],
                    "subject": "You're on the Revtops waitlist!",
                    "html": html_content,
                    "text": text_content,
                },
                timeout=10.0,
            )
            
            if response.status_code == 200:
                print(f"[Email] Waitlist confirmation sent to {to_email}")
                return True
            else:
                print(f"[Email] Failed to send confirmation to {to_email}: {response.status_code} {response.text}")
                return False
                
        except Exception as e:
            print(f"[Email] Error sending confirmation to {to_email}: {e}")
            return False


async def send_invitation_email(to_email: str, name: str) -> bool:
    """
    Send an invitation email to a user who has been approved from the waitlist.
    
    Args:
        to_email: Recipient email address
        name: Recipient's name for personalization
        
    Returns:
        True if email sent successfully, False otherwise
    """
    if not settings.RESEND_API_KEY:
        print(f"[Email] RESEND_API_KEY not set, skipping email to {to_email}")
        return False

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
    </head>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px;">
        <div style="text-align: center; margin-bottom: 30px;">
            <div style="display: inline-block; width: 48px; height: 48px; background: linear-gradient(135deg, #6366f1, #4f46e5); border-radius: 12px; margin-bottom: 16px;"></div>
            <h1 style="margin: 0; font-size: 24px; color: #111;">You're In!</h1>
        </div>
        
        <p>Hey {name},</p>
        
        <p>Great news — you're off the waitlist! Your spot at Revtops is ready.</p>
        
        <p>Revtops connects your CRM, Slack, email, and calendar so you can chat with your revenue data and build automations that save your team hours every week.</p>
        
        <div style="text-align: center; margin: 30px 0;">
            <a href="{settings.FRONTEND_URL}" style="display: inline-block; background: linear-gradient(135deg, #6366f1, #4f46e5); color: white; text-decoration: none; padding: 14px 28px; border-radius: 8px; font-weight: 600;">Sign In to Get Started</a>
        </div>
        
        <p>Questions? Just reply to this email.</p>
        
        <p>— The Revtops Team</p>
        
        <hr style="border: none; border-top: 1px solid #eee; margin: 30px 0;">
        
        <p style="font-size: 12px; color: #666;">
            You received this email because you signed up for the Revtops waitlist.
        </p>
    </body>
    </html>
    """

    text_content = f"""
Hey {name},

Great news — you're off the waitlist! Your spot at Revtops is ready.

Revtops connects your CRM, Slack, email, and calendar so you can chat with your revenue data and build automations that save your team hours every week.

Sign in to get started: {settings.FRONTEND_URL}

Questions? Just reply to this email.

— The Revtops Team
"""

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {settings.RESEND_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": settings.EMAIL_FROM or "Revtops <hello@revtops.com>",
                    "to": [to_email],
                    "subject": "You're off the waitlist! 🎉",
                    "html": html_content,
                    "text": text_content,
                },
                timeout=10.0,
            )
            
            if response.status_code == 200:
                print(f"[Email] Invitation sent to {to_email}")
                return True
            else:
                print(f"[Email] Failed to send to {to_email}: {response.status_code} {response.text}")
                return False
                
        except Exception as e:
            print(f"[Email] Error sending to {to_email}: {e}")
            return False


async def send_org_invitation_email(
    to_email: str,
    org_name: str,
    invited_by_name: Optional[str] = None,
) -> bool:
    """
    Send an invitation email to join an organization.

    Args:
        to_email: Recipient email address
        org_name: Name of the organization they're being invited to
        invited_by_name: Name of the person who sent the invite

    Returns:
        True if email sent successfully, False otherwise
    """
    if not settings.RESEND_API_KEY:
        print(f"[Email] RESEND_API_KEY not set, skipping org invite to {to_email}")
        return False

    inviter_line: str = (
        f"<p>{invited_by_name} has invited you to join <strong>{org_name}</strong> on Revtops.</p>"
        if invited_by_name
        else f"<p>You've been invited to join <strong>{org_name}</strong> on Revtops.</p>"
    )

    html_content: str = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
    </head>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px;">
        <div style="text-align: center; margin-bottom: 30px;">
            <div style="display: inline-block; width: 48px; height: 48px; background: linear-gradient(135deg, #6366f1, #4f46e5); border-radius: 12px; margin-bottom: 16px;"></div>
            <h1 style="margin: 0; font-size: 24px; color: #111;">You're Invited!</h1>
        </div>

        {inviter_line}

        <p>Revtops connects your CRM, Slack, email, and calendar so you can chat with your revenue data and build automations that save your team hours every week.</p>

        <div style="text-align: center; margin: 30px 0;">
            <a href="{settings.FRONTEND_URL}" style="display: inline-block; background: linear-gradient(135deg, #6366f1, #4f46e5); color: white; text-decoration: none; padding: 14px 28px; border-radius: 8px; font-weight: 600;">Accept Invitation</a>
        </div>

        <p>Questions? Just reply to this email.</p>

        <p>— The Revtops Team</p>

        <hr style="border: none; border-top: 1px solid #eee; margin: 30px 0;">

        <p style="font-size: 12px; color: #666;">
            You received this email because someone invited you to {org_name} on Revtops.
        </p>
    </body>
    </html>
    """

    inviter_text: str = (
        f"{invited_by_name} has invited you to join {org_name} on Revtops."
        if invited_by_name
        else f"You've been invited to join {org_name} on Revtops."
    )

    text_content: str = f"""
{inviter_text}

Revtops connects your CRM, Slack, email, and calendar so you can chat with your revenue data and build automations.

Accept the invitation: {settings.FRONTEND_URL}

Questions? Just reply to this email.

— The Revtops Team
"""

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {settings.RESEND_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": settings.EMAIL_FROM or "Revtops <hello@revtops.com>",
                    "to": [to_email],
                    "subject": f"You're invited to {org_name} on Revtops",
                    "html": html_content,
                    "text": text_content,
                },
                timeout=10.0,
            )

            if response.status_code == 200:
                print(f"[Email] Org invitation sent to {to_email} for org {org_name}")
                return True
            else:
                print(f"[Email] Failed to send org invite to {to_email}: {response.status_code} {response.text}")
                return False

        except Exception as e:
            print(f"[Email] Error sending org invite to {to_email}: {e}")
            return False
