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
        "from": from_address or settings.EMAIL_FROM or "Basebase <hello@basebase.com>",
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
                    "from": settings.EMAIL_FROM or "Basebase <hello@basebase.com>",
                    "to": ["support@basebase.com"],
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
        
        <p>Thanks for signing up for Basebase! We've added you to our waitlist and will reach out as soon as your spot is ready.</p>
        
        <p>We're building Basebase to help revenue teams unlock insights by connecting your CRM, Slack, email, and calendar — all through a simple chat interface.</p>
        
        <p>We'll be in touch soon with next steps.</p>
        
        <p>— The Basebase Team</p>
        
        <hr style="border: none; border-top: 1px solid #eee; margin: 30px 0;">
        
        <p style="font-size: 12px; color: #666;">
            You received this email because you signed up for the Basebase waitlist.
        </p>
    </body>
    </html>
    """

    text_content = f"""Hey {name},

Thanks for signing up for Basebase! We've added you to our waitlist and will reach out as soon as your spot is ready.

We're building Basebase to help revenue teams unlock insights by connecting your CRM, Slack, email, and calendar — all through a simple chat interface.

We'll be in touch soon with next steps.

— The Basebase Team
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
                    "from": settings.EMAIL_FROM or "Basebase <hello@basebase.com>",
                    "to": [to_email],
                    "subject": "You're on the Basebase waitlist!",
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
    from urllib.parse import quote

    if not settings.RESEND_API_KEY:
        print(f"[Email] RESEND_API_KEY not set, skipping email to {to_email}")
        return False

    # Link to sign-up with email prepopulated (same pattern as org invitations)
    invite_params: list[str] = ["invite=1", "org_name=Basebase", f"email={quote(to_email)}"]
    base: str = settings.FRONTEND_URL.rstrip("/")
    invite_url = f"{base}?{'&'.join(invite_params)}"

    html_content: str = f"""
<!DOCTYPE html>
<html lang="en" xmlns="http://www.w3.org/1999/xhtml">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>You&apos;re off the waitlist!</title>
</head>
<body style="margin:0;padding:0;background:#f8f9fa;color:#1a1a1a;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f8f9fa;padding:32px 12px;">
    <tr>
      <td align="center">
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:560px;background:#ffffff;border:1px solid #e5e7eb;border-radius:16px;overflow:hidden;">
          <tr>
            <td style="padding:40px 36px 0;text-align:center;">
              <img
                src="https://www.basebase.com/basebase_logo-512.png"
                alt="Basebase"
                width="48"
                height="48"
                style="display:inline-block;height:48px;width:48px;border-radius:12px;"
              />
            </td>
          </tr>
          <tr>
            <td style="padding:24px 36px 0;text-align:center;">
              <h1 style="margin:0 0 12px;font-size:24px;line-height:1.3;color:#111;font-weight:700;">{name}, you&apos;re off the waitlist!</h1>
              <p style="margin:0;color:#6b7280;font-size:15px;line-height:1.6;">Sign up for Basebase to get started.</p>
            </td>
          </tr>
          <tr>
            <td style="padding:28px 36px 0;text-align:center;">
              <a href="{invite_url}" style="display:inline-block;background:#FF9F1C;color:#111111;text-decoration:none;font-size:16px;font-weight:600;padding:14px 32px;border-radius:10px;">Sign Up</a>
            </td>
          </tr>
          <tr>
            <td style="padding:32px 36px 0;">
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f9fafb;border:1px solid #f3f4f6;border-radius:10px;">
                <tr>
                  <td style="padding:20px 24px;">
                    <p style="margin:0 0 12px;font-size:14px;font-weight:600;color:#111;">One AI for your whole team</p>
                    <p style="margin:0;color:#6b7280;font-size:13px;line-height:1.6;">Basebase lives in your Slack workspace. Ask it anything &mdash; deal updates, meeting prep, customer research &mdash; and it answers right in the thread. When one person learns something, the whole team benefits.</p>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
          <tr>
            <td style="padding:28px 36px;text-align:center;">
              <p style="margin:0;color:#9ca3af;font-size:12px;line-height:1.6;">You received this email because you signed up for the Basebase waitlist.<br/>If this wasn&apos;t expected, you can safely ignore it.</p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
    """

    text_content: str = f"""
{name}, you're invited to use Basebase in your Slack workspace.

Sign up for Basebase to get started.

Basebase lives in your Slack workspace. Ask it anything -- deal updates, meeting prep, customer research -- and it answers right in the thread. When one person learns something, the whole team benefits.

Get started: {invite_url}

Questions? Just reply to this email.

-- The Basebase Team
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
                    "from": settings.EMAIL_FROM or "Basebase <hello@basebase.com>",
                    "to": [to_email],
                    "subject": "You're off the Basebase waitlist!",
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
    org_logo_url: Optional[str] = None,
    inviter_avatar_url: Optional[str] = None,
) -> bool:
    """
    Send an invitation email to join an organization.

    Args:
        to_email: Recipient email address
        org_name: Name of the organization they're being invited to
        invited_by_name: Name of the person who sent the invite
        org_logo_url: URL of the organization's logo (passed to frontend via query params)
        inviter_avatar_url: URL of the inviter's avatar (passed to frontend via query params)

    Returns:
        True if email sent successfully, False otherwise
    """
    from urllib.parse import quote

    if not settings.RESEND_API_KEY:
        print(f"[Email] RESEND_API_KEY not set, skipping org invite to {to_email}")
        return False

    params: list[str] = [f"invite=1", f"org_name={quote(org_name)}", f"email={quote(to_email)}"]
    if org_logo_url:
        params.append(f"org_logo={quote(org_logo_url)}")
    if invited_by_name:
        params.append(f"inviter_name={quote(invited_by_name)}")
    if inviter_avatar_url:
        params.append(f"inviter_avatar={quote(inviter_avatar_url)}")
    invite_url: str = f"{settings.FRONTEND_URL}?{'&'.join(params)}"

    headline: str = (
        f"{invited_by_name} invited you to use Basebase in {org_name}&apos;s Slack workspace"
        if invited_by_name
        else f"You&apos;ve been invited to use Basebase in {org_name}&apos;s Slack workspace"
    )

    subject: str = (
        f"{invited_by_name} invited you to use Basebase"
        if invited_by_name
        else f"You're invited to use Basebase in {org_name}'s Slack"
    )

    html_content: str = f"""
<!DOCTYPE html>
<html lang="en" xmlns="http://www.w3.org/1999/xhtml">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{subject}</title>
</head>
<body style="margin:0;padding:0;background:#f8f9fa;color:#1a1a1a;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f8f9fa;padding:32px 12px;">
    <tr>
      <td align="center">
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:560px;background:#ffffff;border:1px solid #e5e7eb;border-radius:16px;overflow:hidden;">
          <tr>
            <td style="padding:40px 36px 0;text-align:center;">
              <img
                src="https://www.basebase.com/basebase_logo-512.png"
                alt="Basebase"
                width="48"
                height="48"
                style="display:inline-block;height:48px;width:48px;border-radius:12px;"
              />
            </td>
          </tr>
          <tr>
            <td style="padding:24px 36px 0;text-align:center;">
              <h1 style="margin:0 0 12px;font-size:24px;line-height:1.3;color:#111;font-weight:700;">{headline}</h1>
              <p style="margin:0;color:#6b7280;font-size:15px;line-height:1.6;">Sign up for Basebase to accept.</p>
            </td>
          </tr>
          <tr>
            <td style="padding:28px 36px 0;text-align:center;">
              <a href="{invite_url}" style="display:inline-block;background:#FF9F1C;color:#111111;text-decoration:none;font-size:16px;font-weight:600;padding:14px 32px;border-radius:10px;">Sign Up</a>
            </td>
          </tr>
          <tr>
            <td style="padding:32px 36px 0;">
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f9fafb;border:1px solid #f3f4f6;border-radius:10px;">
                <tr>
                  <td style="padding:20px 24px;">
                    <p style="margin:0 0 12px;font-size:14px;font-weight:600;color:#111;">One AI for your whole team</p>
                    <p style="margin:0;color:#6b7280;font-size:13px;line-height:1.6;">Basebase lives in your Slack workspace. Ask it anything &mdash; deal updates, meeting prep, customer research &mdash; and it answers right in the thread. When one person learns something, the whole team benefits.</p>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
          <tr>
            <td style="padding:28px 36px;text-align:center;">
              <p style="margin:0;color:#9ca3af;font-size:12px;line-height:1.6;">You received this email because someone at {org_name} invited you to use Basebase.<br/>If this wasn&apos;t expected, you can safely ignore it.</p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
    """

    headline_text: str = (
        f"{invited_by_name} invited you to use Basebase in {org_name}'s Slack workspace."
        if invited_by_name
        else f"You've been invited to use Basebase in {org_name}'s Slack workspace."
    )

    text_content: str = f"""
{headline_text}

Sign up for Basebase to accept.

Basebase lives in your Slack workspace. Ask it anything -- deal updates, meeting prep, customer research -- and it answers right in the thread. When one person learns something, the whole team benefits.

Get started: {invite_url}

Questions? Just reply to this email.

-- The Basebase Team
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
                    "from": settings.EMAIL_FROM or "Basebase <hello@basebase.com>",
                    "to": [to_email],
                    "subject": subject,
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
