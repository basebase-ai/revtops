"""
Email service for sending transactional emails.

Uses Resend for email delivery. Set RESEND_API_KEY in environment.
"""
from __future__ import annotations

from typing import Any, Optional

import httpx

from config import settings


def _resend_request_succeeded(status_code: int) -> bool:
    """Return whether a Resend API response should be considered successful."""
    return 200 <= status_code < 300


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
            
            if _resend_request_succeeded(response.status_code):
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
            
            if _resend_request_succeeded(response.status_code):
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
            <h1 style="margin: 0; font-size: 24px; color: #111;">You're on the list!</h1>
        </div>
        
        <p>Hey {name},</p>
        
        <p>Thanks for signing up for Revtops! We've added you to our waitlist and will reach out as soon as your spot is ready.</p>
        
        <p>We're building Revtops to help revenue teams unlock insights by connecting your CRM, Slack, email, and calendar — all through a simple chat interface.</p>
        
        <p>We'll be in touch soon with next steps.</p>
        
        <p>— The Revtops Team</p>
        
        <hr style="border: none; border-top: 1px solid #eee; margin: 30px 0;">
        
        <p style="font-size: 12px; color: #666;">
            You received this email because you signed up for the Revtops waitlist.
        </p>
    </body>
    </html>
    """

    text_content = f"""Hey {name},

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
            
            if _resend_request_succeeded(response.status_code):
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
            
            if _resend_request_succeeded(response.status_code):
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
        f"{invited_by_name} has invited you to join <strong>{org_name}</strong> on Revtops."
        if invited_by_name
        else f"You've been invited to join <strong>{org_name}</strong> on Revtops."
    )

    html_content: str = f"""
<!DOCTYPE html>
<html lang="en" xmlns="http://www.w3.org/1999/xhtml">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>You're invited to Revtops</title>
</head>
<body style="margin:0;padding:0;background:#0b0f1a;color:#e5e7eb;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#0b0f1a;padding:24px 12px;">
    <tr>
      <td align="center">
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:640px;background:linear-gradient(160deg,#111827,#0f172a);border:1px solid #1f2937;border-radius:20px;overflow:hidden;">
          <tr>
            <td style="padding:36px 32px 12px;text-align:center;">
              <img
                src="https://app.revtops.com/logo.svg"
                alt="Revtops"
                width="52"
                height="52"
                style="display:inline-block;height:52px;width:52px;border-radius:14px;"
              />
              <h1 style="margin:18px 0 8px;font-size:30px;line-height:1.2;color:#f8fafc;font-weight:700;">You're invited</h1>
              <p style="margin:0;color:#94a3b8;font-size:15px;line-height:1.6;">{inviter_line}</p>
            </td>
          </tr>
          <tr>
            <td style="padding:20px 32px 0;">
              <div style="background:#0b1222;border:1px solid #1e293b;border-radius:14px;padding:18px;">
                <p style="margin:0 0 8px;color:#cbd5e1;font-size:14px;line-height:1.5;">Invitation sent to:</p>
                <p style="margin:0;color:#f8fafc;font-size:16px;line-height:1.5;font-weight:600;">{to_email}</p>
                <p style="margin:10px 0 0;color:#94a3b8;font-size:13px;line-height:1.6;">Organization: <strong style="color:#f8fafc;">{org_name}</strong></p>
              </div>
            </td>
          </tr>
          <tr>
            <td style="padding:28px 32px 8px;text-align:center;">
              <a href="{settings.FRONTEND_URL}" style="display:inline-block;background:linear-gradient(135deg,#6366f1,#4f46e5);color:#ffffff;text-decoration:none;font-size:16px;font-weight:700;padding:14px 28px;border-radius:12px;">Accept invitation</a>
            </td>
          </tr>
          <tr>
            <td style="padding:8px 32px 30px;text-align:center;">
              <p style="margin:0;color:#64748b;font-size:13px;line-height:1.6;">If the button doesn&apos;t work, copy and paste this link into your browser:</p>
              <p style="margin:8px 0 0;color:#94a3b8;font-size:12px;line-height:1.6;word-break:break-all;">{settings.FRONTEND_URL}</p>
            </td>
          </tr>
          <tr>
            <td style="padding:20px 32px;border-top:1px solid #1f2937;background:#0b1222;">
              <p style="margin:0;color:#64748b;font-size:12px;line-height:1.6;">You received this email because someone invited you to {org_name} on Revtops. If this wasn&apos;t expected, you can safely ignore this message.</p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
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

            if _resend_request_succeeded(response.status_code):
                print(f"[Email] Org invitation sent to {to_email} for org {org_name}")
                return True
            else:
                print(f"[Email] Failed to send org invite to {to_email}: {response.status_code} {response.text}")
                return False

        except Exception as e:
            print(f"[Email] Error sending org invite to {to_email}: {e}")
            return False
