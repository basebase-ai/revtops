"""Quick script to test Resend email sending."""
import asyncio
import httpx
from config import settings


async def send_test_email(to_email: str) -> None:
    """Send a test email to verify Resend is working."""
    if not settings.RESEND_API_KEY:
        print("ERROR: RESEND_API_KEY not set in environment")
        return

    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {settings.RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "from": "Revtops <support@revtops.com>",
                "to": [to_email],
                "subject": "Test email from Revtops",
                "html": "<h1>It works!</h1><p>Your Resend integration is configured correctly.</p>",
                "text": "It works! Your Resend integration is configured correctly.",
            },
            timeout=10.0,
        )

        print(f"Status: {response.status_code}")
        print(f"Response: {response.json()}")


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python test_email.py <your-email@example.com>")
        sys.exit(1)
    
    to_email = sys.argv[1]
    print(f"Sending test email to {to_email}...")
    asyncio.run(send_test_email(to_email))
